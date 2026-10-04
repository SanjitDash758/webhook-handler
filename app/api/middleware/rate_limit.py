"""
Fixed-window rate limiting middleware.

Runs BEFORE the router — rejects abusive traffic without
touching the DB or doing CPU-intensive work.

Algorithm:
    For each request:
        window = int(now / 60)
        key = "rl:{path_prefix}:{window}"
        count = INCR(key)
        if count == 1: EXPIRE(key, 60 + buffer)
        if count > limit: reject 429

Design:
- One Redis key per (path_prefix, minute).
- TTL is 60s + small buffer to avoid premature expiry.
- Fail OPEN if Redis is unavailable: a Redis outage should not
  take down webhook ingestion.
- Skips non-webhook paths (health, admin, docs).
"""

from __future__ import annotations
import time
from typing import Optional
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.redis_client import get_redis

logger = get_logger(__name__)

PATH_LIMITS: dict[str, int] = {
    "/webhooks/stripe": settings.RATE_LIMIT_STRIPE,
    "/webhooks/generic": settings.RATE_LIMIT_GENERIC,
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        limit = self._resolve_limit(request.url.path)
        if limit is None:
            return await call_next(request)
        if request.method != "POST":
            return await call_next(request)

        allowed, count, retry_after = await self._check(request.url.path, limit)

        if not allowed:
            logger.warning(
                f"Rate limit exceeded: path={request.url.path}"
                f"count={count} limit={limit}"
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests. Limit: {limit}/minute.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    @staticmethod
    def _resolve_limit(path: str) -> Optional[int]:
        """Longest-prefix match against PATH_LIMITS."""
        best: Optional[str] = None
        for prefix in PATH_LIMITS:
            if path.startswith(prefix):
                if best is None or len(prefix) > len(best):
                    best = prefix
        return PATH_LIMITS[best] if best else None

    @staticmethod
    def _resolve_prefix(path: str) -> str:
        """Return the matched prefix, or the full path."""
        for prefix in PATH_LIMITS:
            if path.startswith(prefix):
                return prefix
        return path

    async def _check(self, path: str, limit: int) -> tuple[bool, int, int]:
        prefix = self._resolve_prefix(path)
        window = int(time.time() // 60)
        key = f"rl:{prefix}:{window}"

        try:
            client = await get_redis()
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, 70)
        except Exception as exc:
            logger.warning(f"Rate limit check failed (failing open): {exc}")
            return True, 0, 0

        if count > limit:
            seconds_into_window = int(time.time()) % 60
            retry_after = max(60 - seconds_into_window, 1)
            return False, count, retry_after

        return True, count, 0