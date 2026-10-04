import uuid
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    Index,
    JSON,
    UUID,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.db.enums import (
    provider_type_enum,
    webhook_status_enum,
    error_category_enum,
)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"

    # ============================================
    # IDENTITY — Uses native PostgreSQL UUID, falls back to generic UUID for SQLite
    # ============================================
    id = Column(
        UUID().with_variant(PGUUID(as_uuid=True), "postgresql"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ============================================
    # IDEMPOTENCY KEY — the (provider, key) pair is unique
    # ============================================
    provider = Column(provider_type_enum, nullable=False)
    idempotency_key = Column(String(255), nullable=False)

    # ============================================
    # PAYLOAD — JSONB in PostgreSQL, standard JSON in SQLite
    # ============================================
    event_type = Column(String(255), nullable=False)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)

    # ============================================
    # VERIFICATION — was the signature checked?
    # ============================================
    verified = Column(Boolean, nullable=False, default=False)

    # ============================================
    # LIFECYCLE STATE
    # ============================================
    status = Column(
        webhook_status_enum,
        nullable=False,
        default="pending",
        server_default="pending",
    )

    # ============================================
    # RETRY TRACKING
    # ============================================
    celery_retry_count = Column(Integer, nullable=False, default=0)
    sweep_attempts = Column(Integer, nullable=False, default=0)
    max_sweep_attempts = Column(Integer, nullable=False, default=3)

    # ============================================
    # LAST ERROR (for DLQ forensics)
    # ============================================
    last_error = Column(Text, nullable=True)
    last_error_category = Column(error_category_enum, nullable=True)

    # ============================================
    # CACHED RESPONSE (for idempotent replay)
    # ============================================
    response_snapshot = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    # ============================================
    # TIMESTAMPS
    # ============================================
    processing_started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ============================================
    # CONSTRAINTS & INDEXES
    # ============================================
    __table_args__ = (
        # Idempotency: the same provider can't have two receipts
        # with the same key. This is the ultimate guarantee.
        UniqueConstraint(
            "provider",
            "idempotency_key",
            name="uq_webhook_receipts_provider_key",
        ),

        # Sweep query: find stuck pending receipts older than N seconds.
        # Partial index because we only ever query pending/processing rows.
        Index(
            "ix_webhook_receipts_sweep",
            "created_at",
            postgresql_where=status.in_(["pending", "processing"]),
        ),

        # Operational queries: "show me all Stripe payment_intent events today"
        Index(
            "ix_webhook_receipts_provider_event",
            "provider",
            "event_type",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookReceipt id={self.id} "
            f"provider={self.provider} "
            f"event={self.event_type} "
            f"status={self.status}>"
        )