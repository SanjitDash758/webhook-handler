# Bug Postmortems

Three real bugs found during development of the webhook-handler.

All three were found through **testing against real data**, not code review. All three involved subtle interactions between components that are individually correct but jointly wrong.

---

## Bug 1 — Idempotency Fast-Path Bypass

### Symptom

Sending a webhook with:

- `Idempotency-Key: abc`
- `X-Event-Type: payment.test`

...returns HTTP 202 (accepted).

Then sending:

- `Idempotency-Key: abc` (same key)
- `X-Event-Type: payment.other` (different event)

...returns HTTP 200 with the **first event's cached response**.

**Expected:** 409 Conflict.
**Observed:** 200 OK with wrong data.

### Impact

Silent data loss.

The second event was never processed. The caller received a successful response indicating their event was accepted. No error, no log, no DLQ entry. The event vanished.

In a payment context, this could mean:

- A customer's refund triggered two refund events with the same key
- The first was processed, the second was silently dropped
- The customer only received one refund — the second was assumed done

### Root Cause

The idempotency system has two layers:

1. **Redis fast path** — checks the cache first for speed
2. **PostgreSQL unique constraint** — the source of truth

The Redis cache stored:

```text
key:   idem:generic:abc
value: {"receipt_id": "...", "status": "pending", "response": null}
```

**It did not store the `event_type`.**

When the fast path found a cache hit, it had no way to compare the incoming event type against the cached one. So it returned the cached response — regardless of whether the incoming event was the same event.

The Postgres fallback path _did_ compare event types (`_handle_duplicate` in `webhook_service.py`). But that path only ran when the Redis path missed. On a cache hit, the fallback never executed.

**The two-layer system had asymmetric correctness.** The slow path was correct; the fast path was not.

### Fix

Store `event_type` in the cache, and compare on the fast path:

**Before:**

```python
value = json.dumps({
    "receipt_id": receipt_id,
    "status": status,
    "response": response,
})
```

**After:**

```python
value = json.dumps({
    "receipt_id": receipt_id,
    "event_type": event_type,   # ← added
    "status": status,
    "response": response,
})
```

And in the fast-path check:

```python
if cached is not None:
    cached_event_type = cached.get("event_type")

    if cached_event_type is None:
        # Old cache entry — fall through to Postgres
        pass
    elif cached_event_type != event_type:
        raise DuplicateEventConflict(...)
    else:
        return cached_response
```

### Lesson

**A cache is only correct if it stores everything needed to evaluate the hit.**

The fast path and the slow path must produce **the same decision** for the same input. If they don't, one of them is wrong.

This is a class of bug — not a one-off. The general principle:

> If you have a cache in front of a source of truth, the cache key + cached value must contain enough information to make the _same decision_ the source of truth would.

`idempotency_key` alone was not enough. `(idempotency_key, event_type)` was.

---

## Bug 2 — Replay Counter Race Condition

### Symptom

The DLQ replay endpoint enforces a cap:

```python
MAX_REPLAY_ATTEMPTS = 3
```

Under sequential testing, replays worked. But at some point, the DB showed:

```text
replayed_count = 4
```

...even though the cap was 3.

### Impact

The replay cap is a **safety mechanism**, not a rate limit.

Its purpose: prevent an ops person (or an automated script) from repeatedly re-running a broken webhook. Each replay re-triggers the underlying business logic. If the root cause isn't fixed, the replay fails again — and again.

**A cap that can be silently exceeded defeats its own purpose.**

Worse: this is a symptom of a broader class of bug — the **read-check-write pattern**. Any counter with a cap could have the same problem.

### Root Cause

The original code:

```python
entry = await uow.dead_letter.get_by_id(dlq_id)          # read
if entry.replayed_count >= MAX_REPLAY_ATTEMPTS:          # check
    raise HTTPException(...)
await uow.dead_letter.increment_replay_count(dlq_id)     # write
```

