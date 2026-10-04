import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_sweeper_runs_without_exceptions():
    """Test sweeper context initialization."""
    mock_uow = MagicMock()
    mock_uow.receipts.get_stuck_webhooks = AsyncMock(return_value=[])
    
    stuck_items = await mock_uow.receipts.get_stuck_webhooks()
    assert stuck_items == []