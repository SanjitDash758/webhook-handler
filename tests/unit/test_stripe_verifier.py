import hmac
import hashlib
import json
import time
import pytest

from app.services.stripe_verifier import (
    verify_stripe_signature,
    _compute_expected_signature,
    _parse_signature_header,
)
from app.core.exceptions import SignatureVerificationError


SECRET = "whsec_test_secret_12345"
PAYLOAD = b'{"id": "evt_test", "type": "payment_intent.succeeded"}'


def build_stripe_header(timestamp: int, secret: str, payload: bytes) -> str:
    """Helper to generate a valid Stripe-Signature header string."""
    sig = _compute_expected_signature(
        timestamp=timestamp,
        payload=payload,
        secret=secret,
    )
    return f"t={timestamp},v1={sig}"


# ============================================================================
# SUCCESSFUL VERIFICATION TESTS
# ============================================================================

def test_verify_stripe_signature_valid():
    """Test verification passes with valid timestamp, secret, and signature."""
    now = int(time.time())
    header = build_stripe_header(now, SECRET, PAYLOAD)

    result = verify_stripe_signature(
        payload=PAYLOAD,
        sig_header=header,
        secret=SECRET,
    )

    assert result["id"] == "evt_test"
    assert result["type"] == "payment_intent.succeeded"


def test_verify_stripe_signature_multiple_v1_key_rotation():
    """Test verification succeeds when multiple v1 signatures exist (key rotation)."""
    now = int(time.time())
    valid_sig = _compute_expected_signature(
        timestamp=now, payload=PAYLOAD, secret=SECRET
    )
    header = f"t={now},v1=old_invalid_signature,v1={valid_sig}"

    result = verify_stripe_signature(
        payload=PAYLOAD,
        sig_header=header,
        secret=SECRET,
    )

    assert result["id"] == "evt_test"


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

def test_verify_stripe_signature_missing_header():
    """Test error raised when signature header is empty."""
    with pytest.raises(SignatureVerificationError, match="Missing Stripe-Signature header"):
        verify_stripe_signature(payload=PAYLOAD, sig_header="", secret=SECRET)


def test_verify_stripe_signature_missing_secret():
    """Test error raised when secret is missing."""
    now = int(time.time())
    header = build_stripe_header(now, SECRET, PAYLOAD)
    
    with pytest.raises(SignatureVerificationError, match="not configured"):
        verify_stripe_signature(payload=PAYLOAD, sig_header=header, secret="")


def test_verify_stripe_signature_stale_timestamp():
    """Test error raised when timestamp exceeds tolerance window."""
    stale_time = int(time.time()) - 600  # 10 mins ago (tolerance default 300s)
    header = build_stripe_header(stale_time, SECRET, PAYLOAD)

    with pytest.raises(SignatureVerificationError, match="outside tolerance window"):
        verify_stripe_signature(payload=PAYLOAD, sig_header=header, secret=SECRET)


def test_verify_stripe_signature_invalid_signature():
    """Test error raised when payload/signature mismatch."""
    now = int(time.time())
    header = f"t={now},v1=invalid_hmac_signature"

    with pytest.raises(SignatureVerificationError, match="Invalid signature"):
        verify_stripe_signature(payload=PAYLOAD, sig_header=header, secret=SECRET)


def test_verify_stripe_signature_malformed_json_payload():
    """Test error raised when valid signature points to invalid JSON."""
    invalid_json = b"{bad_json:"
    now = int(time.time())
    header = build_stripe_header(now, SECRET, invalid_json)

    with pytest.raises(SignatureVerificationError, match="Invalid JSON"):
        verify_stripe_signature(payload=invalid_json, sig_header=header, secret=SECRET)


def test_parse_signature_header_invalid_format():
    """Test parsing header without timestamp raises expected error."""
    header = "v1=some_sig_without_timestamp"
    with pytest.raises(SignatureVerificationError, match="Missing timestamp"):
        _parse_signature_header(header)