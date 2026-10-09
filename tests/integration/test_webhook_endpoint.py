import hmac
import hashlib
import json
import time
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, PropertyMock, patch
from app.core.exceptions import DuplicateEventConflict, SignatureVerificationError
from app.models.db.enums import WebhookStatus
from app.services.webhook_service import IngestionResult


def generate_stripe_signature(secret: str, payload: bytes, timestamp: int = None) -> str:
    """Helper to generate Stripe-formatted webhook signature headers (t=...,v1=...)."""
    ts = timestamp or int(time.time())
    signed_payload = f"{ts}.".encode("utf-8") + payload
    sig = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={sig}"


# ============================================================================
# GENERIC WEBHOOK ROUTER TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_generic_webhook_success(async_client: AsyncClient):
    """Test successful ingestion of generic webhook with required headers."""
    payload = {"order_id": "ord_999", "amount": 100}
    raw_body = json.dumps(payload).encode("utf-8")

    headers = {
        "Idempotency-Key": "idem_generic_100",
        "X-Event-Type": "order.created",
        "Content-Type": "application/json",
    }

    mock_result = IngestionResult(
        receipt_id=uuid.uuid4(),
        is_new=True,
        status=WebhookStatus.PENDING,
    )

    with patch("app.api.routers.generic_webhooks.WebhookIngestionService.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = mock_result

        response = await async_client.post("/webhooks/generic", content=raw_body, headers=headers)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event_type"] == "order.created"
        assert "receipt_id" in data


@pytest.mark.asyncio
async def test_generic_webhook_header_validations(async_client: AsyncClient):
    """Test empty and oversized header validations on generic webhook endpoint."""
    payload = json.dumps({"key": "value"}).encode("utf-8")

    # 1. Empty Idempotency-Key
    res = await async_client.post(
        "/webhooks/generic",
        content=payload,
        headers={"Idempotency-Key": "   ", "X-Event-Type": "order.created"}
    )
    assert res.status_code == 400
    assert "Idempotency-Key is empty" in res.json()["detail"]

    # 2. Idempotency-Key exceeds max length (> 255)
    res = await async_client.post(
        "/webhooks/generic",
        content=payload,
        headers={"Idempotency-Key": "a" * 256, "X-Event-Type": "order.created"}
    )
    assert res.status_code == 400
    assert "exceeds 255 chars" in res.json()["detail"]

    # 3. Empty X-Event-Type
    res = await async_client.post(
        "/webhooks/generic",
        content=payload,
        headers={"Idempotency-Key": "valid_key", "X-Event-Type": "   "}
    )
    assert res.status_code == 400
    assert "X-Event-Type is empty" in res.json()["detail"]

    # 4. X-Event-Type exceeds max length (> 100)
    res = await async_client.post(
        "/webhooks/generic",
        content=payload,
        headers={"Idempotency-Key": "valid_key", "X-Event-Type": "e" * 101}
    )
    assert res.status_code == 400
    assert "exceeds 100 chars" in res.json()["detail"]


@pytest.mark.asyncio
async def test_generic_webhook_payload_validations(async_client: AsyncClient):
    """Test empty body, invalid JSON, and non-dict payload validations."""
    headers = {"Idempotency-Key": "key_100", "X-Event-Type": "order.created"}

    # Empty body
    res = await async_client.post("/webhooks/generic", content=b"", headers=headers)
    assert res.status_code == 400
    assert "Empty request body" in res.json()["detail"]

    # Invalid JSON
    res = await async_client.post("/webhooks/generic", content=b"{bad_json", headers=headers)
    assert res.status_code == 400
    assert "Invalid JSON" in res.json()["detail"]

    # Non-dictionary payload (JSON List)
    res = await async_client.post("/webhooks/generic", content=b"[1, 2, 3]", headers=headers)
    assert res.status_code == 400
    assert "JSON object" in res.json()["detail"]


@pytest.mark.asyncio
async def test_generic_webhook_duplicate_and_conflict(async_client: AsyncClient):
    """Test duplicate generic webhook hits return 200 OK with cached or fallback payload."""
    headers = {"Idempotency-Key": "idem_dup", "X-Event-Type": "order.created"}
    body = json.dumps({"order_id": "123"}).encode("utf-8")
    receipt_id = uuid.uuid4()

    mock_duplicate = IngestionResult(
        receipt_id=receipt_id,
        is_new=False,
        status=WebhookStatus.PENDING,
        response_payload={"status": "accepted", "receipt_id": str(receipt_id), "event_type": "order.created"},
    )

    with patch("app.api.routers.generic_webhooks.WebhookIngestionService.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = mock_duplicate
        res = await async_client.post("/webhooks/generic", content=body, headers=headers)
        
        # Enforces contract: Duplicates return 200 OK
        assert res.status_code == 200
        assert res.json()["receipt_id"] == str(receipt_id)

    with patch("app.api.routers.generic_webhooks.WebhookIngestionService.ingest", side_effect=DuplicateEventConflict("Key reused")):
        res = await async_client.post("/webhooks/generic", content=body, headers=headers)
        assert res.status_code == 409
        assert "Key reused" in res.json()["detail"]
# ============================================================================
# STRIPE WEBHOOK ROUTER TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_stripe_webhook_success(async_client: AsyncClient):
    """Test successful ingestion of Stripe webhook with verified signature."""
    payload = {
        "id": "evt_stripe_300",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_123", "amount": 2000}},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature_header = generate_stripe_signature("whsec_test_secret", raw_body)

    headers = {
        "Stripe-Signature": signature_header,
        "Content-Type": "application/json",
    }

    mock_result = IngestionResult(
        receipt_id=uuid.uuid4(),
        is_new=True,
        status=WebhookStatus.PENDING,
    )

    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload), \
         patch("app.api.routers.stripe_webhooks.WebhookIngestionService.ingest", new_callable=AsyncMock) as mock_ingest:

        mock_ingest.return_value = mock_result

        response = await async_client.post("/webhooks/stripe", content=raw_body, headers=headers)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "evt_stripe_300"


@pytest.mark.asyncio
async def test_stripe_webhook_signature_failure_and_empty_body(async_client: AsyncClient):
    """Test rejection on missing body or invalid Stripe signature."""
    headers = {"Stripe-Signature": "t=123,v1=sig"}

    res = await async_client.post("/webhooks/stripe", content=b"", headers=headers)
    assert res.status_code == 400
    assert "Empty request body" in res.json()["detail"]

    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", side_effect=SignatureVerificationError("Invalid sig")):
        res = await async_client.post("/webhooks/stripe", content=b'{"id": "1"}', headers=headers)
        assert res.status_code == 400
        assert "Invalid signature" in res.json()["detail"]


@pytest.mark.asyncio
async def test_stripe_webhook_missing_event_id_or_type(async_client: AsyncClient):
    """Test rejection when Stripe payload is missing 'id' or 'type'."""
    headers = {"Stripe-Signature": "t=123,v1=sig"}

    payload_no_id = {"type": "payment_intent.succeeded"}
    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload_no_id):
        res = await async_client.post("/webhooks/stripe", content=b'{}', headers=headers)
        assert res.status_code == 400
        assert "Missing event id" in res.json()["detail"]

    payload_no_type = {"id": "evt_123"}
    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload_no_type):
        res = await async_client.post("/webhooks/stripe", content=b'{}', headers=headers)
        assert res.status_code == 400
        assert "Missing event type" in res.json()["detail"]


