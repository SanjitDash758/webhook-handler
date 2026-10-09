import uuid
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from app.core.exceptions import DuplicateEventConflict, SignatureVerificationError
from app.main import app
from app.repositories.unit_of_work import get_uow
from app.services.webhook_service import IngestionResult


@pytest.mark.asyncio
async def test_stripe_webhook_missing_signature_header(async_client: AsyncClient):
    """Test 422 Unprocessable Entity when Stripe-Signature header is missing."""
    response = await async_client.post(
        "/webhooks/stripe",
        json={"id": "evt_123", "type": "payment_intent.succeeded"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_stripe_webhook_empty_body(async_client: AsyncClient):
    """Test 400 Bad Request when body is empty."""
    headers = {"Stripe-Signature": "t=123,v1=fakesig"}
    response = await async_client.post(
        "/webhooks/stripe",
        content=b"",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Empty request body"


@pytest.mark.asyncio
@patch("app.api.routers.stripe_webhooks.verify_stripe_signature")
async def test_stripe_webhook_invalid_signature(mock_verify, async_client: AsyncClient):
    """Test 400 Bad Request on SignatureVerificationError."""
    mock_verify.side_effect = SignatureVerificationError("Invalid signature")
    headers = {"Stripe-Signature": "t=123,v1=fakesig"}

    response = await async_client.post(
        "/webhooks/stripe",
        json={"id": "evt_123", "type": "payment_intent.succeeded"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid signature"


@pytest.mark.asyncio
@patch("app.api.routers.stripe_webhooks.WebhookIngestionService")
@patch("app.api.routers.stripe_webhooks.verify_stripe_signature")
async def test_stripe_webhook_success_new_event(
    mock_verify, mock_service_cls, async_client: AsyncClient, mock_uow
):
    """Test 202 Accepted for a new valid Stripe webhook."""
    receipt_id = uuid.uuid4()
    mock_verify.return_value = {"id": "evt_test123", "type": "charge.succeeded"}

    mock_service = mock_service_cls.return_value
    mock_service.ingest = AsyncMock(
        return_value=IngestionResult(
            is_new=True,
            status="accepted",
            receipt_id=receipt_id,
            response_payload={"status": "accepted"},
        )
    )

    app.dependency_overrides[get_uow] = lambda: mock_uow

    headers = {"Stripe-Signature": "t=123,v1=valid_sig"}
    response = await async_client.post(
        "/webhooks/stripe",
        json={"id": "evt_test123", "type": "charge.succeeded"},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["event_id"] == "evt_test123"
    assert data["receipt_id"] == str(receipt_id)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.api.routers.stripe_webhooks.WebhookIngestionService")
@patch("app.api.routers.stripe_webhooks.verify_stripe_signature")
async def test_stripe_webhook_duplicate_event_cached(
    mock_verify, mock_service_cls, async_client: AsyncClient, mock_uow
):
    """Test returning cached response for duplicate Stripe webhook."""
    receipt_id = uuid.uuid4()
    mock_verify.return_value = {"id": "evt_test123", "type": "charge.succeeded"}

    cached_payload = {
        "status": "accepted",
        "receipt_id": str(receipt_id),
        "event_id": "evt_test123",
    }

    mock_service = mock_service_cls.return_value
    mock_service.ingest = AsyncMock(
        return_value=IngestionResult(
            is_new=False,
            status="accepted",
            receipt_id=receipt_id,
            response_payload=cached_payload,
        )
    )

    app.dependency_overrides[get_uow] = lambda: mock_uow

    headers = {"Stripe-Signature": "t=123,v1=valid_sig"}
    response = await async_client.post(
        "/webhooks/stripe",
        json={"id": "evt_test123", "type": "charge.succeeded"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == cached_payload

    app.dependency_overrides.clear()