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

from app.core.exceptions import (
    TransientProcessingError,
    PermanentProcessingError,
)
from app.core.logging import get_logger
from app.models.db import WebhookReceipt


logger = get_logger(__name__)


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

        # ---- TEST HOOK: explicit failure simulation ----
        # Payload can opt-in to a failure path:
        #   {"simulate_failure": "transient"} → retry / DLQ (transient)
        #   {"simulate_failure": "permanent"} → direct DLQ (permanent)
        #
        # This replaces the previous random-roll simulation
        # (SIMULATED_TRANSIENT_FAILURE_RATE = 1.0), which caused every
        # webhook to fail in production.
        #
        # Safe to leave in: real payloads never include this field.
        # Remove the block when real business logic lands.
        simulate = None
        if isinstance(receipt.payload, dict):
            simulate = receipt.payload.get("simulate_failure")

        if simulate == "transient":
            raise TransientProcessingError(
                "Simulated transient failure (network timeout)"
            )
        if simulate == "permanent":
            raise PermanentProcessingError(
                "Simulated permanent failure (insufficient funds)"
            )
        # ---- END TEST HOOK ----

        # ---- REAL LOGIC GOES HERE ----
        # When real business logic lands, dispatch per provider:
        #
        # if receipt.provider == ProviderType.STRIPE:
        #     return await self._handle_stripe(receipt)
        # return await self._handle_generic(receipt)
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