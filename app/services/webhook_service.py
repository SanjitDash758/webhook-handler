"""
Webhook ingestion service.

Coordinates: Redis idempotency → Postgres persistence → Celery enqueue.

This is THE entry point for all webhook ingestion.
Routers must call this; they must not orchestrate themselves.

Responsibility boundary:
- Knows: idempotency semantics, DLQ flow, receipt lifecycle
- Doesn't know: HTTP, signatures, Redis/DB clients, Celery specifics

Dependencies (injected):
- UnitOfWork (owns session + repositories)
- IdempotencyService (Redis fast path)
"""

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import DuplicateEventConflict
from app.core.logging import get_logger
from app.models.db.enums import ProviderType, WebhookStatus
from app.repositories.unit_of_work import UnitOfWork
from app.services.idempotency_service import IdempotencyService
from app.services.pipeline_publisher import PipelinePublisher 
from app.core.pipeline_stages import PipelineStage

logger = get_logger(__name__)


# ============================================
# RESULT OBJECT
# ============================================
@dataclass
class IngestionResult:
    """
    What happened during ingestion.

    Returned to the router, which maps it to an HTTP response.
    """
    receipt_id: UUID
    is_new: bool                    
    status: WebhookStatus          
    response_payload: Optional[dict[str, Any]] = None 

    @property
    def status_code(self) -> int:
        """HTTP status code to return."""
        if self.is_new:
            return 202   # Accepted — processing in background
        return 200       # OK — duplicate, cached response returned


# ============================================
# SERVICE
# ============================================
class WebhookIngestionService:
    def __init__(
        self,
        uow: UnitOfWork,
        idempotency: Optional[IdempotencyService] = None,
        pipeline: Optional[PipelinePublisher] = None,  # NEW
    ):
        self._uow = uow
        self._idempotency = idempotency or IdempotencyService()
        self._pipeline = pipeline or PipelinePublisher()  # NEW

    # ============================================
    # MAIN ENTRY POINT
    # ============================================
    async def ingest(
        self,
        *,
        provider: ProviderType,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        verified: bool,
    ) -> IngestionResult:
        provider_value = provider.value

        # ---- 1. Redis fast path ----
        cached = await self._idempotency.check(provider_value, idempotency_key)

        if cached is not None:
            cached_event_type = cached.get("event_type")

            if cached_event_type is None:
                # Old cache entry from before event_type was added.
                # Fall through to Postgres where the full check happens.
                logger.info(
                    f"Cache entry missing event_type; falling through to "
                    f"Postgres: key={idempotency_key}"
                )

            elif cached_event_type != event_type:
                # Same key, different event → conflict. Do NOT return cached.
                logger.warning(
                    f"Idempotency conflict (Redis): provider={provider_value} "
                    f"key={idempotency_key} cached={cached_event_type} "
                    f"incoming={event_type}"
                )
                raise DuplicateEventConflict(
                    f"Idempotency key already used for event_type "
                    f"'{cached_event_type}'; got '{event_type}'"
                )

            else:
                # Same event → legitimate duplicate. Return cached response.
                logger.info(
                    f"Idempotency hit (Redis): provider={provider_value} "
                    f"key={idempotency_key} receipt_id={cached['receipt_id']}"
                )
                return IngestionResult(
                    receipt_id=UUID(cached["receipt_id"]),
                    is_new=False,
                    status=WebhookStatus(cached["status"]),
                    response_payload=cached.get("response"),
                )

        # ---- 2. Postgres insert ----
        try:
            receipt = await self._uow.receipts.create(
                provider=provider,
                event_type=event_type,
                idempotency_key=idempotency_key,
                payload=payload,
                verified=verified,
            )
        except IntegrityError:
            # Unique constraint fired: (provider, idempotency_key) exists.
            await self._uow.rollback()
            return await self._handle_duplicate(
                provider_value=provider_value,
                idempotency_key=idempotency_key,
                incoming_event_type=event_type,
            )

        # ---- 3. Commit (source of truth) ----
        await self._uow.commit()

        # ---- 3b. Pipeline event: received -
        await self._pipeline.publish(
            receipt_id=receipt.id, provider=provider_value, stage=PipelineStage.RECEIVED
        )

        # ---- 4. Cache in Redis (best-effort, AFTER commit) ----
        await self._idempotency.store(
            provider=provider_value,
            idempotency_key=idempotency_key,
            receipt_id=str(receipt.id),
            event_type=event_type,
            status=WebhookStatus.PENDING.value,
        )

        # ---- 4b. Pipeline event: queued ----
        await self._pipeline.publish(
            receipt_id=receipt.id, provider=provider_value, stage=PipelineStage.QUEUED
        )

        # ---- 5. Enqueue Celery task (best-effort) ----
        from app.workers.tasks.process_webhook import process_webhook_task
        process_webhook_task.delay(str(receipt.id))

        logger.info(
            f"Ingested new webhook: provider={provider_value} "
            f"key={idempotency_key} receipt_id={receipt.id}"
        )

        return IngestionResult(
            receipt_id=receipt.id,
            is_new=True,
            status=WebhookStatus.PENDING,
        )

    # ============================================
    # DUPLICATE HANDLING
    # ============================================
    async def _handle_duplicate(
        self,
        *,
        provider_value: str,
        idempotency_key: str,
        incoming_event_type: str,
    ) -> IngestionResult:
        
        existing = await self._uow.receipts.get_by_idempotency_key(
            provider=provider_value,
            idempotency_key=idempotency_key,
        )

        if existing is None:
            # Raced with a delete, or something is very wrong.
            logger.error(
                f"IntegrityError but no existing row found: "
                f"provider={provider_value} key={idempotency_key}"
            )
            raise DuplicateEventConflict(
                "Duplicate detected but original receipt not found"
            )

        if existing.event_type != incoming_event_type:
            logger.warning(
                f"Idempotency key reused for different event: "
                f"provider={provider_value} key={idempotency_key} "
                f"existing_type={existing.event_type} "
                f"incoming_type={incoming_event_type}"
            )
            raise DuplicateEventConflict(
                f"Idempotency key already used for event_type "
                f"'{existing.event_type}'; got '{incoming_event_type}'"
            )

        # Genuine duplicate — return the cached response.
        logger.info(
            f"Duplicate detected (Postgres): provider={provider_value} "
            f"key={idempotency_key} receipt_id={existing.id}"
        )

        # Backfill Redis so the next duplicate hits the fast path.
        await self._idempotency.store(
            provider=provider_value,
            idempotency_key=idempotency_key,
            receipt_id=str(existing.id),
            event_type=existing.event_type,
            status=existing.status.value,
            response=existing.response_snapshot,
        )

        return IngestionResult(
            receipt_id=existing.id,
            is_new=False,
            status=existing.status,
            response_payload=existing.response_snapshot,
        )