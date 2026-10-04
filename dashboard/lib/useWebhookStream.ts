import { useEffect, useRef, useState } from "react";
import type { PipelineStageValue } from "./stages";

export interface PipelineEvent {
  receipt_id: string;
  provider: string;
  stage: PipelineStageValue;
  ts: number;
}

type ConnectionStatus = "connecting" | "open" | "closed" | "error";

const RECONNECT_DELAY_MS = 3000;

export function useWebhookStream() {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [lastEvent, setLastEvent] = useState<PipelineEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      const url = process.env.NEXT_PUBLIC_WS_URL;
      const token = process.env.NEXT_PUBLIC_WS_TOKEN;

      if (!url || !token) {
        setStatus("error");
        return;
      }

      const ws = new WebSocket(`${url}?token=${encodeURIComponent(token)}`);
      wsRef.current = ws;
      setStatus("connecting");

      ws.onopen = () => {
        if (cancelled) return;
        setStatus("open");
      };

      ws.onmessage = (event) => {
        if (cancelled) return;
        try {
          const parsed: PipelineEvent = JSON.parse(event.data);
          setLastEvent(parsed);
        } catch {
          // Malformed event — ignore rather than crash the dashboard
          // over a single bad message.
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      ws.onerror = () => {
        if (cancelled) return;
        setStatus("error");
        ws.close(); // triggers onclose → reconnect
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, []);

  return { status, lastEvent };
}