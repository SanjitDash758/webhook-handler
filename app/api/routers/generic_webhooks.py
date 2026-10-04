"""
Generic webhook ingestion endpoint.

For callers that cannot sign their requests. Lower trust tier.

Contract:
    POST /webhooks/generic
    Headers:
        Idempotency-Key: <opaque, unique per logical event>   (required)
        X-Event-Type:    <dot.notation.event.type>            (required)
        Content-Type:    application/json
    Body:
        any JSON object

Response codes:
    202 Accepted             — new webhook, queued
    200 OK                   — duplicate; cached response returned
    400 Bad Request          — missing/invalid headers, malformed JSON
    409 Conflict             — idempotency key reused for a different event
    429 Too Many Requests    — rate limited (middleware)
    500 Internal Server Error — unexpected

Because there is no signature, receipts created here are marked
verified=False. Ops can query for them and treat them as lower-trust.
"""

from __future__ import annotations
import json
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from app.core.exceptions import DuplicateEventConflict
from app.core.logging import get_logger
from app.models.db.enums import ProviderType
from app.repositories.unit_of_work import UnitOfWork, get_uow
from app.services.webhook_service import WebhookIngestionService
from app.core.metrics import record_webhook_received, record_webhook_duplicate

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/generic", tags=["webhooks"])


# Reasonable bounds. Prevent storage abuse and log pollution.
MAX_IDEMPOTENCY_KEY_LENGTH = 255
MAX_EVENT_TYPE_LENGTH = 100


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a generic (unsigned) webhook",
    responses={
        202: {"description": "Accepted for processing"},
        200: {"description": "Duplicate; cached response returned"},
        400: {"description": "Missing/invalid headers or malformed JSON"},
        409: {"description": "Idempotency key reused for a different event"},
    },
)
async def receive_generic_webhook(
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    event_type: str = Header(..., alias="X-Event-Type"),
    uow: UnitOfWork = Depends(get_uow),
) -> JSONResponse:

    # ---- 1. Validate headers ----
    idempotency_key = idempotency_key.strip()
    event_type = event_type.strip()

    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is empty")

    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Idempotency-Key exceeds {MAX_IDEMPOTENCY_KEY_LENGTH} chars",
        )

    if not event_type:
        raise HTTPException(status_code=400, detail="X-Event-Type is empty")

    if len(event_type) > MAX_EVENT_TYPE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"X-Event-Type exceeds {MAX_EVENT_TYPE_LENGTH} chars",
        )

    # ---- 2. Read and parse body ----
    raw_body: bytes = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning(f"Invalid JSON in generic webhook: {exc}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}")

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object",
        )
    # Record that a valid generic webhook arrived.
    record_webhook_received(provider="generic", event_type=event_type)

    # ---- 3. Hand off to service ----
    service = WebhookIngestionService(uow=uow)
    try:
        result = await service.ingest(
            provider=ProviderType.GENERIC,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            verified=False,
        )
    except DuplicateEventConflict as exc:
        logger.warning(
            f"Duplicate conflict: key={idempotency_key[:16]}... reason={exc}"
        )
        raise HTTPException(status_code=409, detail=str(exc))

    # ---- 4. Respond ----
    if not result.is_new:
        record_webhook_duplicate(provider="generic")

    body = result.response_payload or {
        "status": "accepted",
        "receipt_id": str(result.receipt_id),
        "event_type": event_type,
    }
    return JSONResponse(status_code=result.status_code, content=body)