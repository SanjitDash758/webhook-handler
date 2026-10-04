"""
Event loop:
    Uses the shared worker-process loop from app.workers.async_runtime,
    NOT a private loop of its own. This file previously kept its own
    module-level _loop, which caused "attached to a different loop"
    crashes whenever this task's connection checkout collided with
    reconciliation_sweep's — both modules shared one SQLAlchemy engine
    and connection pool, but each ran on its own separate event loop.
    See bugs.md, Bug 3.
"""

import time
from app.core.metrics import (
    record_webhook_success,
    record_webhook_failure,
    record_webhook_retry,
)
from celery import Task
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError, DBAPIError
from app.core.exceptions import (
    TransientProcessingError,
    PermanentProcessingError,
)
from app.core.logging import get_logger
from app.core.pipeline_stages import PipelineStage
from app.models.db.enums import ErrorCategory, WebhookStatus
from app.repositories.unit_of_work import UnitOfWork
from app.workers.celery_app import celery_app
from app.workers.tasks.payment_processor import PaymentProcessor
from app.services.pipeline_publisher import PipelinePublisher
from app.workers.async_runtime import run_async as _run_async


logger = get_logger(__name__)


# ============================================
# TASK CLASS
# ============================================
class ProcessWebhookTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Skip Retry — it's control flow, not failure.
        if isinstance(exc, Retry):
            return
        logger.error(
            f"Celery task permanently failed: task_id={task_id} "
            f"receipt_id={args[0] if args else 'unknown'} exc={exc!r}"
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)


# ============================================
# MAIN TASK
# ============================================
@celery_app.task(
    bind=True,
    base=ProcessWebhookTask,
    name="app.workers.tasks.process_webhook.process_webhook_task",
    max_retries=5,
    default_retry_delay=60,
)
def process_webhook_task(self, receipt_id: str) -> None:
    logger.info(
        f"Task start: receipt_id={receipt_id} "
        f"attempt={self.request.retries + 1}/{self.max_retries + 1}"
    )
    try:
        _run_async(_process(receipt_id=receipt_id, task=self))
    except Retry:
        # Retry is Celery control flow. Re-raise untouched.
        raise
    except Exception as exc:
        logger.critical(
            f"Unexpected error escaped _process for receipt_id={receipt_id}: {exc!r}",
            exc_info=True,
        )
        raise


