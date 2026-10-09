"""
Database model registry.

"""

from app.models.db.enums import (
    ProviderType,
    WebhookStatus,
    ErrorCategory,
)
from app.models.db.webhook_receipt import WebhookReceipt
from app.models.db.dead_letter_queue import DeadLetterQueue


__all__ = [
    # Enums
    "ProviderType",
    "WebhookStatus",
    "ErrorCategory",
    # Tables
    "WebhookReceipt",
    "DeadLetterQueue",
]