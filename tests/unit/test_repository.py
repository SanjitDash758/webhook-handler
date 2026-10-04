from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db import WebhookReceipt, WebhookStatus
from app.models.db.enums import ErrorCategory, ProviderType
from app.repositories.webhook_receipt_repo import WebhookReceiptRepository

# Use SQLite in-memory for fast unit testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def async_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_by_idempotency_key_returns_record(async_session):
    repo = WebhookReceiptRepository(async_session)
    receipt = WebhookReceipt(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_100",
        event_type="payment.succeeded",
        payload={"amount": 1000},
        verified=True,
        status=WebhookStatus.PENDING,
    )
    async_session.add(receipt)
    await async_session.commit()

    result = await repo.get_by_idempotency_key(
        provider=ProviderType.STRIPE.value, idempotency_key="key_100"
    )

    assert result is not None
    assert result.provider == ProviderType.STRIPE.value
    assert result.idempotency_key == "key_100"


@pytest.mark.asyncio
async def test_get_by_idempotency_key_returns_none(async_session):
    repo = WebhookReceiptRepository(async_session)

    result = await repo.get_by_idempotency_key(
        provider=ProviderType.STRIPE.value, idempotency_key="non_existent_key"
    )

    assert result is None


@pytest.mark.asyncio
async def test_create_webhook_receipt(async_session):
    repo = WebhookReceiptRepository(async_session)

    receipt = await repo.create(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_200",
        event_type="invoice.paid",
        payload={"invoice_id": "inv_123"},
        verified=True,
    )

    assert receipt.id is not None
    assert receipt.provider == ProviderType.STRIPE.value
    assert receipt.idempotency_key == "key_200"
    assert receipt.status == WebhookStatus.PENDING

    stmt = select(WebhookReceipt).where(WebhookReceipt.idempotency_key == "key_200")
    db_record = (await async_session.execute(stmt)).scalar_one_or_none()
    assert db_record is not None


@pytest.mark.asyncio
async def test_mark_processing_and_mark_dead_lettered(async_session):
    repo = WebhookReceiptRepository(async_session)
    receipt = await repo.create(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_300",
        event_type="charge.failed",
        payload={"reason": "card_declined"},
        verified=True,
    )

    # Move to processing
    processing_receipt = await repo.mark_processing(receipt.id)
    assert processing_receipt is not None
    assert processing_receipt.status == WebhookStatus.PROCESSING
    assert processing_receipt.processing_started_at is not None

    # Move to dead_lettered
    dl_receipt = await repo.mark_dead_lettered(
        receipt_id=receipt.id,
        error_message="Card declined by bank",
        error_category=ErrorCategory.PERMANENT,
    )

    assert dl_receipt is not None
    assert dl_receipt.status == WebhookStatus.DEAD_LETTERED
    assert dl_receipt.last_error == "Card declined by bank"
    assert dl_receipt.last_error_category == ErrorCategory.PERMANENT


@pytest.mark.asyncio
async def test_mark_state_non_existent_id(async_session):
    repo = WebhookReceiptRepository(async_session)
    non_existent_id = uuid4()

    updated_receipt = await repo.mark_processing(non_existent_id)
    assert updated_receipt is None

    success_receipt = await repo.mark_success(non_existent_id, response_snapshot={})
    assert success_receipt is None


@pytest.mark.asyncio
async def test_mark_success_and_reset_for_replay(async_session):
    repo = WebhookReceiptRepository(async_session)
    receipt = await repo.create(
        provider=ProviderType.GENERIC.value,
        idempotency_key="key_400",
        event_type="order.created",
        payload={"order_id": 42},
        verified=True,
    )

    # Transition to success
    success_receipt = await repo.mark_success(
        receipt_id=receipt.id,
        response_snapshot={"status": "ok"},
    )
    assert success_receipt.status == WebhookStatus.SUCCESS
    assert success_receipt.response_snapshot == {"status": "ok"}
    assert success_receipt.completed_at is not None

    # Mark dead_lettered then reset for replay
    await repo.mark_dead_lettered(
        receipt.id, "Fatal error", ErrorCategory.PERMANENT
    )
    replayed_receipt = await repo.reset_for_replay(receipt.id)

    assert replayed_receipt.status == WebhookStatus.PENDING
    assert replayed_receipt.celery_retry_count == 0
    assert replayed_receipt.last_error is None


@pytest.mark.asyncio
async def test_get_stuck_pending_receipts(async_session):
    repo = WebhookReceiptRepository(async_session)
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=10)

    stale_receipt = WebhookReceipt(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_stale",
        event_type="order.created",
        payload={},
        verified=True,
        status=WebhookStatus.PENDING,
        created_at=stale_time,
    )
    fresh_receipt = WebhookReceipt(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_fresh",
        event_type="order.created",
        payload={},
        verified=True,
        status=WebhookStatus.PENDING,
        created_at=now,
    )

    async_session.add_all([stale_receipt, fresh_receipt])
    await async_session.commit()

    stuck_records = await repo.get_stuck_pending_receipts(older_than_seconds=300)

    retrieved_keys = [r.idempotency_key for r in stuck_records]
    assert "key_stale" in retrieved_keys
    assert "key_fresh" not in retrieved_keys


@pytest.mark.asyncio
async def test_increment_retry_counters(async_session):
    repo = WebhookReceiptRepository(async_session)
    receipt = await repo.create(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_retries",
        event_type="payment.attempt",
        payload={},
        verified=True,
    )

    await repo.increment_celery_retry(receipt.id)
    await repo.increment_sweep_attempt(receipt.id)

    updated_receipt = await repo.get_by_id(receipt.id)
    assert updated_receipt.celery_retry_count == 1
    assert updated_receipt.sweep_attempts == 1


@pytest.mark.asyncio
async def test_repository_edge_cases_and_non_existent_ids(async_session):
    repo = WebhookReceiptRepository(async_session)
    non_existent_id = uuid4()

    # Cover line 116 (reset_for_replay with non-existent ID)
    assert await repo.reset_for_replay(non_existent_id) is None
    
    # Cover line 103 (mark_dead_lettered with non-existent ID)
    assert await repo.mark_dead_lettered(non_existent_id, "err", ErrorCategory.PERMANENT) is None