And `increment_replay_count` did:

```python
entry = await self.get_by_id(dlq_id)                     # second read
entry.replayed_count = entry.replayed_count + 1          # modify
```

**Two reads, one check, one write** — with no locking.

Timeline for two requests racing:

| Time | Request A  | Request B  | DB replayed_count |
| ---- | ---------- | ---------- | ----------------- |
| T1   | Read (2)   |            | 2                 |
| T2   |            | Read (2)   | 2                 |
| T3   | 2 >= 3? No |            | 2                 |
| T4   |            | 2 >= 3? No | 2                 |
| T5   | Write (3)  |            | 3                 |
| T6   |            | Write (3)  | 3                 |

**Both requests passed the check. Only one increment should have happened. The cap is silently bypassed.**

This is a TOCTOU (Time-of-Check-to-Time-of-Use) race.

**Note on the symptom number:** the timeline above, as a 2-request race, mathematically produces a final value of `3` (a lost update), not the `4` originally reported. The `4` was not independently re-derivable when this postmortem was revisited — see the Verification section below for what was actually re-confirmed with real concurrent traffic against the fixed code.

### Fix

Move the cap check **into the SQL UPDATE's WHERE clause**:

```python
async def try_increment_replay_count(
    self, dlq_id: UUID, max_allowed: int
) -> Optional[int]:
    result = await self._session.execute(
        update(DeadLetterQueue)
        .where(
            and_(
                DeadLetterQueue.id == dlq_id,
                DeadLetterQueue.resolved.is_(False),
                DeadLetterQueue.replayed_count < max_allowed,
            )
        )
        .values(replayed_count=DeadLetterQueue.replayed_count + 1)
        .returning(DeadLetterQueue.replayed_count)
    )
    row = result.fetchone()
    return int(row[0]) if row is not None else None
```

**Now the check and the write are one atomic operation.** The database guarantees:

- Either the row is updated (returning the new count)
- Or zero rows are affected (returning None — cap reached)

No two requests can both succeed past the cap.

The handler becomes:

```python
new_count = await uow.dead_letter.try_increment_replay_count(
    dlq_id, max_allowed=MAX_REPLAY_ATTEMPTS
)
if new_count is None:
    raise HTTPException(400, detail="Replay limit reached (3). Investigate the root cause before replaying again.")
```

### Verification — real 4-way concurrent replay against the fixed code

The original race could not be re-triggered, because the fix was already live in the code by the time this was retested. What _could_ be verified is whether the fix holds under genuine concurrency, not just sequential calls.

Four `POST /admin/dlq/{id}/replay` requests were fired concurrently (via parallel background jobs, not sequential curls) against a real DLQ entry:

```text
Response 1: {"status":"replayed", ..., "replayed_count":1, "max_replays":3}  STATUS:200
Response 2: {"status":"replayed", ..., "replayed_count":2, "max_replays":3}  STATUS:200
Response 3: {"status":"replayed", ..., "replayed_count":3, "max_replays":3}  STATUS:200
Response 4: {"detail":"Replay limit reached (3). Investigate the root cause before replaying again."}  STATUS:400
```

Final DB state: `replayed_count = 3`. Exactly 3 succeeded, the 4th was cleanly rejected, no lost updates, no over-cap. The three successful replays each independently re-enqueued `process_webhook_task` (three separate task IDs, same `receipt_id`), all cycling through the full retry schedule concurrently — this incidentally also verified the `_process()` terminal-status guard (see `dlq_evidence.md`, Replay Cap Enforcement section): when the first of the three reached its final attempt and moved the receipt back to `dead_lettered`, the other two independently detected the terminal status and no-op'd rather than creating duplicate DLQ rows.

**Honest conclusion:** this is strong evidence the atomic fix is correct under real concurrency. It is not a reproduction of the original defect — that would require reverting the fix, which wasn't done here. Both things are true, and only one of them was actually tested.

