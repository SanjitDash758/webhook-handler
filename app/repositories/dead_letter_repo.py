"""
Repository for the dead_letter_queue table.

This is the ONLY module that should query or mutate
the dead_letter_queue table.

Characteristics:
- Write-once: entries are inserted when a receipt permanently fails.
- Ops-facing: read-heavy, low volume, moderate filtering.
- Immutable except for: resolved, resolved_at, resolution_note, replayed_count.

Conventions match WebhookReceiptRepository:
- No HTTP, no Redis, no Celery knowledge.
- No business rules (e.g., "should we replay?"). Just CRUD.
- Session is injected; this class does not own transactions.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import DeadLetterQueue, ProviderType, ErrorCategory


class DeadLetterRepository:
    """DB access for dead_letter_queue."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ============================================
    # CREATE
    # ============================================
    async def create(
        self,
        *,
        webhook_receipt_id: UUID,
        provider: ProviderType,
        event_type: str,
        payload: dict,
        error_message: str,
        error_category: ErrorCategory,
        celery_retries_exhausted: int,
        sweep_attempts_exhausted: int,
    ) -> DeadLetterQueue:
        entry = DeadLetterQueue(
            webhook_receipt_id=webhook_receipt_id,
            provider=provider,
            event_type=event_type,
            payload=payload,
            error_message=error_message,
            error_category=error_category,
            celery_retries_exhausted=celery_retries_exhausted,
            sweep_attempts_exhausted=sweep_attempts_exhausted,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    # ============================================
    # READ
    # ============================================
    async def get_by_id(self, dlq_id: UUID) -> Optional[DeadLetterQueue]:
        """Fetch by primary key."""
        result = await self._session.execute(
            select(DeadLetterQueue).where(DeadLetterQueue.id == dlq_id)
        )
        return result.scalar_one_or_none()

    async def get_by_receipt_id(
        self, webhook_receipt_id: UUID
    ) -> Optional[DeadLetterQueue]:
        result = await self._session.execute(
            select(DeadLetterQueue).where(
                DeadLetterQueue.webhook_receipt_id == webhook_receipt_id
            )
        )
        return result.scalar_one_or_none()

    async def list_unresolved(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        provider: Optional[ProviderType] = None,
    ) -> list[DeadLetterQueue]:
        stmt = (
            select(DeadLetterQueue)
            .where(DeadLetterQueue.resolved.is_(False))
            .order_by(DeadLetterQueue.failed_at.asc())
            .limit(limit)
            .offset(offset)
        )
        if provider is not None:
            stmt = stmt.where(DeadLetterQueue.provider == provider)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_unresolved(
        self, provider: Optional[ProviderType] = None
    ) -> int:
        """Count unresolved entries (for dashboards and pagination)."""
        stmt = select(func.count(DeadLetterQueue.id)).where(
            DeadLetterQueue.resolved.is_(False)
        )
        if provider is not None:
            stmt = stmt.where(DeadLetterQueue.provider == provider)

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ============================================
    # RESOLUTION
    # ============================================
    async def mark_resolved(
        self,
        dlq_id: UUID,
        *,
        resolution_note: Optional[str] = None,
    ) -> Optional[DeadLetterQueue]:
        entry = await self.get_by_id(dlq_id)
        if entry is None:
            return None
        if not entry.resolved:
            entry.resolved = True
            entry.resolved_at = datetime.now(timezone.utc)
            entry.resolution_note = resolution_note
        return entry

    # ============================================
    # REPLAY TRACKING
    # ============================================
    async def try_increment_replay_count(
        self, dlq_id: UUID, max_allowed: int
    ) -> Optional[int]:
   
        from sqlalchemy import update, and_

        result = await self._session.execute(
            update(DeadLetterQueue)
            .where(
                and_(
                    DeadLetterQueue.id == dlq_id,
                    DeadLetterQueue.replayed_count < max_allowed,
                    DeadLetterQueue.resolved.is_(False),
                )   
            )
            .values(replayed_count=DeadLetterQueue.replayed_count + 1)
            .returning(DeadLetterQueue.replayed_count)
        )
        row = result.fetchone()
        return row[0] if row else None