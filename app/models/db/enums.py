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
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    DEAD_LETTERED = "dead_lettered"


# ============================================
# ERROR CATEGORY — why a webhook failed
# ============================================
class ErrorCategory(str, enum.Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


# ============================================
# SQLALCHEMY ENUM BRIDGES
# ============================================

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