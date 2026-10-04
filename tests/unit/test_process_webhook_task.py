import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.exceptions import (
    PermanentProcessingError,
    TransientProcessingError,
)
from app.models.db.enums import ErrorCategory, WebhookStatus
from app.workers.tasks.process_webhook import (
    _process,
    _refresh_idem_cache,
    _run_async,
    process_webhook_task,
)


@pytest.fixture(scope="session", autouse=True)
def set_asyncio_policy():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ============================================
# FIXTURES
# ============================================
@pytest.fixture
def receipt_id():
    return str(uuid.uuid4())


@pytest.fixture
def mock_receipt(receipt_id):
    receipt = MagicMock()
    receipt.id = uuid.UUID(receipt_id)
    receipt.status = WebhookStatus.PENDING

    mock_provider = MagicMock()
    mock_provider.value = "stripe"
    receipt.provider = mock_provider

    receipt.event_type = "payment_intent.succeeded"
    receipt.idempotency_key = "idem_12345"
    receipt.payload = {"amount": 1000}
    receipt.celery_retry_count = 0
    receipt.sweep_attempts = 0
    receipt.last_error = None
    receipt.last_error_category = None
    return receipt


@pytest.fixture
def mock_uow(mock_receipt):
    uow = AsyncMock()
    uow.receipts.get_by_id.return_value = mock_receipt
    uow.receipts.mark_processing = AsyncMock()
    uow.receipts.mark_success = AsyncMock()
    uow.receipts.mark_dead_lettered = AsyncMock()
    uow.receipts.increment_celery_retry = AsyncMock()
    uow.dead_letter.create = AsyncMock()
    uow.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__.return_value = uow
    cm.__aexit__.return_value = None

    with patch(
        "app.workers.tasks.process_webhook.UnitOfWork.session_scope",
        return_value=cm,
    ):
        yield uow


@pytest.fixture
def mock_task():
    task = MagicMock()
    task.request.retries = 0
    task.max_retries = 5
    task.retry = MagicMock(side_effect=Retry("Retry requested"))
    return task


# ============================================
# UNIT TESTS FOR _process
# ============================================
@pytest.mark.asyncio
async def test_process_receipt_not_found(mock_uow, mock_task, receipt_id):
    """Test when receipt ID does not exist in DB."""
    mock_uow.receipts.get_by_id.return_value = None

    await _process(receipt_id, mock_task)

    mock_uow.receipts.get_by_id.assert_called_once_with(uuid.UUID(receipt_id))
    mock_uow.receipts.mark_processing.assert_not_called()


@pytest.mark.asyncio
async def test_process_receipt_already_terminal(
    mock_uow, mock_receipt, mock_task, receipt_id
):
    """Test when receipt is already SUCCESS or DEAD_LETTERED."""
    mock_receipt.status = WebhookStatus.SUCCESS

    await _process(receipt_id, mock_task)

    mock_uow.receipts.mark_processing.assert_not_called()


