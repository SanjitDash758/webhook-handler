from typing import Optional
import redis.asyncio as redis
from redis.exceptions import RedisError
from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)

# Module-level singleton. None until the first call to get_redis().
_client: Optional[redis.Redis] = None


# ============================================
# LIFECYCLE
# ============================================
async def init_redis() -> redis.Redis:
    global _client
    if _client is not None:
        return _client

    logger.info(f"Initializing Redis client: {_redact(settings.REDIS_URL)}")

    _client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,   
        socket_timeout=5,        
        socket_connect_timeout=5,
        max_connections=50,      
        retry_on_timeout=True,
        retry_on_error=[ConnectionError, TimeoutError],
        health_check_interval=30,
    )

    # Fail-fast: verify the server is reachable before we accept traffic.
    try:
        await _client.ping()
        logger.info("Redis connection verified (PING → PONG)")
    except RedisError as exc:
        logger.error(f"Redis connection failed: {exc}")
        # Clean up the half-initialized client so a retry can re-create it.
        await _client.aclose()
        _client = None
        raise

    return _client


async def get_redis() -> redis.Redis:
    if _client is None:
        await init_redis()
    return _client


async def close_redis() -> None:
    global _client
    if _client is None:
        return
    logger.info("Closing Redis connection pool")
    try:
        await _client.aclose()
    except RedisError as exc:
        # Closing shouldn't raise; log if it does.
        logger.warning(f"Error while closing Redis client: {exc}")
    finally:
        _client = None


# ============================================
# HELPERS
# ============================================
def _redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme_and_creds, rest = url.rsplit("@", 1)
    if ":" not in scheme_and_creds:
        return url
    scheme, _creds = scheme_and_creds.split("://", 1)
    return f"{scheme}://***@{rest}"