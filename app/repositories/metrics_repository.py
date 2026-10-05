from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.webhook_receipt import WebhookReceipt
from app.models.db.enums import WebhookStatus
from app.models.db.dead_letter_queue import DeadLetterQueue
from app.schemas.metrics import (
    ProviderBreakdown, LatencyStats, RetrySweepStats, DLQStats, RateLimitStats,
)


async def get_total_counts(db: AsyncSession) -> tuple[int, int, int]:
    stmt = select(
        func.count().label("total_received"),
        func.count().filter(WebhookReceipt.status == WebhookStatus.SUCCESS).label("total_success"),
        func.count().filter(WebhookReceipt.status == WebhookStatus.DEAD_LETTERED).label("total_dead_lettered"),
    )
    row = (await db.execute(stmt)).one()
    return row.total_received, row.total_success, row.total_dead_lettered


async def get_provider_breakdown(db: AsyncSession) -> list[ProviderBreakdown]:
    stmt = (
        select(
            WebhookReceipt.provider,
            func.count().label("received"),
            func.count().filter(WebhookReceipt.status == WebhookStatus.SUCCESS).label("success"),
            func.count().filter(WebhookReceipt.status == WebhookStatus.DEAD_LETTERED).label("dead_lettered"),
            func.count().filter(WebhookReceipt.verified.is_(False)).label("unverified_count"),
        )
        .group_by(WebhookReceipt.provider)
    )
    rows = (await db.execute(stmt)).all()
    return [
        ProviderBreakdown(
            provider=row.provider.value,
            received=row.received,
            success=row.success,
            dead_lettered=row.dead_lettered,
            unverified_count=row.unverified_count,
        )
        for row in rows
    ]


async def get_latency_stats(db: AsyncSession) -> LatencyStats:
    duration_ms = (
        func.extract("epoch", WebhookReceipt.completed_at - WebhookReceipt.processing_started_at) * 1000
    )

    stmt = select(
        func.percentile_cont(0.50).within_group(duration_ms).label("p50"),
        func.percentile_cont(0.95).within_group(duration_ms).label("p95"),
        func.percentile_cont(0.99).within_group(duration_ms).label("p99"),
        func.count().label("sample_size"),
    ).where(
        WebhookReceipt.status == WebhookStatus.SUCCESS,
        WebhookReceipt.processing_started_at.isnot(None),
        WebhookReceipt.completed_at.isnot(None),
    )
    row = (await db.execute(stmt)).one()

    return LatencyStats(
        p50_ms=row.p50 or 0.0,
        p95_ms=row.p95 or 0.0,
        p99_ms=row.p99 or 0.0,
        sample_size=row.sample_size,
    )


async def get_retry_sweep_stats(db: AsyncSession) -> RetrySweepStats:
    stmt = select(
        func.coalesce(func.sum(WebhookReceipt.celery_retry_count), 0).label("total_celery_retries"),
        func.coalesce(func.sum(WebhookReceipt.sweep_attempts), 0).label("total_sweep_reenqueues"),
        func.count().filter(WebhookReceipt.status == WebhookStatus.PENDING).label("currently_pending"),
        func.count().filter(WebhookReceipt.status == WebhookStatus.PROCESSING).label("currently_processing"),
    )
    row = (await db.execute(stmt)).one()
    return RetrySweepStats(
        total_celery_retries=row.total_celery_retries,
        total_sweep_reenqueues=row.total_sweep_reenqueues,
        currently_pending=row.currently_pending,
        currently_processing=row.currently_processing,
    )


# ==== Failure Mode ==== #

# async def get_dlq_stats(db: AsyncSession) -> DLQStats:
#     stmt = select(
#         func.count().filter(DeadLetterQueue.resolved.is_(False)).label("total_unresolved"),
#         func.count().filter(DeadLetterQueue.resolved.is_(True)).label("total_resolved"),
#         func.min(DeadLetterQueue.failed_at).filter(DeadLetterQueue.resolved.is_(False)).label("oldest_unresolved"),
#     )
#     row = (await db.execute(stmt)).one()
#
#     age_seconds = None
#     if row.oldest_unresolved is not None:
#         age_seconds = int((func.now() - row.oldest_unresolved).total_seconds()) \
#             if hasattr(row.oldest_unresolved, "total_seconds") else None
#         # computed properly in the service layer instead — see note below
#
#     return DLQStats(
#         total_unresolved=row.total_unresolved,
#         total_resolved=row.total_resolved,
#         oldest_unresolved_age_seconds=None,  # placeholder, fixed in service layer
#     )


async def get_dlq_stats(db: AsyncSession) -> DLQStats:
    stmt = select(
        func.count().filter(DeadLetterQueue.resolved.is_(False)).label("total_unresolved"),
        func.count().filter(DeadLetterQueue.resolved.is_(True)).label("total_resolved"),
        func.extract(
            "epoch",
            func.now() - func.min(DeadLetterQueue.failed_at).filter(DeadLetterQueue.resolved.is_(False)),
        ).label("oldest_unresolved_age_seconds"),
    )
    row = (await db.execute(stmt)).one()

    return DLQStats(
        total_unresolved=row.total_unresolved,
        total_resolved=row.total_resolved,
        oldest_unresolved_age_seconds=(
            int(row.oldest_unresolved_age_seconds)
            if row.oldest_unresolved_age_seconds is not None
            else None
        ),
    )


async def get_rate_limit_stats(redis, provider_list: list[str]) -> RateLimitStats:
    import time
    current_hour = int(time.time() // 3600)

    async def sum_hours(hours_back: int) -> int:
        keys = [
            f"ratelimit:rejections:{provider}:{current_hour - h}"
            for provider in provider_list
            for h in range(hours_back)
        ]
        if not keys:
            return 0
        values = await redis.mget(keys)
        return sum(int(v) for v in values if v is not None)

    return RateLimitStats(
        rejections_last_hour=await sum_hours(1),
        rejections_last_24h=await sum_hours(24),
    )