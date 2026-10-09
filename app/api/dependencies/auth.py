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
    Verifies the X-API-Key header.
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
    authorization: str = Header(..., alias="Authorization"),
) -> None:
    """
    Dependency for dashboard endpoints.
    Expects: Authorization: Bearer <token>
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
        )

    token = authorization.removeprefix("Bearer ").strip()

    if not settings.ADMIN_API_KEY:
        logger.error("ADMIN_API_KEY is not configured; rejecting dashboard request")
        raise HTTPException(status_code=500, detail="Dashboard auth not configured")

    if not secrets.compare_digest(token, settings.ADMIN_API_KEY):
        logger.warning("Dashboard API: invalid token presented")
        raise HTTPException(status_code=401, detail="Invalid dashboard token")