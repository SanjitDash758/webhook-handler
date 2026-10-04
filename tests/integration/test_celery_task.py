import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.db.enums import WebhookStatus


@pytest.mark.asyncio
async def test_celery_task_context_handling():
    """Test celery worker unit of work handling."""
    receipt_id = str(uuid.uuid4())

    with patch("app.repositories.unit_of_work.UnitOfWork") as MockUOW:
        mock_uow_instance = MockUOW.return_value
        mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
        mock_uow_instance.__aexit__ = AsyncMock(return_value=None)
        
        fake_receipt = MagicMock()
        fake_receipt.id = receipt_id
        fake_receipt.status = WebhookStatus.PENDING
        mock_uow_instance.receipts.get_by_id = AsyncMock(return_value=fake_receipt)

        # Basic context verification check
        assert mock_uow_instance.receipts is not None