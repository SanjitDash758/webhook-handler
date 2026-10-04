"""
Celery application configuration.

This module creates the Celery app instance used by all tasks.

"""
from app.core.metrics_bootstrap import setup_multiprocess_dir

setup_multiprocess_dir()
from celery import Celery
from kombu import Queue
from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)


# ============================================
# APP INSTANCE
# ============================================
celery_app = Celery(
    "webhook_handler",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.process_webhook",
        "app.workers.tasks.sweep",
    ],
)


# ============================================
# SERIALIZATION
# ============================================
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],        # reject pickle, yaml, msgpack
    result_accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


# ============================================
# TASK EXECUTION LIMITS
# ============================================
celery_app.conf.update(
    # Hard limit: worker is killed (SIGKILL) if the task runs this long.
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,           # 300s

    # Soft limit: a SoftTimeLimitExceeded exception is raised inside
    # the task, giving it a chance to clean up. Fires slightly before
    # the hard limit.
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT, # 280s

    # Track task start/finish for observability. Slight overhead.
    task_track_started=True,
)


# ============================================
# RETRY DEFAULTS (per-task overrides still possible)
# ============================================
celery_app.conf.update(
    task_default_retry_delay=settings.CELERY_RETRY_BASE_DELAY,  # 60s
    task_max_retries=settings.CELERY_MAX_RETRIES,               # 5
)


# ============================================
# WORKER BEHAVIOR
# ============================================
celery_app.conf.update(
    # Acknowledge a task only AFTER it succeeds (or raises).
    # Default is to ack BEFORE execution, which loses the task if
    # the worker crashes mid-execution.
    task_acks_late=True,

    # If a worker crashes while holding a task, another worker
    # picks it up after this many seconds. Prevents infinite
    # redelivery if a task always crashes the worker.
    task_reject_on_worker_lost=True,

    # Prefetch: how many tasks a worker holds in memory waiting to run.
    # Default is 4× concurrency, which starves other workers for long tasks.
    # 1 is safe and predictable.
    worker_prefetch_multiplier=1,

    # Send task events (for monitoring with Flower). Off by default.
    worker_send_task_events=False,

    # Restart pool workers periodically to mitigate memory leaks.
    # 0 = never restart. We set 50 as a compromise.
    worker_max_tasks_per_child=50,
)


# ============================================
# RESULT BACKEND BEHAVIOR
# ============================================
celery_app.conf.update(
    # Results expire after 1 hour. Our webhooks complete in seconds;
    # keeping results longer is wasted Redis memory.
    result_expires=3600,

    # Do NOT store the return value of tasks by default — most are
    # fire-and-forget. Individual tasks can override with `ignore_result=False`.
    task_ignore_result=True,
)


# ============================================
# BROKER / BACKEND CONNECTION
# ============================================
celery_app.conf.update(
    # Retry connecting to the broker at startup instead of crashing.
    broker_connection_retry_on_startup=True,

    # Reconnect if the broker connection drops mid-run.
    broker_connection_retry=True,

    # Cap on reconnect attempts. Beyond this, the worker exits and
    # relies on the process supervisor (systemd, supervisor, k8s) to restart it.
    broker_connection_max_retries=10,
)


# ============================================
# QUEUE DEFINITION
# ============================================
celery_app.conf.task_queues = (
    Queue("default", routing_key="default"),
)


# ============================================
# ROUTING
# ============================================
# Every task goes to 'default' unless overridden.
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_default_exchange = "default"
celery_app.conf.task_default_routing_key = "default"


# ============================================
# BEAT SCHEDULE (periodic tasks)
# ============================================
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "reconciliation-sweep": {
        # Reference the task by its registered name (matches @celery_app.task(name=...)).
        "task": "app.workers.tasks.sweep.reconciliation_sweep",
        # Run every 5 minutes.
        # This value should match settings.SWEEP_INTERVAL_SECONDS
        # (300s = 5min) but Celery Beat's schedule syntax is time-based.
        "schedule": crontab(minute="*/5"),
    },
}


# ============================================
# SANITY LOG
# ============================================
logger.info(
    f"Celery configured: broker={settings.CELERY_BROKER_URL.split('@')[-1]}, "
    f"backend={settings.CELERY_RESULT_BACKEND.split('@')[-1]}"
)