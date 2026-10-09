# ============================================
# BASE EXCEPTION
# ============================================
class WebhookProcessorError(Exception):
    pass


# ============================================
# CATEGORY 1: WEBHOOK INGESTION ERRORS
# ============================================

class WebhookIngestionError(WebhookProcessorError):
    pass


class SignatureVerificationError(WebhookIngestionError):
    pass


class IdempotencyKeyMissingError(WebhookIngestionError):
    pass


class DuplicateEventConflict(WebhookIngestionError):
    pass


class InvalidPayloadError(WebhookIngestionError):
    pass


class RateLimitExceededError(WebhookIngestionError):
    pass

class AuthenticationError(WebhookProcessorError):
    pass

# ============================================
# CATEGORY 2: PROCESSING ERRORS
# ============================================

class ProcessingError(WebhookProcessorError):
    """Base class for errors during webhook processing."""
    pass


class TransientProcessingError(ProcessingError):
    pass


class PermanentProcessingError(ProcessingError):
    pass


# ============================================
# CATEGORY 3: SYSTEM ERRORS
# ============================================

class SystemError(WebhookProcessorError):
    pass


class DatabaseUnavailableError(SystemError):
    pass


class RedisUnavailableError(SystemError):
    pass


# ============================================
# CATEGORY 4: DEAD LETTER QUEUE ERRORS
# ============================================
class DeadLetterError(WebhookProcessorError):
    pass


class ReplayLimitExceededError(DeadLetterError):
    pass