# ============================================
# ASYNC CORE (testable without Celery)
# ============================================
async def _process(receipt_id: str, task: Task) -> None:
    """
    The actual work. Split from the Celery-decorated function so it
    can be unit-tested without invoking Celery.

    """
    from uuid import UUID

    rid = UUID(receipt_id)
    start_time = time.monotonic()
    async with UnitOfWork.session_scope() as uow:
        # ---- 1. Load receipt ----
        receipt = await uow.receipts.get_by_id(rid)
        if receipt is None:
            # Receipt was deleted (shouldn't happen). Log and exit.
            logger.error(f"Receipt not found: {rid}")
            return

        # ---- 2. Idempotency: already processed? ----
        if receipt.status in (WebhookStatus.SUCCESS, WebhookStatus.DEAD_LETTERED):
            logger.info(
                f"Receipt already terminal: receipt_id={rid} "
                f"status={receipt.status.value}"
            )
            return

        # ---- 3. Transition to processing ----
        await uow.receipts.mark_processing(rid)
        await uow.commit()  # persist so the sweep sees it as 'processing'

        await PipelinePublisher().publish(
            receipt_id=rid, provider=receipt.provider.value, stage=PipelineStage.PROCESSING
        )

        # ---- 4. Call business logic ----
        processor = PaymentProcessor()
        try:
            result = await processor.process(receipt)

        except TransientProcessingError as exc:
            await _handle_transient_error(
                uow, receipt=receipt, exc=exc, task=task, start_time=start_time
            )
            return

        except PermanentProcessingError as exc:
            await _handle_permanent_error(
                uow, receipt=receipt, exc=exc, start_time=start_time
            )
            return

        except OperationalError as exc:
            # DB is having a bad day. Retry. Do NOT move to DLQ —
            # the receipt may be perfectly valid.
            await _handle_db_error(
                uow, receipt=receipt, exc=exc, task=task, start_time=start_time
            )
            return

        except DBAPIError as exc:
            # Same treatment as OperationalError for our purposes.
            await _handle_db_error(
                uow, receipt=receipt, exc=exc, task=task, start_time=start_time
            )
            return

        except Exception as exc:
            # Unknown error — treat as transient, but log CRITICAL.
            logger.critical(
                f"Unknown error processing receipt_id={rid}: {exc!r}",
                exc_info=True,
            )
            await _handle_transient_error(
                uow,
                receipt=receipt,
                exc=TransientProcessingError(f"Unknown error: {exc}"),
                task=task,
                start_time=start_time,
            )
            return

        # ---- 5. Success ----
        await uow.receipts.mark_success(rid, response_snapshot=result)
        await uow.commit()
        # Record success metric with duration.
        duration = time.monotonic() - start_time
        record_webhook_success(
            provider=receipt.provider.value,
            event_type=receipt.event_type,
            duration_seconds=duration,
        )

        await PipelinePublisher().publish(
            receipt_id=rid, provider=receipt.provider.value, stage=PipelineStage.SUCCESS
        )

        # ---- 6. Update idempotency cache ----
        from app.services.idempotency_service import IdempotencyService
        idem = IdempotencyService()
        await idem.update_response(
            provider=receipt.provider.value,
            idempotency_key=receipt.idempotency_key,
            receipt_id=str(receipt.id),
            event_type=receipt.event_type,
            status=WebhookStatus.SUCCESS.value,
            response=result,
        )

        logger.info(f"Task success: receipt_id={rid}")


# ============================================
# ERROR HANDLERS
# ============================================
async def _handle_transient_error(
    uow: UnitOfWork,
    *,
    receipt,
    exc: Exception,
    task: Task,
    start_time: float,
) -> None:
    """
    Transient error: retry with exponential backoff.

    If retries are exhausted, move to DLQ.
    """
    # Persist the error on the receipt for observability.
    receipt.last_error = str(exc)
    receipt.last_error_category = ErrorCategory.TRANSIENT
    await uow.receipts.increment_celery_retry(receipt.id)
    await uow.commit()

    # Are we out of retries?
    if task.request.retries >= task.max_retries:
        logger.error(
            f"Retries exhausted for receipt_id={receipt.id}; moving to DLQ"
        )
        await _move_to_dlq(
            uow,
            receipt=receipt,
            error_message=str(exc),
            error_category=ErrorCategory.TRANSIENT,
            start_time=start_time,
        )
        return

    # Compute backoff: 60s, 120s, 240s, 480s, 960s
    retry_delay = 60 * (2 ** task.request.retries)

    # Update idempotency cache with failure so the admin UI reflects it.
    await _refresh_idem_cache(receipt, status=WebhookStatus.PROCESSING)
    # Record retry metric.
    record_webhook_retry(
        provider=receipt.provider.value,
        error_category=ErrorCategory.TRANSIENT.value,
    )

    await PipelinePublisher().publish(
        receipt_id=receipt.id, provider=receipt.provider.value, stage=PipelineStage.RETRYING
    )

    logger.warning(
        f"Retrying receipt_id={receipt.id} in {retry_delay}s "
        f"(attempt {task.request.retries + 1}/{task.max_retries})"
    )

    # Celery's retry() raises Retry internally, so no return needed.
    raise task.retry(exc=exc, countdown=retry_delay)


