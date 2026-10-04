import type { MetricsSummary } from "./types";

export const METRICS_POLL_INTERVAL_MS = 5000;

export class MetricsFetchError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "MetricsFetchError";
    this.status = status;
  }
}

export async function fetchMetricsSummary(): Promise<MetricsSummary> {
  const res = await fetch("/api/metrics", { cache: "no-store" });

  if (!res.ok) {
    throw new MetricsFetchError(`Metrics proxy returned ${res.status}`, res.status);
  }

  return (await res.json()) as MetricsSummary;
}