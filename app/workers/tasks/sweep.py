"""
Reconciliation sweep.

Periodic task that finds stuck webhook receipts and re-enqueues them.

Why this exists:
    A Celery task can get "lost" — the broker accepted it but the
    worker crashed, or the message was dropped, or the worker never
    picked it up. The receipt sits in 'pending' forever. Without a
    sweep, these would require manual intervention.

What this task does:
    1. Query for receipts in 'pending' older than the threshold.
    2. For each one, increment sweep_attempts.
    3. Re-enqueue the original process_webhook_task.
    4. If sweep_attempts >= max_sweep_attempts, move to DLQ.

Safety:
    The re-enqueued task checks the receipt's status before processing,
    so re-running an already-processed receipt is a no-op. This makes
    the sweep idempotent — safe to run concurrently, safe to double-run.

Schedule:
    Configured in celery_app.conf.beat_schedule. Default: every 5 minutes.

Event loop:
    Uses the shared worker-process loop from app.workers.async_runtime,
    NOT a private loop of its own. This file previously kept its own
    module-level _loop, which caused "attached to a different loop"
    crashes whenever this task's connection checkout collided with
    process_webhook_task's — both modules shared one SQLAlchemy engine
    and connection pool, but each ran on its own separate event loop.
    See bugs.md, Bug 3.
"""

from celery import Task
from celery.exceptions import Retry
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import record_webhook_failure
from app.models.db.enums import ErrorCategory
from app.repositories.unit_of_work import UnitOfWork
from app.workers.celery_app import celery_app
from app.workers.async_runtime import run_async as _run_async


logger = get_logger(__name__)


# ============================================
# TASK
# ============================================
class ReconciliationSweepTask(Task):
    """Custom Task subclass for the sweep."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Retry is Celery control flow, not a failure.
        if isinstance(exc, Retry):
            return
        logger.error(
            f"Reconciliation sweep failed: task_id={task_id} exc={exc!r}"
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)


@celery_app.task(
    bind=True,
    base=ReconciliationSweepTask,
    name="app.workers.tasks.sweep.reconciliation_sweep",
    # No retries: the next scheduled run in 5 minutes covers us.
    max_retries=0,
)
def reconciliation_sweep(self) -> dict:
    """
    Find stuck pending receipts and re-enqueue them.
    """
    logger.info("Reconciliation sweep started")
    try:
        return _run_async(_sweep())
    except Exception as exc:
        logger.critical(f"Sweep crashed: {exc!r}", exc_info=True)
        raise


async def _sweep() -> dict:
    from app.workers.tasks.process_webhook import process_webhook_task

    stuck_threshold_seconds = settings.SWEEP_STUCK_THRESHOLD_SECONDS
    max_sweep_attempts = settings.MAX_SWEEP_ATTEMPTS
    found = 0
    requeued = 0
    dead_lettered = 0

    async with UnitOfWork.session_scope() as uow:
        # ---- 1. Find stuck receipts ----
        stuck = await uow.receipts.get_stuck_pending_receipts(
            older_than_seconds=stuck_threshold_seconds,
            limit=100,
        )
        found = len(stuck)

        if found == 0:
            logger.info("Sweep: no stuck receipts")
            return {"found": 0, "requeued": 0, "dead_lettered": 0}

        logger.warning(f"Sweep: found {found} stuck receipts")

        # ---- 2. Process each receipt ----
        for receipt in stuck:
            # Check if this receipt has already exhausted its sweep budget.
            # If so, move to DLQ instead of re-enqueueing.
            if receipt.sweep_attempts >= max_sweep_attempts:
                logger.error(
                    f"Sweep exhausted for receipt {receipt.id} "
                    f"({receipt.sweep_attempts}/{max_sweep_attempts})"
                )
                await _move_to_dlq_for_sweep(
                    uow, receipt, max_sweep_attempts
                )
                dead_lettered += 1
                continue

            await uow.receipts.increment_sweep_attempt(receipt.id)
            await uow.commit()

            process_webhook_task.delay(str(receipt.id))
            requeued += 1

            logger.info(
                f"Sweep requeued receipt {receipt.id} "
                f"(attempt {receipt.sweep_attempts + 1}/{max_sweep_attempts})"
            )

    logger.info(
        f"Sweep complete: found={found} requeued={requeued} "
        f"dead_lettered={dead_lettered}"
    )

    return {
        "found": found,
        "requeued": requeued,
        "dead_lettered": dead_lettered,
    }


async def _move_to_dlq_for_sweep(
    uow: UnitOfWork,
    receipt,
    max_sweep_attempts: int,
) -> None:
    error_message = (
        f"Sweep attempts exhausted "
        f"({receipt.sweep_attempts}/{max_sweep_attempts}). "
        f"Receipt was pending for longer than the sweep could recover."
    )

    # Mark the receipt dead_lettered
    await uow.receipts.mark_dead_lettered(
        receipt.id,
        error_message=error_message,
        error_category=ErrorCategory.PERMANENT,
    )

    # Create DLQ entry
    await uow.dead_letter.create(
        webhook_receipt_id=receipt.id,
        provider=receipt.provider,
        event_type=receipt.event_type,
        payload=receipt.payload,
        error_message=error_message,
        error_category=ErrorCategory.PERMANENT,
        celery_retries_exhausted=receipt.celery_retry_count,
        sweep_attempts_exhausted=receipt.sweep_attempts,
    )

    await uow.commit()

    # Record metric
    try:
        record_webhook_failure(
            provider=receipt.provider.value,
            event_type=receipt.event_type,
            error_category=ErrorCategory.PERMANENT.value,
            duration_seconds=0.0,
        )
    except Exception as exc:
        logger.warning(f"Metric recording failed in sweep DLQ: {exc}")