from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import WebhookReceipt, WebhookStatus
from app.models.db.enums import ErrorCategory


class WebhookReceiptRepository:
    """DB access for webhook_receipts."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ============================================
    # CREATE
    # ============================================
    async def create(
        self,
        *,
        provider: str,
        event_type: str,
        idempotency_key: str,
        payload: dict,
        verified: bool,
    ) -> WebhookReceipt:
        receipt = WebhookReceipt(
            provider=provider,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            verified=verified,
            status=WebhookStatus.PENDING,
        )
        self._session.add(receipt)
        await self._session.flush()
        return receipt

    # ============================================
    # READ
    # ============================================
    async def get_by_id(self, receipt_id: UUID) -> Optional[WebhookReceipt]:
       
        result = await self._session.execute(
            select(WebhookReceipt).where(WebhookReceipt.id == receipt_id)
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self,
        provider: str,
        idempotency_key: str,
    ) -> Optional[WebhookReceipt]:
        result = await self._session.execute(
            select(WebhookReceipt).where(
                WebhookReceipt.provider == provider,
                WebhookReceipt.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    # ============================================
    # STATE TRANSITIONS
    # ============================================
    async def mark_processing(
        self,
        receipt_id: UUID,
    ) -> Optional[WebhookReceipt]:
        """
        Move pending → processing. Idempotent: no-op if already processing.

        Sets processing_started_at on first entry.
        """
        receipt = await self.get_by_id(receipt_id)
        if receipt is None:
            return None
        if receipt.status == WebhookStatus.PENDING:
            receipt.status = WebhookStatus.PROCESSING
            receipt.processing_started_at = datetime.now(timezone.utc)
        return receipt

    async def mark_success(
        self,
        receipt_id: UUID,
        response_snapshot: dict,
    ) -> Optional[WebhookReceipt]:
        """Move to success. Stores response for later idempotent replay."""
        receipt = await self.get_by_id(receipt_id)
        if receipt is None:
            return None
        receipt.status = WebhookStatus.SUCCESS
        receipt.completed_at = datetime.now(timezone.utc)
        receipt.response_snapshot = response_snapshot
        receipt.last_error = None
        receipt.last_error_category = None
        return receipt

    async def mark_dead_lettered(
        self,
        receipt_id: UUID,
        error_message: str,
        error_category: ErrorCategory,
    ) -> Optional[WebhookReceipt]:
        """Move to dead_lettered. Stores the final error for forensics."""
        receipt = await self.get_by_id(receipt_id)
        if receipt is None:
            return None
        receipt.status = WebhookStatus.DEAD_LETTERED
        receipt.completed_at = datetime.now(timezone.utc)
        receipt.last_error = error_message
        receipt.last_error_category = error_category
        return receipt

    async def reset_for_replay(self, receipt_id: UUID) -> Optional[WebhookReceipt]:
        """
        Admin replay: dead_lettered → pending, clear retry history.

        Called only from the admin API after verifying replay is allowed.
        """
        receipt = await self.get_by_id(receipt_id)
        if receipt is None:
            return None
        receipt.status = WebhookStatus.PENDING
        receipt.celery_retry_count = 0
        receipt.last_error = None
        receipt.last_error_category = None
        receipt.completed_at = None
        receipt.processing_started_at = None
        return receipt

    # ============================================
    # RETRY COUNTERS
    # ============================================
    async def increment_celery_retry(self, receipt_id: UUID) -> None:
        """Bump the Celery retry counter (called before re-raising for retry)."""
        await self._session.execute(
            update(WebhookReceipt)
            .where(WebhookReceipt.id == receipt_id)
            .values(celery_retry_count=WebhookReceipt.celery_retry_count + 1)
        )

    async def increment_sweep_attempt(self, receipt_id: UUID) -> None:
        """Bump the sweep attempt counter (called by reconciliation sweep)."""
        await self._session.execute(
            update(WebhookReceipt)
            .where(WebhookReceipt.id == receipt_id)
            .values(sweep_attempts=WebhookReceipt.sweep_attempts + 1)
        )
    # ============================================
    # SWEEP QUERIES
    # ============================================
    async def get_stuck_pending_receipts(
        self,
        *,
        older_than_seconds: int,
        limit: int = 100,
    ) -> list[WebhookReceipt]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)

        result = await self._session.execute(
            select(WebhookReceipt)
            .where(
                WebhookReceipt.status == WebhookStatus.PENDING,
                WebhookReceipt.created_at < cutoff,
            )
            .order_by(WebhookReceipt.created_at.asc())  # oldest first
            .limit(limit)
        )
        return list(result.scalars().all())
