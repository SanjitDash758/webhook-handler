import json
from fastapi import APIRouter, Depends

from app.schemas.metrics import MetricsSummary
from app.services.metrics_service import MetricsService
from app.core.database import get_db
from app.utils.redis_client import get_redis
from app.api.dependencies.auth import require_dashboard_access

router = APIRouter(prefix="/metrics", tags=["metrics"])

CACHE_KEY = "metrics:summary:cache"
CACHE_TTL_SECONDS = 5


@router.get("/summary", response_model=MetricsSummary)
async def get_metrics_summary(
    db=Depends(get_db),
    redis=Depends(get_redis),
    _=Depends(require_dashboard_access),
) -> MetricsSummary:
    cached = await redis.get(CACHE_KEY)
    if cached is not None:
        return MetricsSummary.model_validate_json(cached)

    service = MetricsService(db=db, redis=redis)
    summary = await service.get_summary()

    await redis.set(CACHE_KEY, summary.model_dump_json(), ex=CACHE_TTL_SECONDS)
    return summary