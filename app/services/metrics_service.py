# app/services/metrics_service.py

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.models.db.enums import ProviderType
from app.schemas.metrics import MetricsSummary
from app.repositories import metrics_repository as repo


class MetricsService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_summary(self) -> MetricsSummary:
        total_received, total_success, total_dead_lettered = await repo.get_total_counts(self.db)

        by_provider = await repo.get_provider_breakdown(self.db)
        latency = await repo.get_latency_stats(self.db)
        retry_sweep = await repo.get_retry_sweep_stats(self.db)
        dlq = await repo.get_dlq_stats(self.db)

        rate_limit = await repo.get_rate_limit_stats(
            self.redis,
            provider_list=[p.value for p in ProviderType],
        )

        return MetricsSummary(
            generated_at=datetime.now(timezone.utc),
            total_received=total_received,
            total_success=total_success,
            total_dead_lettered=total_dead_lettered,
            by_provider=by_provider,
            latency=latency,
            retry_sweep=retry_sweep,
            rate_limit=rate_limit,
            dlq=dlq,
        )