@pytest.mark.asyncio
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
@patch("app.services.idempotency_service.IdempotencyService.update_response")
async def test_process_success_path(
    mock_idem_update,
    mock_pipeline,
    mock_processor,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test successful event processing path."""
    mock_processor.return_value = {"processed": True}

    await _process(receipt_id, mock_task)

    mock_uow.receipts.mark_processing.assert_called_once_with(mock_receipt.id)
    mock_processor.assert_called_once_with(mock_receipt)
    mock_uow.receipts.mark_success.assert_called_once_with(
        mock_receipt.id, response_snapshot={"processed": True}
    )
    assert mock_uow.commit.call_count == 2


@pytest.mark.asyncio
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook._refresh_idem_cache",
    new_callable=AsyncMock,
)
async def test_process_transient_error_triggers_retry(
    mock_idem,
    mock_pipeline,
    mock_processor,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test transient error triggers Celery retry with backoff."""
    mock_processor.side_effect = TransientProcessingError("API rate limit")

    with pytest.raises(Retry):
        await _process(receipt_id, mock_task)

    mock_uow.receipts.increment_celery_retry.assert_called_once_with(
        mock_receipt.id
    )
    mock_task.retry.assert_called_once()
    assert mock_task.retry.call_args[1]["countdown"] == 60


@pytest.mark.asyncio
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook._refresh_idem_cache",
    new_callable=AsyncMock,
)
async def test_process_transient_error_retries_exhausted_moves_to_dlq(
    mock_idem,
    mock_pipeline,
    mock_processor,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test transient error when max retries reached escalates to DLQ."""
    mock_task.request.retries = 5
    mock_processor.side_effect = TransientProcessingError("Persistent timeout")

    await _process(receipt_id, mock_task)

    mock_uow.receipts.mark_dead_lettered.assert_called_once_with(
        mock_receipt.id,
        error_message="Persistent timeout",
        error_category=ErrorCategory.TRANSIENT,
    )
    mock_uow.dead_letter.create.assert_called_once()
    mock_task.retry.assert_not_called()


@pytest.mark.asyncio
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook._refresh_idem_cache",
    new_callable=AsyncMock,
)
async def test_process_permanent_error_moves_to_dlq(
    mock_idem,
    mock_pipeline,
    mock_processor,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test permanent error skips retries and goes straight to DLQ."""
    mock_processor.side_effect = PermanentProcessingError("Invalid payload schema")

    await _process(receipt_id, mock_task)

    mock_uow.receipts.mark_dead_lettered.assert_called_once_with(
        mock_receipt.id,
        error_message="Invalid payload schema",
        error_category=ErrorCategory.PERMANENT,
    )
    mock_uow.dead_letter.create.assert_called_once()
    mock_task.retry.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "db_exc",
    [
        OperationalError("stmt", "params", "orig"),
        DBAPIError("stmt", "params", "orig"),
    ],
)
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
async def test_process_db_error_triggers_short_retry(
    mock_pipeline,
    mock_processor,
    db_exc,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test database error triggers short retry delay."""
    mock_processor.side_effect = db_exc

    with pytest.raises(Retry):
        await _process(receipt_id, mock_task)

    mock_task.retry.assert_called_once()
    assert mock_task.retry.call_args[1]["countdown"] == 30


@pytest.mark.asyncio
@patch(
    "app.workers.tasks.process_webhook.PaymentProcessor.process",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook.PipelinePublisher.publish",
    new_callable=AsyncMock,
)
@patch(
    "app.workers.tasks.process_webhook._refresh_idem_cache",
    new_callable=AsyncMock,
)
async def test_process_unknown_error_treated_as_transient(
    mock_idem,
    mock_pipeline,
    mock_processor,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Test unhandled exception is logged as critical and treated as transient error."""
    mock_processor.side_effect = ValueError("Unexpected code condition")

    with pytest.raises(Retry):
        await _process(receipt_id, mock_task)

    mock_task.retry.assert_called_once()


# ============================================
# EDGE CASES & EXCEPTION HANDLING
# ============================================
@pytest.mark.asyncio
@patch("app.services.idempotency_service.IdempotencyService.update_response")
async def test_refresh_idem_cache_handles_errors(mock_update_response, caplog):
    """Covers lines 296-306 (idempotency cache refresh failure branch)."""
    mock_update_response.side_effect = Exception("Redis cache error")

    mock_receipt = MagicMock()
    mock_receipt.idempotency_key = "key_123"

    mock_status = MagicMock()
    mock_status.value = "FAILED"

    await _refresh_idem_cache(mock_receipt, status=mock_status)

    mock_update_response.assert_called_once()
    assert "Idempotency cache refresh failed" in caplog.text


def test_run_async_executes_coroutine():
    """Covers lines 44-50 & 73-78 (_run_async helper execution)."""
    async def sample_coro():
        return 42

    result = _run_async(sample_coro())
    assert result == 42


@pytest.mark.asyncio
@patch("app.workers.tasks.process_webhook.PaymentProcessor")
@patch("app.workers.tasks.process_webhook.PipelinePublisher")
async def test_process_publisher_failure_handled(
    mock_publisher_cls,
    mock_processor_cls,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Covers lines 141, 148, 163 (PipelinePublisher error handling)."""
    mock_processor_instance = MagicMock()
    mock_processor_instance.process = AsyncMock(return_value={"processed": True})
    mock_processor_cls.return_value = mock_processor_instance

    mock_publisher_instance = MagicMock()
    mock_publisher_instance.publish = AsyncMock(
        side_effect=Exception("Kafka publish failed")
    )
    mock_publisher_cls.return_value = mock_publisher_instance

    with pytest.raises(Exception, match="Kafka publish failed"):
        await _process(receipt_id, mock_task)


@pytest.mark.asyncio
@patch("app.workers.tasks.process_webhook.PaymentProcessor")
@patch("app.workers.tasks.process_webhook._refresh_idem_cache", new_callable=AsyncMock)
async def test_process_dlq_creation_failure_handled(
    mock_idem,
    mock_processor_cls,
    mock_uow,
    mock_receipt,
    mock_task,
    receipt_id,
):
    """Covers lines 383-395 (DLQ creation fallback exception handling)."""
    mock_processor_instance = AsyncMock()
    mock_processor_instance.process.side_effect = PermanentProcessingError("Fatal error")
    mock_processor_cls.return_value = mock_processor_instance

    mock_uow.dead_letter.create.side_effect = Exception("DLQ DB insertion failed")

    with pytest.raises(Exception, match="DLQ DB insertion failed"):
        await _process(receipt_id, mock_task)

    mock_uow.dead_letter.create.assert_called_once()


@pytest.mark.asyncio
async def test_process_webhook_invalid_receipt_id_raises_error(mock_task):
    """Covers invalid inputs passed to UUID parsing in _process."""
    with pytest.raises(TypeError):
        await _process(None, mock_task)

    with pytest.raises(ValueError):
        await _process("not-a-valid-uuid", mock_task)


# ============================================
# CELERY TASK WRAPPER TESTS
# ============================================
@patch("app.workers.tasks.process_webhook._process")
@patch("app.workers.tasks.process_webhook._run_async")
def test_process_webhook_task_wrapper_success(
    mock_run_async, mock_process, receipt_id
):
    """Test Celery wrapper delegating _process to _run_async."""
    process_webhook_task(receipt_id)

    mock_run_async.assert_called_once()

    coro = mock_run_async.call_args[0][0]
    coro.close()

    mock_process.assert_called_once()
    assert mock_process.call_args[1]["receipt_id"] == receipt_id


@patch("app.workers.tasks.process_webhook._process")
@patch(
    "app.workers.tasks.process_webhook._run_async",
    side_effect=Retry("Celery retry"),
)
def test_process_webhook_task_wrapper_reraises_retry(
    mock_run_async, mock_process, receipt_id
):
    """Test Celery task wrapper lets Retry exception pass through cleanly."""
    with pytest.raises(Retry):
        process_webhook_task(receipt_id)

    coro = mock_run_async.call_args[0][0]
    coro.close()