@pytest.mark.asyncio
async def test_stripe_webhook_duplicate_and_conflict(async_client: AsyncClient):
    """Test duplicate response, status_code property contract (200 OK), and 409 conflict handling on Stripe router."""
    headers = {"Stripe-Signature": "t=123,v1=sig"}
    payload = {"id": "evt_dup", "type": "payment_intent.succeeded"}
    receipt_id = uuid.uuid4()

    # 1. Duplicate event with custom response payload
    mock_dup = IngestionResult(
        receipt_id=receipt_id,
        is_new=False,
        status=WebhookStatus.PENDING,
        response_payload={"status": "accepted", "receipt_id": str(receipt_id), "event_id": "evt_dup"}
    )
    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload), \
         patch("app.api.routers.stripe_webhooks.WebhookIngestionService.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = mock_dup
        res = await async_client.post("/webhooks/stripe", content=b"{}", headers=headers)
        
        # IngestionResult.status_code returns 200 for duplicates
        assert res.status_code == 200
        assert res.json()["receipt_id"] == str(receipt_id)

    # 2. Duplicate event with fallback response payload (response_payload is None)
    mock_dup_fallback = IngestionResult(
        receipt_id=receipt_id,
        is_new=False,
        status=WebhookStatus.PENDING,
        response_payload=None
    )
    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload), \
         patch("app.api.routers.stripe_webhooks.WebhookIngestionService.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = mock_dup_fallback
        res = await async_client.post("/webhooks/stripe", content=b"{}", headers=headers)
        assert res.status_code == 200
        assert res.json()["event_id"] == "evt_dup"

    # 3. Duplicate event conflict (409)
    with patch("app.api.routers.stripe_webhooks.verify_stripe_signature", return_value=payload), \
         patch("app.api.routers.stripe_webhooks.WebhookIngestionService.ingest", side_effect=DuplicateEventConflict("Stripe key conflict")):
        res = await async_client.post("/webhooks/stripe", content=b"{}", headers=headers)
        assert res.status_code == 409
        assert "Stripe key conflict" in res.json()["detail"]