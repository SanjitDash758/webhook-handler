"""
Stripe webhook signature verification.

Reference: https://stripe.com/docs/webhooks/signatures

Algorithm (Stripe's own):
1. Extract timestamp (t) and signature (v1) from the Stripe-Signature header.
2. Reject if the timestamp is too old (replay protection).
3. Compute HMAC-SHA256 over the string "{timestamp}.{raw_body}".
4. Compare with the v1 signature using constant-time comparison.
5. Return the parsed JSON payload if valid.

"""

import hashlib
import hmac
import json
import time
from typing import Any
from app.core.exceptions import SignatureVerificationError
from app.core.logging import get_logger

logger = get_logger(__name__)


# Stripe's recommended tolerance. Webhooks older than this
# are rejected to prevent replay attacks.
DEFAULT_TOLERANCE_SECONDS = 300  # 5 minutes


def verify_stripe_signature(
    *,
    payload: bytes,
    sig_header: str,
    secret: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    if not sig_header:
        raise SignatureVerificationError("Missing Stripe-Signature header")

    if not secret:
        raise SignatureVerificationError("Stripe webhook secret is not configured")

    # ---- 1. Parse the header ----
    timestamp, signatures = _parse_signature_header(sig_header)

    # ---- 2. Replay protection ----
    _check_timestamp(timestamp, tolerance_seconds)

    # ---- 3. Compute expected signature ----
    expected = _compute_expected_signature(
        timestamp=timestamp,
        payload=payload,
        secret=secret,
    )

    # ---- 4. Constant-time comparison ----
    if not _any_signature_matches(signatures, expected):
        logger.warning("Stripe signature verification failed")
        raise SignatureVerificationError("Invalid signature")

    # ---- 5. Parse and return ----
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SignatureVerificationError(f"Invalid JSON in payload: {exc}") from exc


# ============================================
# INTERNAL HELPERS
# ============================================

def _parse_signature_header(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []

    for part in header.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue

        key, _, value = part.partition("=")

        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureVerificationError(
                    f"Invalid timestamp in signature header: {value!r}"
                ) from exc
        elif key == "v1":
            signatures.append(value)

    if timestamp is None:
        raise SignatureVerificationError("Missing timestamp (t=) in signature header")

    if not signatures:
        raise SignatureVerificationError("Missing v1 signature in header")

    return timestamp, signatures


def _check_timestamp(timestamp: int, tolerance_seconds: int) -> None:
    now = int(time.time())
    delta = abs(now - timestamp)

    if delta > tolerance_seconds:
        raise SignatureVerificationError(
            f"Timestamp outside tolerance window "
            f"(delta={delta}s, tolerance={tolerance_seconds}s)"
        )


def _compute_expected_signature(
    *,
    timestamp: int,
    payload: bytes,
    secret: str,
) -> str:
    signed_payload = f"{timestamp}.".encode("utf-8") + payload

    return hmac.new(
        key=secret.encode("utf-8"),
        msg=signed_payload,
        digestmod=hashlib.sha256,
    ).hexdigest()


def _any_signature_matches(candidates: list[str], expected: str) -> bool:
    match = False
    for candidate in candidates:
        if hmac.compare_digest(candidate, expected):
            match = True
           
    return match