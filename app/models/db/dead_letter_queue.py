import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    Boolean,
    TIMESTAMP,
    ForeignKey,
    Index,
    JSON,
    UUID,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.db.enums import (
    provider_type_enum,
    error_category_enum,
)


class DeadLetterQueue(Base):
    __tablename__ = "dead_letter_queue"

    # ============================================
    # IDENTITY — Cross-dialect UUID mapping
    # ============================================
    id = Column(
        UUID().with_variant(PGUUID(as_uuid=True), "postgresql"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ============================================
    # LINK BACK TO ORIGINAL RECEIPT — Cross-dialect UUID mapping
    # ============================================
    webhook_receipt_id = Column(
        UUID().with_variant(PGUUID(as_uuid=True), "postgresql"),
        ForeignKey("webhook_receipts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ============================================
    # DENORMALIZED SNAPSHOT — JSON in SQLite, JSONB in PostgreSQL
    # ============================================
    provider = Column(provider_type_enum, nullable=False)
    event_type = Column(String(255), nullable=False)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)

    # ============================================
    # WHY IT FAILED
    # ============================================
    error_message = Column(Text, nullable=False)
    error_category = Column(error_category_enum, nullable=False)

    # Retry history — how many attempts before giving up
    celery_retries_exhausted = Column(Integer, nullable=False)
    sweep_attempts_exhausted = Column(Integer, nullable=False)

    # ============================================
    # WHEN IT FAILED
    # ============================================
    failed_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ============================================
    # OPS RESOLUTION STATE
    # ============================================
    resolved = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)

    # How many times an admin has tried to replay this
    # (prevents infinite manual retry loops)
    replayed_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # ============================================
    # RELATIONSHIP
    # ============================================
    webhook_receipt = relationship("WebhookReceipt", lazy="joined")

    # ============================================
    # INDEXES
    # ============================================
    __table_args__ = (
        # The ops query: "show me unresolved failures, oldest first"
        Index(
            "ix_dlq_unresolved",
            "failed_at",
            postgresql_where=resolved.is_(False),
        ),

        # The forensic query: "what failed for Stripe recently?"
        Index(
            "ix_dlq_provider_unresolved",
            "provider",
            "failed_at",
            postgresql_where=resolved.is_(False),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DeadLetterQueue id={self.id} "
            f"provider={self.provider} "
            f"event={self.event_type} "
            f"category={self.error_category} "
            f"resolved={self.resolved}>"
        )