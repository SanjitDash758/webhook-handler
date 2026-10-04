"""
Payment processing business logic.

This is the ONLY place where a webhook's payload turns into
application state changes (credits, subscriptions, notifications).

Contracts:
- process() MUST be idempotent within a single receipt.
  (The task layer guarantees it's not called twice for the same
  receipt in a terminal state, but the method should not rely on
  that — it should be safe on its own.)

- process() MUST raise:
    TransientProcessingError — retryable failure
    PermanentProcessingError — terminal failure, no retry

- process() MUST return a JSON-serializable dict on success.
  This becomes the response_snapshot on the receipt.

Currently: a stub that simulates success/failure to exercise the
retry-vs-DLQ paths. Replace the `process()` body with real logic.
"""

import random
from app.core.exceptions import (
    TransientProcessingError,
    PermanentProcessingError,
)
from app.core.logging import get_logger
from app.models.db import WebhookReceipt


logger = get_logger(__name__)


# ============================================
# SIMULATION KNOBS (delete when real logic lands)
# ============================================
SIMULATED_TRANSIENT_FAILURE_RATE = 1.0  
SIMULATED_PERMANENT_FAILURE_RATE = 0.02   


class PaymentProcessor:

    def __init__(self) -> None:
        # Future: inject HTTP client, ledger service, email service, etc.
        pass

    async def process(self, receipt: WebhookReceipt) -> dict:
        logger.info(
            f"Processing receipt: id={receipt.id} "
            f"provider={receipt.provider.value} "
            f"event_type={receipt.event_type}"
        )

        # ---- STUB: simulate transient/permanent failures ----
        # This exists so we can exercise the retry and DLQ paths
        # without real business logic. Delete when implementing.
        roll = random.random()
        if roll < SIMULATED_TRANSIENT_FAILURE_RATE:
            raise TransientProcessingError(
                "Simulated transient failure (network timeout)"
            )
        if roll < SIMULATED_TRANSIENT_FAILURE_RATE + SIMULATED_PERMANENT_FAILURE_RATE:
            raise PermanentProcessingError(
                "Simulated permanent failure (insufficient funds)"
            )
        # ---- END STUB ----

        # ---- HERE, THE REAL LOGIC GOES HERE ----
        # This is where we'd dispatch to per-provider handlers, e.g.:
        #
        # if receipt.provider == ProviderType.STRIPE:
        #     result = await self._handle_stripe(receipt)
        # else:
        #     result = await self._handle_generic(receipt)
        #
        # return result
        #
        # For now, return a fixed shape.

        result = {
            "status": "success",
            "receipt_id": str(receipt.id),
            "provider": receipt.provider.value,
            "event_type": receipt.event_type,
        }

        logger.info(f"Processed receipt: id={receipt.id} result={result}")
        return result

    # ============================================
    # PATTERN REFERENCE (unused for now)
    # ============================================
    #
    # When we implement per-provider handling, we'd follow this pattern:
    #
    # async def _handle_stripe(self, receipt) -> dict:
    #     payload = receipt.payload
    #     event_type = receipt.event_type
    #     data = payload.get("data", {}).get("object", {})
    #
    #     if event_type == "payment_intent.succeeded":
    #         return await self._credit_user(data)
    #     if event_type == "charge.refunded":
    #         return await self._debit_user(data)
    #
    #     # Unhandled event types: not an error. Return a no-op snapshot.
    #     return {"status": "ignored", "event_type": event_type}
    #
    # async def _credit_user(self, data: dict) -> dict:
    #     user_id = (data.get("metadata") or {}).get("user_id")
    #     if not user_id:
    #         # Missing metadata = bad data = permanent.
    #         raise PermanentProcessingError("Missing user_id in metadata")
    #
    #     amount_cents = data.get("amount")
    #     if not isinstance(amount_cents, int) or amount_cents <= 0:
    #         raise PermanentProcessingError(f"Invalid amount: {amount_cents!r}")
    #
    #     try:
    #         await self._ledger.credit(user_id, amount_cents)
    #     except LedgerUnavailableError as exc:
    #         # Downstream unavailable = transient.
    #         raise TransientProcessingError(str(exc)) from exc
    #     except LedgerRejectedError as exc:
    #         # Ledger refused (e.g., account frozen) = permanent.
    #         raise PermanentProcessingError(str(exc)) from exc
    #
    #     return {"status": "credited", "user_id": user_id, "amount": amount_cents}