from datetime import datetime
from pydantic import BaseModel


class ProviderBreakdown(BaseModel):
    provider: str
    received: int
    success: int
    dead_lettered: int
    unverified_count: int 


class LatencyStats(BaseModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_size: int 

class RetrySweepStats(BaseModel):
    total_celery_retries: int
    total_sweep_reenqueues: int
    currently_pending: int       
    currently_processing: int 


class RateLimitStats(BaseModel):
    rejections_last_hour: int
    rejections_last_24h: int


class DLQStats(BaseModel):
    total_unresolved: int
    total_resolved: int
    oldest_unresolved_age_seconds: int | None 

class MetricsSummary(BaseModel):
    generated_at: datetime

    total_received: int
    total_success: int
    total_dead_lettered: int

    by_provider: list[ProviderBreakdown]
    latency: LatencyStats
    retry_sweep: RetrySweepStats
    rate_limit: RateLimitStats
    dlq: DLQStats