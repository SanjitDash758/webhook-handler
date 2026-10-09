import json
from typing import Any, Optional
import redis.asyncio as redis
from redis.exceptions import RedisError
from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.enums import ProviderType

logger = get_logger(__name__)

class IdempotencyService:
    """
    Redis fast path for idempotency checks.

    This class is async-friendly and stateless — the Redis client
    is injected (or lazily created from settings).
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client

    # ============================================
    # CLIENT
    # ============================================
    def _client(self) -> redis.Redis:
        """
        Return the Redis client, creating it lazily.
        """
        if self._redis is None:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        return self._redis
    # ============================================
    # KEY BUILDING
    # ============================================
    def _build_key(self, provider: str, idempotency_key: str) -> str:
        """
        Key format: idem:{provider}:{idempotency_key}

        Provider is part of the key so that the same key from two
        providers (Stripe vs Generic) doesn't collide.
        """
        return f"idem:{provider}:{idempotency_key}"

    def _ttl_for(self, provider: str) -> int:
        """
        How long a processed key remains cached.

        Trade-off:
        - Long TTL → more memory, but catches late retries.
        - Short TTL → less memory, but late retries hit Postgres.

        Stripe retries for up to 3 days. We keep 24h — the Postgres
        constraint catches anything older.
        """
        if provider == ProviderType.STRIPE.value:
            return settings.IDEMPOTENCY_TTL_STRIPE
        return settings.IDEMPOTENCY_TTL_GENERIC

    # ============================================
    # FAST PATH: LOOKUP
    # ============================================
    async def check(self, provider: str, idempotency_key: str) -> Optional[dict[str, Any]]:
        """
        Check Redis for a previously-seen event.

        Returns:
            dict with {receipt_id, status, response} if found.
            None if not found OR Redis is unavailable.

        On Redis error, we log and return None — correctness falls
        back to Postgres, and we do NOT want to reject a legit webhook
        just because Redis is having a bad day.
        """
        key = self._build_key(provider, idempotency_key)
        try:
            raw = await self._client().get(key)
        except RedisError as exc:
            logger.warning(f"Redis unavailable during idempotency check: {exc}")
            return None

        if raw is None:
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt cache entry — treat as miss and let Postgres decide.
            logger.warning(f"Corrupt idempotency cache entry for key={key}")
            return None

    # ============================================
    # WRITE
    # ============================================
    async def store(
        self,
        *,
        provider: str,
        idempotency_key: str,
        receipt_id: str,
        event_type: str,     
        status: str,
        response: Optional[dict[str, Any]] = None,
    ) -> None:
        key = self._build_key(provider, idempotency_key)
        value = json.dumps({
             "receipt_id": receipt_id,
            "event_type": event_type,   # ← NEW
            "status": status,
            "response": response,
        })
        try:
            await self._client().set(key, value, ex=self._ttl_for(provider))
        except RedisError as exc:
            logger.warning(f"Redis unavailable during idempotency store: {exc}")
    async def update_response(
        self,
        *,
        provider: str,
        idempotency_key: str,
        receipt_id: str,
        event_type: str, 
        status: str,
        response: dict[str, Any],
    ) -> None:
        """
        Update the cached entry after processing finishes.

        Preserves the original TTL by re-fetching it; if the key has
        expired, this is a no-op.
        """
        key = self._build_key(provider, idempotency_key)
        try:
            # Get remaining TTL so we don't extend the original window
            ttl = await self._client().ttl(key)
            if ttl <= 0:
                # Key expired or never existed — nothing to update.
                return
            value = json.dumps(
                {
                    "receipt_id": receipt_id,
                    "event_type": event_type,
                    "status": status,
                    "response": response,
                }
            )
            await self._client().set(key, value, ex=ttl)
        except RedisError as exc:
            logger.warning(f"Redis unavailable during idempotency update: {exc}")

    # ============================================
    # INVALIDATION (used by admin replay)
    # ============================================
    async def invalidate(self, provider: str, idempotency_key: str) -> None:
        """
        Remove a cached entry. Used when admin replays a failed webhook.

        Without this, an admin replay would immediately hit the cached
        entry and short-circuit — the replay would be a no-op.
        """
        key = self._build_key(provider, idempotency_key)
        try:
            await self._client().delete(key)
        except RedisError as exc:
            logger.warning(f"Redis unavailable during idempotency invalidate: {exc}")