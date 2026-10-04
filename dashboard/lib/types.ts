export interface ProviderBreakdown {
  provider: string;
  received: number;
  success: number;
  dead_lettered: number;
  unverified_count: number;
}

export interface LatencyStats {
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  sample_size: number;
}

export interface RetrySweepStats {
  total_celery_retries: number;
  total_sweep_reenqueues: number;
  currently_pending: number;
  currently_processing: number;
}

export interface RateLimitStats {
  rejections_last_hour: number;
  rejections_last_24h: number;
}

export interface DLQStats {
  total_unresolved: number;
  total_resolved: number;
  oldest_unresolved_age_seconds: number | null;
}

export interface MetricsSummary {
  generated_at: string; // ISO 8601 — Pydantic's datetime serializes to a string over JSON, not a Date
  total_received: number;
  total_success: number;
  total_dead_lettered: number;
  by_provider: ProviderBreakdown[];
  latency: LatencyStats;
  retry_sweep: RetrySweepStats;
  rate_limit: RateLimitStats;
  dlq: DLQStats;
}