async def _handle_permanent_error(
    uow: UnitOfWork,
    *,
    receipt,
    exc: Exception,
    start_time: float,
) -> None:
    """
    Permanent error: skip retries, move directly to DLQ.
    """
    logger.error(
        f"Permanent error for receipt_id={receipt.id}: {exc!r}"
    )
    receipt.last_error = str(exc)
    receipt.last_error_category = ErrorCategory.PERMANENT
    await uow.commit()

    await _move_to_dlq(
        uow,
        receipt=receipt,
        error_message=str(exc),
        error_category=ErrorCategory.PERMANENT,
        start_time=start_time,
    )


async def _handle_db_error(
    uow: UnitOfWork,
    *,
    receipt,
    exc: Exception,
    task: Task,
    start_time: float,
) -> None:
    """
    Database error: retry, but shorter backoff than transient.

    """
    logger.warning(f"DB error for receipt_id={receipt.id}: {exc!r}")

    if task.request.retries >= task.max_retries:
        logger.error(
            f"Retries exhausted (DB error) for receipt_id={receipt.id}; moving to DLQ"
        )
        await _move_to_dlq(
            uow,
            receipt=receipt,
            error_message=f"DB error: {exc}",
            error_category=ErrorCategory.TRANSIENT,
            start_time=start_time,
        )
        return

    retry_delay = 30 * (2 ** task.request.retries)
    # Record retry metric.
    record_webhook_retry(
        provider=receipt.provider.value,
        error_category=ErrorCategory.TRANSIENT.value,
    )

    await PipelinePublisher().publish(
        receipt_id=receipt.id, provider=receipt.provider.value, stage=PipelineStage.RETRYING
    )

    raise task.retry(exc=exc, countdown=retry_delay)


async def _move_to_dlq(
    uow: UnitOfWork,
    *,
    receipt,
    error_message: str,
    error_category: ErrorCategory,
    start_time: float,
) -> None:
    """
    Terminal action: receipt → dead_lettered, insert DLQ row, update cache.

    """
    # 1. Mark receipt as dead_lettered
    await uow.receipts.mark_dead_lettered(
        receipt.id,
        error_message=error_message,
        error_category=error_category,
    )

    # 2. Insert DLQ entry (denormalized snapshot for ops)
    await uow.dead_letter.create(
        webhook_receipt_id=receipt.id,
        provider=receipt.provider,
        event_type=receipt.event_type,
        payload=receipt.payload,
        error_message=error_message,
        error_category=error_category,
        celery_retries_exhausted=receipt.celery_retry_count + 1,
        sweep_attempts_exhausted=receipt.sweep_attempts,
    )

    # 3. Commit both writes atomically
    await uow.commit()

    duration = time.monotonic() - start_time
    record_webhook_failure(
        provider=receipt.provider.value,
        event_type=receipt.event_type,
        error_category=error_category.value,
        duration_seconds=duration,
    )

    # 4. Pipeline event: dead_lettered
    await PipelinePublisher().publish(
        receipt_id=receipt.id, provider=receipt.provider.value, stage=PipelineStage.DEAD_LETTERED
    )

    # 5. Update idempotency cache (best-effort, after commit)
    await _refresh_idem_cache(receipt, status=WebhookStatus.DEAD_LETTERED)

    logger.warning(
        f"Moved to DLQ: receipt_id={receipt.id} "
        f"category={error_category.value} reason={error_message}"
    )


async def _refresh_idem_cache(receipt, *, status: WebhookStatus) -> None:
    """
    Best-effort cache refresh. Failures are logged, not raised.

    """
    try:
        from app.services.idempotency_service import IdempotencyService
        idem = IdempotencyService()
        await idem.update_response(
            provider=receipt.provider.value,
            idempotency_key=receipt.idempotency_key,
            receipt_id=str(receipt.id),
            event_type=receipt.event_type,
            status=status.value,
            response={"status": status.value, "receipt_id": str(receipt.id)},
        )
    except Exception as exc:
        logger.warning(f"Idempotency cache refresh failed: {exc}")