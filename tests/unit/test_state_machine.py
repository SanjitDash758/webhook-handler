import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import IntegrityError
from app.services.webhook_service import WebhookIngestionService, IngestionResult
from app.models.db.enums import ProviderType, WebhookStatus
from app.core.exceptions import DuplicateEventConflict
from app.core.pipeline_stages import PipelineStage


@pytest.fixture
def mock_uow():
    """Fixture providing a mock UnitOfWork."""
    uow = MagicMock()
    uow.receipts.create = AsyncMock()
    uow.receipts.get_by_idempotency_key = AsyncMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    return uow


@pytest.fixture
def mock_idempotency():
    """Fixture providing a mock IdempotencyService."""
    idem = MagicMock()
    idem.check = AsyncMock()
    idem.store = AsyncMock()
    return idem


@pytest.fixture
def mock_pipeline():
    """Fixture providing a mock PipelinePublisher."""
    pipe = MagicMock()
    pipe.publish = AsyncMock()
    return pipe


@pytest.fixture
def webhook_service(mock_uow, mock_idempotency, mock_pipeline):
    """Fixture initializing WebhookIngestionService with dependencies."""
    return WebhookIngestionService(
        uow=mock_uow,
        idempotency=mock_idempotency,
        pipeline=mock_pipeline,
    )


# ============================================================================
# NEW INGESTION TESTS
# ============================================================================

@pytest.mark.asyncio
@patch("app.workers.tasks.process_webhook.process_webhook_task.delay")
async def test_ingest_new_webhook_success(
    mock_celery_delay, webhook_service, mock_uow, mock_idempotency, mock_pipeline
):
    """Test standard flow for ingesting a brand new webhook."""
    receipt_id = uuid.uuid4()
    mock_idempotency.check.return_value = None  # Cache miss

    fake_receipt = MagicMock()
    fake_receipt.id = receipt_id
    mock_uow.receipts.create.return_value = fake_receipt

    result = await webhook_service.ingest(
        provider=ProviderType.STRIPE,
        event_type="payment_intent.succeeded",
        idempotency_key="evt_123",
        payload={"amount": 1000},
        verified=True,
    )

    # Assertions
    assert isinstance(result, IngestionResult)
    assert result.is_new is True
    assert result.status == WebhookStatus.PENDING
    assert result.status_code == 202
    assert result.receipt_id == receipt_id

    # Pipeline stages published
    assert mock_pipeline.publish.call_count == 2
    mock_pipeline.publish.assert_any_call(
        receipt_id=receipt_id, provider="stripe", stage=PipelineStage.RECEIVED
    )
    mock_pipeline.publish.assert_any_call(
        receipt_id=receipt_id, provider="stripe", stage=PipelineStage.QUEUED
    )

    # Celery task dispatched
    mock_celery_delay.assert_called_once_with(str(receipt_id))


# ============================================================================
# REDIS FAST PATH (DUPLICATE) TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_ingest_redis_duplicate_hit(webhook_service, mock_idempotency):
    """Test duplicate detection hit via Redis cache returns cached response."""
    cached_id = str(uuid.uuid4())
    mock_idempotency.check.return_value = {
        "receipt_id": cached_id,
        "event_type": "payment_intent.succeeded",
        "status": WebhookStatus.PROCESSING.value,
        "response": {"status": "success"},
    }

    result = await webhook_service.ingest(
        provider=ProviderType.STRIPE,
        event_type="payment_intent.succeeded",
        idempotency_key="evt_123",
        payload={"amount": 1000},
        verified=True,
    )

    assert result.is_new is False
    assert result.status_code == 200
    assert result.receipt_id == uuid.UUID(cached_id)
    assert result.status == WebhookStatus.PROCESSING
    assert result.response_payload == {"status": "success"}


@pytest.mark.asyncio
async def test_ingest_redis_conflict_event_type_mismatch(webhook_service, mock_idempotency):
    """Test error raised when same key is used for a different event type in Redis."""
    mock_idempotency.check.return_value = {
        "receipt_id": str(uuid.uuid4()),
        "event_type": "payment_intent.failed",  # Mismatch
        "status": WebhookStatus.PROCESSING.value,
    }

    with pytest.raises(DuplicateEventConflict, match="Idempotency key already used"):
        await webhook_service.ingest(
            provider=ProviderType.STRIPE,
            event_type="payment_intent.succeeded",
            idempotency_key="evt_123",
            payload={"amount": 1000},
            verified=True,
        )


# ============================================================================
# POSTGRES DUPLICATE & FALLBACK TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_ingest_postgres_duplicate_fallback(
    webhook_service, mock_uow, mock_idempotency
):
    """Test duplicate caught by Postgres unique constraint backfills Redis cache."""
    receipt_id = uuid.uuid4()
    mock_idempotency.check.return_value = None  # Cache miss

    # DB IntegrityError on create
    mock_uow.receipts.create.side_effect = IntegrityError(None, None, Exception())

    existing_receipt = MagicMock()
    existing_receipt.id = receipt_id
    existing_receipt.event_type = "payment_intent.succeeded"
    existing_receipt.status = WebhookStatus.PENDING
    existing_receipt.response_snapshot = {"status": "processing"}
    mock_uow.receipts.get_by_idempotency_key.return_value = existing_receipt

    result = await webhook_service.ingest(
        provider=ProviderType.STRIPE,
        event_type="payment_intent.succeeded",
        idempotency_key="evt_123",
        payload={"amount": 1000},
        verified=True,
    )

    assert result.is_new is False
    assert result.receipt_id == receipt_id
    mock_uow.rollback.assert_called_once()

    # Verify Redis backfill took place with string enum value
    mock_idempotency.store.assert_called_once_with(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id=str(receipt_id),
        event_type="payment_intent.succeeded",
        status=WebhookStatus.PENDING.value,
        response={"status": "processing"},
    )


@pytest.mark.asyncio
async def test_ingest_postgres_duplicate_type_mismatch(
    webhook_service, mock_uow, mock_idempotency
):
    """Test error raised when same key exists in Postgres with a different event type."""
    mock_idempotency.check.return_value = None
    mock_uow.receipts.create.side_effect = IntegrityError(None, None, Exception())

    existing_receipt = MagicMock()
    existing_receipt.event_type = "charge.dispute.created"  # Mismatch
    mock_uow.receipts.get_by_idempotency_key.return_value = existing_receipt

    with pytest.raises(DuplicateEventConflict, match="Idempotency key already used"):
        await webhook_service.ingest(
            provider=ProviderType.STRIPE,
            event_type="payment_intent.succeeded",
            idempotency_key="evt_123",
            payload={"amount": 1000},
            verified=True,
        )