import { NextResponse } from "next/server";

const RENDER_API_BASE = process.env.RENDER_API_BASE; 
const DASHBOARD_TOKEN = process.env.DASHBOARD_API_TOKEN; 
const RENDER_COLD_START_TIMEOUT_MS = 65_000;

export async function GET() {
  if (!RENDER_API_BASE || !DASHBOARD_TOKEN) {
    return NextResponse.json(
      { error: "Server misconfigured: missing RENDER_API_BASE or DASHBOARD_API_TOKEN" },
      { status: 500 }
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), RENDER_COLD_START_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${RENDER_API_BASE}/metrics/summary`, {
      headers: { Authorization: `Bearer ${DASHBOARD_TOKEN}` },
      signal: controller.signal,
      cache: "no-store",
    });

    clearTimeout(timeout);

    if (!upstream.ok) {
      return NextResponse.json(
        { error: `Upstream returned ${upstream.status}` },
        { status: upstream.status }
      );
    }

    const data = await upstream.json();
    return NextResponse.json(data);
  } catch (err) {
    clearTimeout(timeout);

    if (err instanceof Error && err.name === "AbortError") {
      return NextResponse.json(
        { error: "Backend did not respond in time (likely waking from idle)" },
        { status: 504 }
      );
    }

    return NextResponse.json(
      { error: "Failed to reach backend" },
      { status: 502 }
    );
  }
}