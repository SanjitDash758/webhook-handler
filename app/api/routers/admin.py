"""
Security:
    All routes require an X-API-Key header matching settings.ADMIN_API_KEY.
    The key is compared with hmac.compare_digest (timing-safe).

"""

from __future__ import annotations
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.logging import get_logger
from app.models.db.enums import ProviderType
from app.repositories.unit_of_work import UnitOfWork, get_uow
from app.services.idempotency_service import IdempotencyService
from app.api.dependencies.auth import require_admin_api_key

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Guardrail. After this many replays, an ops human must intervene.
# Enforced atomically in SQL by try_increment_replay_count().
MAX_REPLAY_ATTEMPTS = 3

# === List Unresolved DLQ entries === # 
@router.get(
    "/dlq",
    summary="List unresolved DLQ entries",
    dependencies=[Depends(require_admin_api_key)],
)
async def list_dlq(
    uow: UnitOfWork = Depends(get_uow),
    provider: Optional[ProviderType] = Query(
        default=None,
        description="Filter by provider (stripe, generic)",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    List unresolved DLQ entries, oldest first.

    Supports pagination and optional provider filter.
    """
    entries = await uow.dead_letter.list_unresolved(
        limit=limit,
        offset=offset,
        provider=provider,
    )
    total = await uow.dead_letter.count_unresolved(provider=provider)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": [
            {
                "id": str(e.id),
                "webhook_receipt_id": str(e.webhook_receipt_id),
                "provider": e.provider.value,
                "event_type": e.event_type,
                "error_message": e.error_message,
                "error_category": e.error_category.value,
                "failed_at": e.failed_at.isoformat(),
                "celery_retries_exhausted": e.celery_retries_exhausted,
                "sweep_attempts_exhausted": e.sweep_attempts_exhausted,
                "replayed_count": e.replayed_count,
            }
            for e in entries
        ],
    }


# === REPLAY - Re-enqueue a failed webhook. === #

@router.post(
    "/dlq/{dlq_id}/replay",
    summary="Re-enqueue a failed webhook",
    dependencies=[Depends(require_admin_api_key)],
)
async def replay_dlq_entry(
    dlq_id: UUID,
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    # ---- 1. Load entry (for 404 and resolved checks) ---- 
    entry = await uow.dead_letter.get_by_id(dlq_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="DLQ entry not found")

    # ---- 2. Already resolved? ----
    if entry.resolved:
        raise HTTPException(
            status_code=400,
            detail="DLQ entry is already resolved",
        )

    # ---- 3. Load the underlying receipt ----
    receipt = await uow.receipts.get_by_id(entry.webhook_receipt_id)
    if receipt is None:
        # Shouldn't happen: FK constraints prevent orphaned DLQ entries.
        logger.error(
            f"DLQ entry {dlq_id} references missing receipt "
            f"{entry.webhook_receipt_id}"
        )
        raise HTTPException(
            status_code=500,
            detail="Associated receipt missing — data corruption",
        )

    # ---- 4. Atomic increment with cap ----
    new_count = await uow.dead_letter.try_increment_replay_count(
        dlq_id, max_allowed=MAX_REPLAY_ATTEMPTS
    )
    if new_count is None:
        # Either the cap was already reached, or the row was resolved
        # between our earlier check and this UPDATE (race).
        raise HTTPException(
            status_code=400,
            detail=(
                f"Replay limit reached ({MAX_REPLAY_ATTEMPTS}). "
                "Investigate the root cause before replaying again."
            ),
        )

    # ---- 5. Reset the receipt ----
    await uow.receipts.reset_for_replay(receipt.id)

    # ---- 6. Commit both updates atomically ----
    await uow.commit()

    # ---- 7. Invalidate Redis cache (best-effort) ----
    try:
        idem = IdempotencyService()
        await idem.invalidate(
            provider=receipt.provider.value,
            idempotency_key=receipt.idempotency_key,
        )
    except Exception as exc:
        logger.warning(f"Idempotency cache invalidate failed: {exc}")

    # ---- 8. Enqueue Celery task ----
    # Imported here to avoid a circular import at module load.
    from app.workers.tasks.process_webhook import process_webhook_task
    process_webhook_task.delay(str(receipt.id))

    logger.warning(
        f"Admin replay: dlq_id={dlq_id} receipt_id={receipt.id} "
        f"attempt={new_count}/{MAX_REPLAY_ATTEMPTS}"
    )

    return {
        "status": "replayed",
        "dlq_id": str(dlq_id),
        "receipt_id": str(receipt.id),
        "replayed_count": new_count,
        "max_replays": MAX_REPLAY_ATTEMPTS,
    }


# === RESOLVE - Mark a DLQ entry resolved === # 
@router.post(
    "/dlq/{dlq_id}/resolve",
    summary="Mark a DLQ entry resolved",
    dependencies=[Depends(require_admin_api_key)],
)
async def resolve_dlq_entry(
    dlq_id: UUID,
    uow: UnitOfWork = Depends(get_uow),
    note: Optional[str] = Query(
        default=None,
        max_length=500,
        description="Optional resolution note for the audit trail",
    ),
) -> dict:
    entry = await uow.dead_letter.get_by_id(dlq_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="DLQ entry not found")

    if entry.resolved:
        raise HTTPException(status_code=400, detail="DLQ entry already resolved")

    await uow.dead_letter.mark_resolved(dlq_id, resolution_note=note)
    await uow.commit()

    logger.warning(
        f"Admin resolve: dlq_id={dlq_id} note={note!r}"
    )

    return {
        "status": "resolved",
        "dlq_id": str(dlq_id),
        "resolution_note": note,
    }