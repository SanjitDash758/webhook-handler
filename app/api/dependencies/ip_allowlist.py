"""
IP allowlist dependency for Stripe webhook endpoints.

Design notes:
- Loaded once at import time. Refreshing requires restart.
- Fail-closed: refuses to start if enabled and the file is missing
  or empty (unless STRIPE_IP_ALLOWLIST_OVERRIDE is set).
- Uses the TCP peer address (request.client.host), NOT headers.
  X-Forwarded-For is spoofable; only trust it behind a proxy you
  control, and even then only after proper configuration.
- Emits a metric on every rejection for alerting.

This is defense-in-depth. It reduces attack surface but is NOT
authentication. The HMAC signature verification is the real
trust boundary.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import ip_allowlist_rejections_total


logger = get_logger(__name__)


# ============================================
# LOAD THE ALLOWLIST AT IMPORT TIME
# ============================================
_allowlist: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
_enabled: bool = settings.STRIPE_IP_ALLOWLIST_ENABLED


def _load_allowlist_from_file(path: str) -> tuple[ipaddress._BaseNetwork, ...]:
    """
    Read the JSON file and parse each entry as a CIDR network.

    Raises:
        FileNotFoundError: if the file doesn't exist.
        ValueError: if the file is malformed, the JSON is invalid,
            or any entry is not a valid CIDR.
    """
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"IP allowlist file not found: {path}")

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"IP allowlist file is not valid JSON: {exc}") from exc

    prefixes: Iterable[str] = data.get("prefixes", [])
    if not prefixes:
        raise ValueError(f"IP allowlist file has no 'prefixes' entries: {path}")

    networks: list[ipaddress._BaseNetwork] = []
    for raw in prefixes:
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"Invalid CIDR in IP allowlist: {raw!r} — {exc}"
            ) from exc

    return tuple(networks)


if _enabled:
    if settings.STRIPE_IP_ALLOWLIST_OVERRIDE:
        logger.critical(
            "STRIPE_IP_ALLOWLIST_OVERRIDE=true — IP allowlist is DISABLED "
            "despite STRIPE_IP_ALLOWLIST_ENABLED=true. This should be a "
            "temporary emergency measure. Investigate why the allowlist "
            "is failing and remove the override."
        )
        _enabled = False
    else:
        try:
            _allowlist = _load_allowlist_from_file(settings.STRIPE_IP_ALLOWLIST_FILE)
            logger.info(
                f"Stripe IP allowlist loaded: {len(_allowlist)} networks "
                f"from {settings.STRIPE_IP_ALLOWLIST_FILE}"
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.critical(
                f"Failed to load Stripe IP allowlist: {exc}. "
                f"Set STRIPE_IP_ALLOWLIST_ENABLED=false to disable, or "
                f"STRIPE_IP_ALLOWLIST_OVERRIDE=true to bypass (emergency)."
            )
            # Fail closed: refuse to start.
            raise


# ============================================
# THE DEPENDENCY
# ============================================
async def require_stripe_ip(request: Request) -> None:
    """
    FastAPI dependency. Rejects requests whose source IP is not in
    the Stripe allowlist.

    Behavior when disabled:
        No-op. Every request passes. This is the default for local dev.

    Behavior when enabled:
        - Extract request.client.host (TCP peer IP)
        - Normalize to an ip_address object
        - Check against every allowed network
        - On miss: log WARNING, increment metric, raise 403
    """
    if not _enabled:
        return

    # request.client can be None in unusual contexts (e.g., synthetic
    # test requests). Fail closed — we cannot verify the source.
    if request.client is None or not request.client.host:
        logger.warning(
            "IP allowlist: request has no client address (rejecting)"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    try:
        peer_ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        # Malformed IP — should not happen for a real TCP peer, but
        # fail closed if it does.
        logger.warning(
            f"IP allowlist: could not parse client host "
            f"{request.client.host!r} (rejecting)"
        )
        _record_rejection(path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    # Match against the allowlist.
    for network in _allowlist:
        if peer_ip in network:
            return  # allowed

    # Not found in allowlist.
    logger.warning(
        f"IP allowlist: rejected source_ip={peer_ip} "
        f"path={request.url.path} user_agent={request.headers.get('user-agent', '')!r}"
    )
    _record_rejection(path=request.url.path)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden",
    )


def _record_rejection(path: str) -> None:
    """Increment the metric. Wrapped so a metric failure never breaks the request."""
    try:
        ip_allowlist_rejections_total.labels(path=path).inc()
    except Exception as exc:
        logger.warning(f"Failed to record IP allowlist rejection metric: {exc}")