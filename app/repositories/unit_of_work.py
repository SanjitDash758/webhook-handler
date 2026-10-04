"""
Unit of Work pattern.

Groups repository operations into a single atomic transaction.

Why this exists:
- A single business operation may write to multiple tables
  (e.g., mark a receipt dead_lettered AND insert a DLQ entry).
- Those writes must succeed or fail TOGETHER.
- Sharing one session = one transaction = atomicity.

"""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.repositories.webhook_receipt_repo import WebhookReceiptRepository
from app.repositories.dead_letter_repo import DeadLetterRepository

logger = get_logger(__name__)

class UnitOfWork:
    def __init__(self, session: AsyncSession):
        self._session = session

        # Lazy-initialized repositories — created only if accessed.
        # Saves a tiny bit of memory when a caller only needs one repo.
        self._receipts: Optional[WebhookReceiptRepository] = None
        self._dead_letter: Optional[DeadLetterRepository] = None

    # ============================================
    # REPOSITORY ACCESSORS
    # ============================================
    @property
    def receipts(self) -> WebhookReceiptRepository:
        if self._receipts is None:
            self._receipts = WebhookReceiptRepository(self._session)
        return self._receipts

    @property
    def dead_letter(self) -> DeadLetterRepository:
        if self._dead_letter is None:
            self._dead_letter = DeadLetterRepository(self._session)
        return self._dead_letter

    # ============================================
    # SESSION ACCESS
    # ============================================
    @property
    def session(self) -> AsyncSession:
        return self._session

    # ============================================
    # TRANSACTION CONTROL
    # ============================================
    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # ============================================
    # CONTEXT MANAGERS
    # ============================================
    @classmethod
    @asynccontextmanager
    async def session_scope(cls) -> AsyncIterator["UnitOfWork"]:
        """
        Async context manager for non-FastAPI callers (Celery tasks, CLI scripts).

        Guarantees:
        - A fresh session per scope.
        - Commit on successful exit.
        - Rollback on exception.
        - Session always closed.
        """
        session = AsyncSessionLocal()
        uow = cls(session)
        try:
            yield uow
            await uow.commit()
        except Exception:
            await uow.rollback()
            raise
        finally:
            await session.close()


# ============================================
# FASTAPI DEPENDENCY
# ============================================
async def get_uow() -> AsyncIterator[UnitOfWork]:
    session = AsyncSessionLocal()
    uow = UnitOfWork(session)
    try:
        yield uow
    finally:
        await session.close()