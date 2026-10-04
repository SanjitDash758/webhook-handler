import enum
from sqlalchemy import Enum as SQLEnum


# ============================================
# PROVIDER — who sent the webhook
# ============================================
class ProviderType(str, enum.Enum):
    STRIPE = "stripe"
    GENERIC = "generic"


# ============================================
# WEBHOOK STATUS — lifecycle of a webhook
# ============================================
class WebhookStatus(str, enum.Enum):
    """
    Lifecycle states of a webhook receipt.

    Legal transitions (enforced in application logic):
        pending     → processing | dead_lettered
        processing  → success | dead_lettered
        success     → (terminal)
        dead_lettered → processing (via admin replay)

    Notes:
    - 'pending'       = received, saved, not yet picked up by Celery
    - 'processing'    = Celery task has locked the row
    - 'success'       = terminal; processing completed
    - 'dead_lettered' = terminal; permanently failed
    """
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    DEAD_LETTERED = "dead_lettered"


# ============================================
# ERROR CATEGORY — why a webhook failed
# ============================================
class ErrorCategory(str, enum.Enum):
    """
    Categorization of processing failures.

    Drives retry behavior and alerting:
    - transient: retry with backoff; usually network blips
    - permanent: never retry; business rule violation
    - unknown:   treat as transient + alert (unclassified)
    """
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


# ============================================
# SQLALCHEMY ENUM BRIDGES
# ============================================
# native_enum=False forces SQLite to store string values, 
# while PostgreSQL will still use its native enum when created via Alembic/Postgres DDL.

provider_type_enum = SQLEnum(
    ProviderType,
    name="provider_type",
    values_callable=lambda x: [e.value for e in x],
    native_enum=False,
)

webhook_status_enum = SQLEnum(
    WebhookStatus,
    name="webhook_status",
    values_callable=lambda x: [e.value for e in x],
    native_enum=False,
)

error_category_enum = SQLEnum(
    ErrorCategory,
    name="error_category",
    values_callable=lambda x: [e.value for e in x],
    native_enum=False,
)