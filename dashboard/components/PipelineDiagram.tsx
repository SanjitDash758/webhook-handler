"use client";

import { useEffect, useState } from "react";
import { Layers, Globe, Database, Cpu, CheckCircle2, AlertTriangle } from "lucide-react";
import { useWebhookStream } from "@/lib/useWebhookStream";
import { PipelineStage, PIPELINE_STAGE_ORDER } from "@/lib/stages";

const ACTIVE_WINDOW_MS = 4000;

interface ActiveReceipt {
  stage: string;
  lastSeen: number;
}

// Real stages only — no invented "Ingress Gateway / Token Bucket / Autoscale"
// nodes. Icons + copy describe what each real stage actually is.
const STAGE_META: Record<string, { label: string; desc: string; icon: React.ReactNode; color: string }> = {
  [PipelineStage.RECEIVED]: { label: "Received", desc: "Signature verified, persisted", icon: <Globe className="w-4 h-4" />, color: "cyan" },
  [PipelineStage.QUEUED]: { label: "Queued", desc: "Enqueued to Celery", icon: <Database className="w-4 h-4" />, color: "cyan" },
  [PipelineStage.PROCESSING]: { label: "Processing", desc: "Worker executing", icon: <Cpu className="w-4 h-4" />, color: "amber" },
  [PipelineStage.SUCCESS]: { label: "Success", desc: "Completed", icon: <CheckCircle2 className="w-4 h-4" />, color: "emerald" },
  [PipelineStage.DEAD_LETTERED]: { label: "Dead-lettered", desc: "Retries exhausted", icon: <AlertTriangle className="w-4 h-4" />, color: "rose" },
};

export default function PipelineDiagram() {
  const { status, lastEvent } = useWebhookStream();
  const [receipts, setReceipts] = useState<Record<string, ActiveReceipt>>({});

  useEffect(() => {
    if (!lastEvent) return;
    setReceipts((prev) => ({ ...prev, [lastEvent.receipt_id]: { stage: lastEvent.stage, lastSeen: Date.now() } }));
  }, [lastEvent]);

  useEffect(() => {
    const t = setInterval(() => {
      setReceipts((prev) => {
        const next = { ...prev };
        const now = Date.now();
        for (const [id, r] of Object.entries(next)) {
          if (now - r.lastSeen > ACTIVE_WINDOW_MS) delete next[id];
        }
        return next;
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const countAt = (stage: string) => Object.values(receipts).filter((r) => r.stage === stage).length;
  const retryingCount = countAt(PipelineStage.RETRYING);

  return (
    <div className="relative rounded-2xl bg-slate-900/60 border border-slate-800/80 p-6 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(99,102,241,0.08),transparent_50%)]" />

      <div className="flex items-center justify-between mb-6 relative z-10">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100">Live pipeline</h3>
            <p className="text-xs text-slate-400">Real event lifecycle, pushed over WebSocket</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex h-2 w-2 relative">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${status === "open" ? "bg-emerald-400 opacity-75" : "bg-slate-500"}`} />
            <span className={`relative inline-flex rounded-full h-2 w-2 ${status === "open" ? "bg-emerald-500" : "bg-slate-600"}`} />
          </span>
          <span className="text-xs font-mono text-slate-400">{status}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 relative z-10">
        {PIPELINE_STAGE_ORDER.map((stage) => {
          const meta = STAGE_META[stage];
          const count = countAt(stage);
          const lit = count > 0;
          const isProcessing = stage === PipelineStage.PROCESSING;

          return (
            <div
              key={stage}
              className={`relative rounded-xl bg-slate-950/80 border p-4 transition-colors duration-300 ${
                lit ? `border-${meta.color}-500/50` : "border-slate-800"
              }`}
            >
              <div className="flex items-center justify-between mb-3">
                <div className={`p-1.5 rounded-lg bg-${meta.color}-500/10 border border-${meta.color}-500/20 text-${meta.color}-400`}>
                  {meta.icon}
                </div>
                {isProcessing && retryingCount > 0 && (
                  <span className="text-[10px] font-mono text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded-full border border-amber-500/20">
                    ↻ {retryingCount}
                  </span>
                )}
              </div>
              <p className="text-xs font-semibold text-slate-200">{meta.label}</p>
              <p className="text-[11px] text-slate-400 mt-1">{meta.desc}</p>
              <div className="mt-3 pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] font-mono text-slate-500">
                <span>last 4s</span>
                <span className={lit ? `text-${meta.color}-400` : "text-slate-600"}>{count}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}