"""
Stripe webhook ingestion endpoint.

Boundary layer: HTTP in → domain calls → HTTP out.
No business logic; no side effects outside services.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import (
    DuplicateEventConflict,
    SignatureVerificationError,
)
from app.core.logging import get_logger
from app.core.metrics import record_webhook_duplicate, record_webhook_received
from app.models.db.enums import ProviderType
from app.repositories.unit_of_work import UnitOfWork, get_uow
from app.services.stripe_verifier import verify_stripe_signature
from app.services.webhook_service import WebhookIngestionService

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/stripe", tags=["webhooks"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a Stripe webhook",
    responses={
        202: {"description": "Accepted for processing"},
        200: {"description": "Duplicate; cached response returned"},
        400: {"description": "Invalid signature or payload"},
        409: {"description": "Idempotency key reused for a different event"},
    },
)
async def receive_stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="Stripe-Signature"),
    uow: UnitOfWork = Depends(get_uow),
) -> JSONResponse:
    """Receive, verify, and enqueue a Stripe webhook."""

    # ---- 1. Raw body ----
    raw_body: bytes = await request.body()
    if not raw_body:
        logger.warning("Rejected empty body")
        raise HTTPException(status_code=400, detail="Empty request body")

    # ---- 2. Verify signature ----
    try:
        event = verify_stripe_signature(
            payload=raw_body,
            sig_header=stripe_signature,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except SignatureVerificationError as exc:
        logger.warning(f"Stripe signature verification failed: {exc}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # ---- 3. Extract identifiers ----
    event_id = event.get("id")
    event_type = event.get("type")

    if not isinstance(event_id, str) or not event_id:
        record_webhook_received(provider="stripe", event_type=event_type)
        logger.warning("Stripe event missing or invalid 'id'")
        raise HTTPException(status_code=400, detail="Missing event id")

    if not isinstance(event_type, str) or not event_type:
        logger.warning(f"Stripe event {event_id} missing or invalid 'type'")
        raise HTTPException(status_code=400, detail="Missing event type")

    idempotency_key = event_id

    # ---- 4. Service execution ----
    service = WebhookIngestionService(uow=uow)
    try:
        result = await service.ingest(
            provider=ProviderType.STRIPE,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=event,
            verified=True,
        )
    except DuplicateEventConflict as exc:
        logger.warning(f"Duplicate event conflict for Stripe event {event_id}: {exc}")
        raise HTTPException(status_code=409, detail=str(exc))

    # ---- 5. Map result to HTTP response ----
    if result.is_new:
        body = {
            "status": "accepted",
            "receipt_id": str(result.receipt_id),
            "event_id": event_id,
            "event_type": event_type,
        }
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)

    record_webhook_duplicate(provider="stripe")
    body = result.response_payload or {
        "status": "accepted",
        "receipt_id": str(result.receipt_id),
        "event_id": event_id,
    }

    status_code = getattr(result, "status_code", None)
    if not status_code or status_code == 202:
        status_code = status.HTTP_200_OK

    return JSONResponse(status_code=status_code, content=body)