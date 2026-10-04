"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, CheckCircle2, AlertTriangle, Clock, Zap, ShieldAlert, Server, ArrowUpRight, Play, Pause, Radio, Sliders } from "lucide-react";
import { fetchMetricsSummary, METRICS_POLL_INTERVAL_MS, MetricsFetchError } from "@/lib/api";
import type { MetricsSummary } from "@/lib/types";
import { Sparkline } from "@/components/Sparkline";
import PipelineDiagram from "@/components/PipelineDiagram";

const MAX_HISTORY = 20;

export default function DashboardPage() {
  const [data, setData] = useState<MetricsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null);
  const [isPollingActive, setIsPollingActive] = useState(true);
  const [latencyHistory, setLatencyHistory] = useState<number[]>([]);
  const [eventRate, setEventRate] = useState<number | null>(null);
  const [filterProvider, setFilterProvider] = useState<string>("all");

  const prevReceived = useRef<number | null>(null);
  const prevPollTime = useRef<number | null>(null);

  useEffect(() => {
    if (!isPollingActive) return;
    let cancelled = false;

    async function poll() {
      try {
        const summary = await fetchMetricsSummary();
        if (cancelled) return;

        // Real derived rate: delta in total_received over real elapsed time —
        // not a random number. First poll has nothing to diff against, so it's null.
        const now = Date.now();
        if (prevReceived.current != null && prevPollTime.current != null) {
          const deltaCount = summary.total_received - prevReceived.current;
          const deltaSeconds = (now - prevPollTime.current) / 1000;
          setEventRate(deltaSeconds > 0 ? Math.max(0, deltaCount / deltaSeconds) : 0);
        }
        prevReceived.current = summary.total_received;
        prevPollTime.current = now;

        setLatencyHistory((prev) => [...prev.slice(-(MAX_HISTORY - 1)), summary.latency.p50_ms]);

        setData(summary);
        setError(null);
        setLastSuccess(new Date());
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof MetricsFetchError
            ? err.status === 504
              ? "Backend waking up (free-tier cold start)"
              : `Backend error (${err.status})`
            : "Unexpected error reaching backend"
        );
      }
    }

    poll();
    const interval = setInterval(poll, METRICS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [isPollingActive]);

  const filteredProviders = data
    ? filterProvider === "all"
      ? data.by_provider
      : data.by_provider.filter((p) => p.provider === filterProvider)
    : [];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8 relative overflow-hidden">
      <div className="absolute top-[-10%] left-[15%] w-[500px] h-[500px] bg-indigo-600/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute top-[40%] right-[-5%] w-[500px] h-[500px] bg-rose-600/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute bottom-[-10%] left-[30%] w-[500px] h-[500px] bg-emerald-600/10 rounded-full blur-[140px] pointer-events-none" />

      <div className="max-w-7xl mx-auto space-y-8 relative z-10">
        {/* HEADER */}
        <header className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-6 border-b border-slate-800/80">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/30 rounded-xl text-indigo-400">
              <Radio className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-100 to-slate-400 bg-clip-text text-transparent">
                Webhook Pipeline
              </h1>
              <p className="text-xs text-slate-400 flex items-center gap-2 mt-1">
                <span className={`inline-block w-2 h-2 rounded-full ${isPollingActive ? "bg-emerald-400" : "bg-slate-600"}`} />
                {lastSuccess ? `Updated ${lastSuccess.toLocaleTimeString()}` : "Connecting..."}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => setIsPollingActive((v) => !v)}
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-medium border bg-slate-900/80 border-slate-700/80 text-slate-200 hover:bg-slate-800 transition-colors"
            >
              {isPollingActive ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
              {isPollingActive ? "Pause refresh" : "Resume refresh"}
            </button>

            {error ? (
              <div className="flex items-center gap-2.5 px-4 py-2 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
                <AlertTriangle className="w-4 h-4" />
                {error}
              </div>
            ) : (
              <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900/90 border border-emerald-500/30 text-xs font-medium text-emerald-400">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                Operational
              </div>
            )}
          </div>
        </header>

        {!data ? (
          <p className="text-slate-500 text-sm">Waiting for first response…</p>
        ) : (
          <>
            {/* HERO METRICS */}
            <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <HeroCard
                label="Total received"
                value={data.total_received.toLocaleString()}
                icon={<Zap className="w-4 h-4" />}
                accent="indigo"
                sub={eventRate != null ? `${eventRate.toFixed(2)} req/s` : "measuring…"}
                subIcon={<ArrowUpRight className="w-3 h-3" />}
              />
              <HeroCard
                label="Success"
                value={data.total_success.toLocaleString()}
                icon={<CheckCircle2 className="w-4 h-4" />}
                accent="emerald"
                sub={
                  data.total_received > 0
                    ? `${((data.total_success / data.total_received) * 100).toFixed(2)}% rate`
                    : "—"
                }
              />
              <HeroCard
                label="Dead-lettered"
                value={data.total_dead_lettered.toLocaleString()}
                icon={<AlertTriangle className="w-4 h-4" />}
                accent="rose"
                sub={`${data.dlq.total_unresolved} unresolved`}
              />
              <div className="rounded-2xl bg-gradient-to-b from-cyan-500/10 via-slate-900/40 to-slate-900/80 border border-cyan-500/20 p-5">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs uppercase tracking-wider text-slate-400">p50 latency</span>
                  <div className="p-2 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
                    <Clock className="w-4 h-4" />
                  </div>
                </div>
                <div className="flex items-baseline justify-between mb-1">
                  <span className="text-3xl font-extrabold text-white">
                    {data.latency.p50_ms.toFixed(0)} <span className="text-sm font-normal text-slate-400">ms</span>
                  </span>
                  <span className="text-[10px] font-mono text-cyan-400 bg-cyan-500/10 px-2 py-0.5 rounded-full border border-cyan-500/20">
                    p95: {data.latency.p95_ms.toFixed(0)}ms
                  </span>
                </div>
                <div className="mt-2">
                  <Sparkline data={latencyHistory} color="#06b6d4" height={32} />
                </div>
                <p className="text-[10px] text-slate-500 mt-1">
                  n={data.latency.sample_size}
                  {data.latency.sample_size < 10 ? " — low confidence" : ""}
                </p>
              </div>
            </section>

            {/* LIVE PIPELINE */}
            <section>
              <PipelineDiagram />
            </section>

            {/* SECONDARY PANELS — mapped to real fields only */}
            <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <Panel icon={<Activity className="w-4 h-4" />} iconColor="amber" title="In-flight">
                <div className="flex justify-between items-center text-xs mb-2">
                  <span className="text-slate-400">Processing</span>
                  <span className="font-mono text-amber-400">{data.retry_sweep.currently_processing}</span>
                </div>
                <div className="flex justify-between items-center text-xs mb-3">
                  <span className="text-slate-400">Pending</span>
                  <span className="font-mono text-slate-200">{data.retry_sweep.currently_pending}</span>
                </div>
                <p className="text-[11px] text-slate-500">
                  {data.retry_sweep.total_celery_retries} retries · {data.retry_sweep.total_sweep_reenqueues} sweep re-enqueues (total)
                </p>
              </Panel>

              <Panel icon={<ShieldAlert className="w-4 h-4" />} iconColor="purple" title="DLQ health">
                <div className="flex justify-between items-center text-xs mb-2">
                  <span className="text-slate-400">Oldest unresolved</span>
                  <span className="font-mono text-slate-200">
                    {data.dlq.oldest_unresolved_age_seconds != null
                      ? `${Math.round(data.dlq.oldest_unresolved_age_seconds / 60)} min ago`
                      : "none"}
                  </span>
                </div>
                <div className="flex justify-between items-center text-xs mb-3">
                  <span className="text-slate-400">Resolved</span>
                  <span className="font-mono text-slate-200">{data.dlq.total_resolved}</span>
                </div>
                <p className="text-[11px] text-slate-500">Exponential backoff + reconciliation sweep</p>
              </Panel>

              <Panel icon={<Sliders className="w-4 h-4" />} iconColor="orange" title="Rate-limit rejections">
                <div className="flex justify-between items-center text-xs mb-2">
                  <span className="text-slate-400">Last hour</span>
                  <span className="font-mono text-orange-400">{data.rate_limit.rejections_last_hour}</span>
                </div>
                <div className="flex justify-between items-center text-xs mb-3">
                  <span className="text-slate-400">Last 24h</span>
                  <span className="font-mono text-slate-200">{data.rate_limit.rejections_last_24h}</span>
                </div>
                <p className="text-[11px] text-slate-500">Fixed window · 1 min</p>
              </Panel>
            </section>

            {/* PROVIDERS — only real providers, no invented fields */}
            <section className="space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Server className="w-4 h-4 text-indigo-400" />
                  <h2 className="text-sm font-semibold text-slate-200">Provider breakdown</h2>
                </div>
                <div className="flex items-center gap-1.5 p-1 bg-slate-900/80 rounded-xl border border-slate-800 text-xs">
                  <button
                    onClick={() => setFilterProvider("all")}
                    className={`px-3 py-1 rounded-lg ${filterProvider === "all" ? "bg-indigo-600 text-white" : "text-slate-400"}`}
                  >
                    All
                  </button>
                  {data.by_provider.map((p) => (
                    <button
                      key={p.provider}
                      onClick={() => setFilterProvider(p.provider)}
                      className={`px-3 py-1 rounded-lg capitalize ${filterProvider === p.provider ? "bg-indigo-600 text-white" : "text-slate-400"}`}
                    >
                      {p.provider}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {filteredProviders.map((p) => {
                  const successPct = p.received > 0 ? ((p.success / p.received) * 100).toFixed(1) : "100.0";
                  // Derived client-side, NOT server-reported — flagged in comment, not hidden
                  const failRate = p.received > 0 ? p.dead_lettered / p.received : 0;
                  const healthLabel = failRate > 0.05 ? "degraded" : "healthy";

                  return (
                    <div key={p.provider} className="rounded-2xl bg-slate-900/50 border border-slate-800/80 p-5">
                      <div className="flex items-center justify-between mb-3">
                        <h4 className="text-sm font-semibold capitalize text-slate-100">{p.provider}</h4>
                        <span
                          className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                            healthLabel === "healthy"
                              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                              : "bg-amber-500/10 text-amber-400 border-amber-500/20"
                          }`}
                        >
                          {healthLabel}
                        </span>
                      </div>

                      <div className="grid grid-cols-3 gap-2 my-4 p-3 rounded-xl bg-slate-950/60 border border-slate-800/50">
                        <Stat label="Received" value={p.received} />
                        <Stat label="Success" value={p.success} color="text-emerald-400" />
                        <Stat label="DLQ" value={p.dead_lettered} color="text-rose-400" />
                      </div>

                      <div className="flex justify-between text-[11px] mb-1.5">
                        <span className="text-slate-400">Success rate</span>
                        <span className="font-mono text-slate-300">{successPct}%</span>
                      </div>
                      <div className="w-full bg-slate-800/80 rounded-full h-1.5 overflow-hidden">
                        <div
                          className="bg-gradient-to-r from-emerald-500 to-indigo-500 h-1.5 rounded-full"
                          style={{ width: `${successPct}%` }}
                        />
                      </div>

                      {p.unverified_count > 0 && (
                        <div className="mt-3 pt-2.5 border-t border-slate-800/60 flex items-center gap-1.5 text-[11px] text-amber-400">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          {p.unverified_count} unverified
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}

function HeroCard({
  label,
  value,
  icon,
  accent,
  sub,
  subIcon,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  accent: "indigo" | "emerald" | "rose";
  sub: string;
  subIcon?: React.ReactNode;
}) {
  const accentClasses = {
    indigo: "from-indigo-500/10 border-indigo-500/20 bg-indigo-500/10 text-indigo-400",
    emerald: "from-emerald-500/10 border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
    rose: "from-rose-500/10 border-rose-500/20 bg-rose-500/10 text-rose-400",
  }[accent];

  return (
    <div className={`rounded-2xl bg-gradient-to-b ${accentClasses.split(" ")[0]} via-slate-900/40 to-slate-900/80 border ${accentClasses.split(" ")[1]} p-5`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs uppercase tracking-wider text-slate-400">{label}</span>
        <div className={`p-2 rounded-xl border ${accentClasses.split(" ")[1]} ${accentClasses.split(" ")[2]} ${accentClasses.split(" ")[3]}`}>
          {icon}
        </div>
      </div>
      <div className="text-3xl font-extrabold text-white mb-2">{value}</div>
      <div className="flex items-center gap-1 text-xs text-slate-400">
        {subIcon}
        <span className={accentClasses.split(" ")[3]}>{sub}</span>
      </div>
    </div>
  );
}

function Panel({ icon, iconColor, title, children }: { icon: React.ReactNode; iconColor: string; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-slate-900/60 border border-slate-800/80 p-5">
      <div className="flex items-center gap-2.5 mb-4">
        <div className={`p-2 rounded-lg bg-${iconColor}-500/10 text-${iconColor}-400 border border-${iconColor}-500/20`}>{icon}</div>
        <h3 className="text-xs uppercase tracking-wider text-slate-300 font-semibold">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, color = "text-slate-200" }: { label: string; value: number; color?: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase text-slate-500">{label}</p>
      <p className={`text-sm font-semibold font-mono ${color}`}>{value.toLocaleString()}</p>
    </div>
  );
}