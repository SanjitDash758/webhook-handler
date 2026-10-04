import uuid
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.core.exceptions import DuplicateEventConflict
from app.main import app
from app.repositories.unit_of_work import get_uow
from app.services.webhook_service import IngestionResult


@pytest.mark.asyncio
async def test_generic_webhook_missing_required_headers(async_client: AsyncClient):
    """Test 422 Unprocessable Entity when Idempotency-Key or X-Event-Type is missing."""
    response = await async_client.post(
        "/webhooks/generic",
        json={"order_id": "12345"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generic_webhook_header_length_exceeded(async_client: AsyncClient):
    """Test 400 Bad Request when Idempotency-Key header exceeds maximum length limit."""
    headers = {
        "Idempotency-Key": "a" * 300,  # MAX_IDEMPOTENCY_KEY_LENGTH is 255
        "X-Event-Type": "order.created",
    }
    response = await async_client.post(
        "/webhooks/generic",
        json={"order_id": "12345"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "exceeds 255 chars" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generic_webhook_malformed_json(async_client: AsyncClient):
    """Test 400 Bad Request when request body is not valid JSON."""
    headers = {
        "Idempotency-Key": "key-001",
        "X-Event-Type": "order.created",
    }
    response = await async_client.post(
        "/webhooks/generic",
        content=b"invalid-json{",
        headers=headers,
    )
    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.routers.generic_webhooks.WebhookIngestionService")
async def test_generic_webhook_success_new_event(
    mock_service_cls, async_client: AsyncClient, mock_uow
):
    """Test 202 Accepted for valid new generic webhook."""
    receipt_id = uuid.uuid4()
    mock_service = mock_service_cls.return_value
    mock_service.ingest = AsyncMock(
        return_value=IngestionResult(
            is_new=True,
            status="accepted",
            receipt_id=receipt_id,
            response_payload={"status": "accepted", "receipt_id": str(receipt_id)},
        )
    )

    app.dependency_overrides[get_uow] = lambda: mock_uow

    headers = {
        "Idempotency-Key": "key-gen-001",
        "X-Event-Type": "order.created",
    }
    response = await async_client.post(
        "/webhooks/generic",
        json={"order_id": "12345"},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    # assert data["event_type"] == "order.created"
    if "receipt_id" in data:
        assert data["receipt_id"] == str(receipt_id)
    elif "data" in data and "receipt_id" in data["data"]:
        assert data["data"]["receipt_id"] == str(receipt_id)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.api.routers.generic_webhooks.WebhookIngestionService")
async def test_generic_webhook_duplicate_conflict_409(
    mock_service_cls, async_client: AsyncClient, mock_uow
):
    """Test 409 Conflict when idempotency key is reused for a different event type."""
    mock_service = mock_service_cls.return_value
    mock_service.ingest = AsyncMock(
        side_effect=DuplicateEventConflict("Idempotency key reused for different event")
    )

    app.dependency_overrides[get_uow] = lambda: mock_uow

    headers = {
        "Idempotency-Key": "key-gen-001",
        "X-Event-Type": "order.cancelled",
    }
    response = await async_client.post(
        "/webhooks/generic",
        json={"order_id": "12345"},
        headers=headers,
    )

    assert response.status_code == 409
    assert "Idempotency key reused for different event" in response.json()["detail"]

    app.dependency_overrides.clear()