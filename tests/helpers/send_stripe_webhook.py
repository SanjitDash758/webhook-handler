from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
import httpx

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
      pass

# Make `app` importable when run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings


ENDPOINT = "http://localhost:8000/webhooks/stripe"


# A realistic minimal Stripe event. Stripe's real payloads are
# much bigger, but the shape is what matters for verification.
def build_event() -> dict:
    return {
        "id": f"evt_test_{int(time.time() * 1000)}",
        "object": "event",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_test_12345",
                "object": "payment_intent",
                "amount": 1000,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {"user_id": "user_test_001"},
            }
        },
    }


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    """Compute the Stripe-Signature header value for a payload."""
    signed = str(timestamp).encode("utf-8") + b"." + payload
    signature = hmac.new(
        key=secret.encode(),
        msg=signed,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Stripe webhook.")
    parser.add_argument(
        "mode",
        choices=["valid", "invalid", "stale"],
        help="Which signature to send.",
    )
    args = parser.parse_args()

    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret or secret.startswith("whsec_replace"):
        print("ERROR: STRIPE_WEBHOOK_SECRET is not set in .env", file=sys.stderr)
        return 2

    event = build_event()
    payload = json.dumps(event, separators=(",", ":")).encode()

    # Timestamp: now for valid/invalid, one hour ago for stale.
    timestamp = int(time.time()) if args.mode != "stale" else int(time.time()) - 3600

    # Signature secret: real one for valid/stale, wrong one for invalid.
    sign_secret = secret if args.mode != "invalid" else "whsec_wrong_secret"
    signature_header = sign(payload, sign_secret, timestamp)

    headers = {
        "Content-Type": "application/json",
        "Stripe-Signature": signature_header,
    }

    print(f"Mode:      {args.mode}")
    print(f"Event ID:  {event['id']}")
    print(f"Timestamp: {timestamp} ({'now' if args.mode != 'stale' else '1h ago'})")
    print(f"Endpoint:  {ENDPOINT}")
    print()

    with httpx.Client(timeout=30) as client:
        response = client.post(ENDPOINT, content=payload, headers=headers)

    print(f"HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)

    # Expected-status check for CI-style use.
    expected = {"valid": 202, "invalid": 400, "stale": 400}[args.mode]
    if response.status_code != expected:
        print(
            f"\nUNEXPECTED: expected {expected}, got {response.status_code}",
            file=sys.stderr,
        )
        return 1

    print(f"\nOK - as expected ({expected})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())