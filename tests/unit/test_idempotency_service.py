import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from redis.exceptions import RedisError
from app.services.idempotency_service import IdempotencyService
from app.models.db.enums import ProviderType


@pytest.fixture
def mock_redis():
    """Fixture providing a mock Redis client with async capabilities."""
    client = MagicMock()
    client.get = AsyncMock()
    client.set = AsyncMock()
    client.ttl = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def idempotency_service(mock_redis):
    """Fixture initializing IdempotencyService with the mock Redis client."""
    return IdempotencyService(redis_client=mock_redis)


# ============================================================================
# CHECK METHOD TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_check_cache_hit(idempotency_service, mock_redis):
    """Test check returns parsed JSON on cache hit."""
    cached_payload = {
        "receipt_id": "rcpt_123",
        "event_type": "payment_intent.succeeded",
        "status": "ACCEPTED",
        "response": {"result": "ok"},
    }
    mock_redis.get.return_value = json.dumps(cached_payload)

    result = await idempotency_service.check("stripe", "evt_123")

    assert result == cached_payload
    mock_redis.get.assert_called_once_with("idem:stripe:evt_123")


@pytest.mark.asyncio
async def test_check_cache_miss(idempotency_service, mock_redis):
    """Test check returns None on cache miss."""
    mock_redis.get.return_value = None

    result = await idempotency_service.check("stripe", "evt_123")

    assert result is None
    mock_redis.get.assert_called_once_with("idem:stripe:evt_123")


@pytest.mark.asyncio
async def test_check_redis_error_fallback(idempotency_service, mock_redis):
    """Test check returns None and handles exception gracefully when Redis fails."""
    mock_redis.get.side_effect = RedisError("Connection refused")

    result = await idempotency_service.check("stripe", "evt_123")

    assert result is None


@pytest.mark.asyncio
async def test_check_corrupt_json(idempotency_service, mock_redis):
    """Test check handles invalid JSON entries as cache misses."""
    mock_redis.get.return_value = "invalid-json-string"

    result = await idempotency_service.check("stripe", "evt_123")

    assert result is None


@pytest.mark.asyncio
async def test_check_missing_event_type_fallback(idempotency_service, mock_redis):
    """Test check handles legacy cache entries missing the event_type field."""
    cached_payload = {
        "receipt_id": "rcpt_123",
        "status": "ACCEPTED",
    }
    mock_redis.get.return_value = json.dumps(cached_payload)

    result = await idempotency_service.check("stripe", "evt_123")

    assert result == cached_payload
    assert "event_type" not in result


@pytest.mark.asyncio
async def test_check_string_status_conversion(idempotency_service, mock_redis):
    """Test check handles raw string status payloads."""
    cached_payload = {
        "receipt_id": "rcpt_123",
        "event_type": "payment_intent.succeeded",
        "status": "pending",
    }
    mock_redis.get.return_value = json.dumps(cached_payload)

    result = await idempotency_service.check("stripe", "evt_123")
    assert result["status"] == "pending"


# ============================================================================
# STORE METHOD TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_store_success(idempotency_service, mock_redis):
    """Test store serializes payload and saves to Redis with correct key and TTL."""
    await idempotency_service.store(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id="rcpt_123",
        event_type="payment_intent.succeeded",
        status="ACCEPTED",
        response={"status": "accepted"},
    )

    mock_redis.set.assert_called_once()
    args, kwargs = mock_redis.set.call_args
    assert args[0] == "idem:stripe:evt_123"
    
    stored_data = json.loads(args[1])
    assert stored_data["receipt_id"] == "rcpt_123"
    assert stored_data["event_type"] == "payment_intent.succeeded"
    assert stored_data["status"] == "ACCEPTED"
    assert stored_data["response"] == {"status": "accepted"}


@pytest.mark.asyncio
async def test_store_redis_error_handled(idempotency_service, mock_redis):
    """Test store does not propagate RedisError exceptions."""
    mock_redis.set.side_effect = RedisError("Write timeout")

    await idempotency_service.store(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id="rcpt_123",
        event_type="payment_intent.succeeded",
        status="ACCEPTED",
    )


# ============================================================================
# UPDATE RESPONSE & INVALIDATE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_update_response_existing_key(idempotency_service, mock_redis):
    """Test update_response updates cached entry preserving remaining TTL."""
    mock_redis.ttl.return_value = 1800

    await idempotency_service.update_response(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id="rcpt_123",
        event_type="payment_intent.succeeded",
        status="COMPLETED",
        response={"status": "processed"},
    )

    mock_redis.ttl.assert_called_once_with("idem:stripe:evt_123")
    mock_redis.set.assert_called_once()
    _, kwargs = mock_redis.set.call_args
    assert kwargs["ex"] == 1800


@pytest.mark.asyncio
async def test_update_response_expired_key(idempotency_service, mock_redis):
    """Test update_response skips updating if key is expired (TTL <= 0)."""
    mock_redis.ttl.return_value = -2

    await idempotency_service.update_response(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id="rcpt_123",
        event_type="payment_intent.succeeded",
        status="COMPLETED",
        response={"status": "processed"},
    )

    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_update_response_redis_error_handled(idempotency_service, mock_redis):
    """Test update_response handles Redis exceptions gracefully without propagating."""
    mock_redis.ttl.side_effect = RedisError("Redis connection error")

    await idempotency_service.update_response(
        provider="stripe",
        idempotency_key="evt_123",
        receipt_id="rcpt_123",
        event_type="payment_intent.succeeded",
        status="COMPLETED",
        response={"status": "processed"},
    )


@pytest.mark.asyncio
async def test_invalidate_deletes_key(idempotency_service, mock_redis):
    """Test invalidate removes key from Redis."""
    await idempotency_service.invalidate("stripe", "evt_123")

    mock_redis.delete.assert_called_once_with("idem:stripe:evt_123")


@pytest.mark.asyncio
async def test_invalidate_redis_error_handled(idempotency_service, mock_redis):
    """Test invalidate handles Redis exceptions gracefully when key deletion fails."""
    mock_redis.delete.side_effect = RedisError("Redis unavailable")

    await idempotency_service.invalidate("stripe", "evt_123")


# ============================================================================
# INITIALIZATION FALLBACK TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_init_default_redis_client():
    """Test default Redis connection fallback initialization when no client is passed."""
    with patch("redis.asyncio.from_url", return_value=MagicMock()):
        service = IdempotencyService(redis_client=None)
        assert service is not None