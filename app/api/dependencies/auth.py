"""
Authentication dependencies for protected endpoints.

Only admin endpoints require authentication. Webhook endpoints
must remain public — providers can't send an API key.

Design:
- Header: X-API-Key
- Value: matches settings.ADMIN_API_KEY
- On mismatch: raise AuthenticationError (translated to 401 by the API layer)

Timing safety:
- Comparison uses hmac.compare_digest (constant time).
- Using `==` would leak information about the correct key through
  response timing — an attacker could guess the key one byte at a time.
"""

import hmac
import secrets
from fastapi import Header, HTTPException
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger

logger = get_logger(__name__)

async def require_admin_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> None:
    """
    FastAPI dependency. Verifies the X-API-Key header.
    """
    if not settings.ADMIN_API_KEY:
        # Defensive: config validation should prevent this, but if
        # someone bypasses the config, we fail closed.
        logger.error("ADMIN_API_KEY is not configured; rejecting admin request")
        raise AuthenticationError("Admin authentication is not configured")

    if not hmac.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        logger.warning("Admin API: invalid API key presented")
        raise AuthenticationError("Invalid or missing API key")


async def require_dashboard_access(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> None:
    """
    Verifies the X-API-Key header for dashboard endpoints.
    Same secret as admin — one token, two usage contexts.
    """
    if not settings.ADMIN_API_KEY:
        logger.error("ADMIN_API_KEY is not configured; rejecting dashboard request")
        raise AuthenticationError("Dashboard authentication is not configured")

    if not hmac.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        logger.warning("Dashboard API: invalid API key presented")
        raise AuthenticationError("Invalid or missing API key")