### Lesson

**Any counter with a cap must be incremented atomically in SQL.**

The pattern:

```python
# WRONG — race condition
if entity.count < CAP:
    entity.count += 1

# RIGHT — atomic
UPDATE ... SET count = count + 1 WHERE id = X AND count < CAP
```

This applies to:

- Rate limiters
- Retry counters
- Replay counters
- Claim counters
- Any "N times maximum" logic

**Rare ≠ safe.** Admin endpoints are still race-prone. Even if concurrency is unlikely, the code must be correct under it.

---

## Bug 3 — Event-Loop Collision Between Independent Task Modules

### Symptom

```text
RuntimeError: Task <Task pending name='Task-2' coro=<_sweep() running at
/app/app/workers/tasks/sweep.py:111> cb=[_run_until_complete_cb() at
/usr/local/lib/python3.12/asyncio/base_events.py:181]> got Future
<Future pending cb=[BaseProtocol._on_waiter_completed()]> attached to
a different loop
```

Every invocation of the reconciliation sweep crashed with this error, every 5 minutes, without exception. The same error — identical shape — also crashed `process_webhook_task` on its very first attempt, before any sweep-specific code was even involved.

### Impact

This directly contradicted a documented guarantee. `architecture.md` states the reconciliation sweep turns stuck receipts from "requires manual intervention" into "self-heals in ≤5 minutes." With this bug present, the sweep never completed a single run — it crashed on every tick. The documented self-healing behavior did not exist in the running system, despite reading correctly in the design doc and the code review that preceded testing.

### Root Cause

`process_webhook.py` and `sweep.py` each maintained their **own independent, module-level persistent event loop**:

```python
# duplicated in BOTH files, independently
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = Lock()

def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        with _loop_lock:
            if _loop is None or _loop.is_closed():
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
    return _loop
```

Both modules shared **one SQLAlchemy async engine** (`app/core/database.py`), created once at import time with a real connection pool (`pool_size=10, max_overflow=20`, default `AsyncAdaptedQueuePool` — connections persist and get reused, not `NullPool`).

An asyncpg connection is permanently bound to whichever event loop was active when it was created. Even under Celery's `--pool=solo` (single OS thread), "same thread" does not mean "same loop" — `process_webhook.py`'s loop and `sweep.py`'s loop were two distinct `asyncio.AbstractEventLoop` objects. The failure sequence:

1. `process_webhook_task` runs on Loop A, checks out a connection from the shared pool, returns it when done.
2. Five minutes later, `reconciliation_sweep` runs on Loop B — a different loop object, same thread. It checks out a connection from the _same_ pool — possibly the exact connection Loop A just returned.
3. asyncpg refuses: that connection belongs to Loop A, which isn't the loop currently executing.

A secondary symptom, `InternalClientError('got result for unknown protocol state 3')`, was not a separate bug — it was the connection's internal state becoming corrupted as a direct consequence of the cross-loop misuse.

### Fix

One shared event loop for the entire worker process, not one private loop per module:

```python
# app/workers/async_runtime.py — new file, single source of truth
import asyncio
from threading import Lock
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = Lock()

def get_worker_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        with _loop_lock:
            if _loop is None or _loop.is_closed():
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
    return _loop

def run_async(coro):
    return get_worker_loop().run_until_complete(coro)
```

Both `process_webhook.py` and `sweep.py` had their private loop-management code deleted entirely, replaced with:

```python
from app.workers.async_runtime import run_async as _run_async
```

Every connection the shared pool now hands out is always used on the same loop it was created on, regardless of which task module checks it out. `NullPool` (opening a fresh connection every time, never reusing one) would also have fixed this, but was not the chosen fix — the bug was architectural (two loops where there should be one), and treating the symptom at the pool-config level would have left the actual duplication (two independently-invented copies of tricky loop-management logic) in place for a third module to reintroduce later.

