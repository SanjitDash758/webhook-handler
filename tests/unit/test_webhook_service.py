"""
Unit tests for WebhookIngestionService.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from app.core.exceptions import DuplicateEventConflict
from app.models.db.enums import ProviderType, WebhookStatus
from app.services.webhook_service import WebhookIngestionService


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with repository dependencies matching WebhookIngestionService."""
    uow = AsyncMock()
    uow.receipts = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    return uow


@pytest.fixture
def mock_idempotency():
    """Mock IdempotencyService."""
    service = AsyncMock()
    service.check = AsyncMock(return_value=None)
    service.store = AsyncMock()
    return service


@pytest.fixture
def mock_pipeline():
    """Mock PipelinePublisher."""
    pipeline = AsyncMock()
    pipeline.publish = AsyncMock()
    return pipeline


@pytest.mark.asyncio
@patch("app.workers.tasks.process_webhook.process_webhook_task.delay")
async def test_ingest_new_event_success(
    mock_celery_delay, mock_uow, mock_idempotency, mock_pipeline
):
    """Test ingesting a new event creates a database entry and enqueues task."""
    mock_idempotency.check.return_value = None

    receipt_id = uuid.uuid4()
    mock_created_receipt = MagicMock(
        id=receipt_id,
        provider=ProviderType.STRIPE,
        status=WebhookStatus.PENDING,
    )
    mock_uow.receipts.create.return_value = mock_created_receipt

    service = WebhookIngestionService(
        uow=mock_uow,
        idempotency=mock_idempotency,
        pipeline=mock_pipeline,
    )
    payload = {"id": "evt_test123", "type": "charge.succeeded"}

    result = await service.ingest(
        provider=ProviderType.STRIPE,
        event_type="charge.succeeded",
        idempotency_key="evt_test123",
        payload=payload,
        verified=True,
    )

    assert result.is_new is True
    assert result.receipt_id == receipt_id
    assert result.status == WebhookStatus.PENDING

    mock_uow.receipts.create.assert_called_once_with(
        provider=ProviderType.STRIPE,
        event_type="charge.succeeded",
        idempotency_key="evt_test123",
        payload=payload,
        verified=True,
    )
    mock_uow.commit.assert_called_once()
    mock_celery_delay.assert_called_once_with(str(receipt_id))


@pytest.mark.asyncio
async def test_ingest_duplicate_event_returns_cached_payload(
    mock_uow, mock_idempotency, mock_pipeline
):
    """Test returning a cached payload when Redis idempotency check hits."""
    existing_receipt_id = uuid.uuid4()
    cached_response = {"status": "accepted", "id": "resp_123"}

    mock_idempotency.check.return_value = {
        "receipt_id": str(existing_receipt_id),
        "event_type": "payment.created",
        "status": WebhookStatus.PROCESSING.value,
        "response": cached_response,
    }

    service = WebhookIngestionService(
        uow=mock_uow,
        idempotency=mock_idempotency,
        pipeline=mock_pipeline,
    )
    payload = {"id": "evt_duplicate", "type": "payment.created"}

    result = await service.ingest(
        provider=ProviderType.STRIPE,
        event_type="payment.created",
        idempotency_key="evt_duplicate",
        payload=payload,
        verified=True,
    )

    assert result.is_new is False
    assert result.receipt_id == existing_receipt_id
    assert result.status == WebhookStatus.PROCESSING
    assert result.response_payload == cached_response
    mock_uow.receipts.create.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_duplicate_key_mismatched_payload_raises_conflict(
    mock_uow, mock_idempotency, mock_pipeline
):
    """Test raising DuplicateEventConflict when an idempotency key is reused for a different event type."""
    mock_idempotency.check.return_value = {
        "receipt_id": str(uuid.uuid4()),
        "event_type": "order.created",
        "status": WebhookStatus.PENDING.value,
    }

    service = WebhookIngestionService(
        uow=mock_uow,
        idempotency=mock_idempotency,
        pipeline=mock_pipeline,
    )
    mismatched_payload = {"id": "evt_123", "amount": 2000}

    with pytest.raises(DuplicateEventConflict):
        await service.ingest(
            provider=ProviderType.GENERIC,
            event_type="order.updated",
            idempotency_key="evt_123",
            payload=mismatched_payload,
            verified=False,
        )