"""
Application metrics (Prometheus format).

All metrics are defined here — one place to see everything the
system exposes for observability.

"""

from prometheus_client import Counter, Gauge, Histogram


# ============================================
# INGESTION METRICS
# ============================================
# These increment at the API layer — the moment a webhook arrives.
# Labels:
#   provider — "stripe" | "generic"
#   event_type — the webhook's event type (bounded cardinality)

webhook_received_total = Counter(
    "webhook_received_total",
    "Total webhooks received at the API layer.",
    labelnames=("provider", "event_type"),
)

webhook_duplicate_total = Counter(
    "webhook_duplicate_total",
    "Total webhook duplicates detected (Redis fast-path or Postgres constraint).",
    labelnames=("provider",),
)

webhook_rate_limited_total = Counter(
    "webhook_rate_limited_total",
    "Total webhook requests rejected by rate limiting.",
    labelnames=("provider",),
)


# ============================================
# PROCESSING METRICS
# ============================================
# These increment inside the Celery task — the moment processing runs.
# Labels:
#   provider — "stripe" | "generic"
#   event_type — the webhook's event type

webhook_processed_success_total = Counter(
    "webhook_processed_success_total",
    "Total webhooks processed successfully.",
    labelnames=("provider", "event_type"),
)

webhook_processed_failure_total = Counter(
    "webhook_processed_failure_total",
    "Total webhooks that reached a terminal failure.",
    labelnames=("provider", "event_type", "error_category"),
)

webhook_retries_total = Counter(
    "webhook_retries_total",
    "Total retry attempts scheduled by Celery workers.",
    labelnames=("provider", "error_category"),
)

webhook_processing_duration_seconds = Histogram(
    "webhook_processing_duration_seconds",
    "Time spent processing a webhook (seconds).",
    labelnames=("provider", "event_type"),
    # Buckets tuned for webhook processing.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# ============================================
# STATE METRICS
# ============================================

webhook_dlq_size = Gauge(
    "webhook_dlq_size",
    "Number of unresolved entries in the dead-letter queue.",
)


# ============================================
# METRIC HELPERS
# ============================================
def record_webhook_received(provider: str, event_type: str) -> None:
    webhook_received_total.labels(provider=provider, event_type=event_type).inc()


def record_webhook_duplicate(provider: str) -> None:
    webhook_duplicate_total.labels(provider=provider).inc()


def record_webhook_success(provider: str, event_type: str, duration_seconds: float) -> None:
    webhook_processed_success_total.labels(
        provider=provider, event_type=event_type
    ).inc()
    webhook_processing_duration_seconds.labels(
        provider=provider, event_type=event_type
    ).observe(duration_seconds)


def record_webhook_failure(
    provider: str, event_type: str, error_category: str, duration_seconds: float
) -> None:
    webhook_processed_failure_total.labels(
        provider=provider, event_type=event_type, error_category=error_category
    ).inc()
    webhook_processing_duration_seconds.labels(
        provider=provider, event_type=event_type
    ).observe(duration_seconds)


def record_webhook_retry(provider: str, error_category: str) -> None:
    webhook_retries_total.labels(
        provider=provider, error_category=error_category
    ).inc()


def update_dlq_size(size: int) -> None:
    webhook_dlq_size.set(size)