### Verification

Post-fix, the sweep ran cleanly on its 5-minute schedule continuously for roughly 3 hours, including through a system sleep/resume event (confirmed by one tick firing off-schedule at `00:38:31` instead of the expected `:35` boundary, then resuming normal alignment — Celery Beat's `PersistentScheduler` self-corrected after the clock gap rather than silently dropping the missed run). Across more than 25 sweep ticks during this window, all reported `Sweep: no stuck receipts` with zero crashes — including while 3 receipts were actively cycling through retries and, separately, while 3 concurrent replay-triggered task chains were in flight. The fix held under real, sustained, concurrent load, not just at rest.

### Lesson

**A shared resource pool assumes a single execution context unless told otherwise.** SQLAlchemy's async engine and connection pool don't know or care how many event loops exist in a process — they assume whichever loop checks out a connection is the one that'll use it and return it. The moment two independently-managed event loops exist in the same process and share one pool, that assumption breaks, and the failure mode (cross-loop connection reuse) is silent until a connection actually gets reused across the boundary — which may not happen on every run, making this the kind of bug that can pass light testing and only appear under sustained operation.

**Duplicated "tricky" code is a bug waiting to happen, not just untidy.** Two independent copies of the same persistent-event-loop pattern looked like harmless repetition during code review. It was actually the root cause — the duplication meant there were two loops instead of one, which is precisely what broke the shared pool's assumption.

---

## What All Three Bugs Have in Common

All three bugs share three properties:

1. **Each component was individually correct.** Redis worked. Postgres worked. The SQL UPDATE worked. Each module's event-loop management worked correctly in isolation. The bug was in the _interaction_.
2. **They only appeared under specific conditions.** Bug 1 required a cache hit. Bug 2 required concurrent requests. Bug 3 required two different task modules both touching the database during the same operational window — a single-task test would never trigger it. Happy-path, single-scenario testing would never reveal any of them.
3. **They were found through empirical testing, not code review.** Reading the code, all three looked fine. Running the code with real data and real concurrency exposed the flaws.

**The general principle:**

> Correct components don't guarantee a correct system. You must test the _interactions_, not just the parts.

All three bugs were found because specific edge cases were deliberately exercised:

- **Bug 1:** Sent the same key with a different event type, expecting 409. Got 200.
- **Bug 2:** Replayed the same DLQ entry four times concurrently, expecting three successes and one 400. The fix held — this time the test confirmed correctness rather than finding a defect.
- **Bug 3:** Ran the reconciliation sweep on its normal schedule while other retry activity was happening in the same process — not an isolated unit test of the sweep alone.

**The tests you run to verify normal operation will never find these bugs.** Only tests designed to break the system, or that run multiple real workflows concurrently, will.

---

## Testing Habits That Caught These

- **Test the fast path _and_ the slow path separately.** They must agree.
- **Test the same operation multiple times, rapidly.** Race conditions hide in sequential tests.
- **Compare against expectations, not against "does it work."** Bug 1 passed "does it work?" — the response was 200 OK. It failed "does the response mean what it should?"
- **Trust the numbers.** Bug 2 was investigated because `replayed_count = 4` didn't match `MAX = 3` — and re-verifying it honestly meant admitting the original number couldn't be reproduced, rather than forcing a match.
- **Don't test background/periodic tasks in isolation.** Bug 3 only existed because two different Celery task modules shared a resource while running on the same schedule in the same process — a standalone test of the sweep task alone, mocking everything else, would have passed cleanly and hidden the bug.

---

## Further Reading

- TOCTOU races: [Wikipedia — Time-of-check to time-of-use](https://en.wikipedia.org/wiki/Time-of-check_to_time-of-use)
- Atomic counters in SQL: use the `WHERE` clause, not application-level checks
- Idempotency semantics: [Stripe's idempotency documentation](https://stripe.com/docs/api/idempotent_requests)
