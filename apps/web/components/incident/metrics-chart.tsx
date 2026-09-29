"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardBody, CardHeader, Empty } from "@/components/ui/primitives";
import { fmtMs, fmtPct } from "@/lib/format";
import type { MetricsSnapshot } from "@/lib/types";

export function ServiceHealth({
  snapshots,
  slo,
}: {
  snapshots: MetricsSnapshot[];
  slo?: { p95_latency_ms: number; error_rate_pct: number };
}) {
  const latest = snapshots.filter((s) => s.series?.length).at(-1);
  const investigation = snapshots.find((s) => s.phase === "investigation");
  const post = snapshots.filter((s) => s.phase === "post_remediation").at(-1);
  const data = (latest?.series ?? []).map((p) => ({
    t: new Date(String(p.ts)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    p95: Number(p.p95_latency_ms),
    errors: Number(p.error_rate_pct),
    pool: Number(p.db_pool_utilization_pct),
    cpu: Number(p.cpu_pct),
  }));

  return (
    <Card>
      <CardHeader
        title="Service health"
        description={post ? "Before and after remediation" : "Golden signals over the last hour"}
      />
      <CardBody>
        <div className="mb-3 grid grid-cols-5 gap-2 text-center text-xs">
          {[
            ["p95 latency", fmtMs(investigation?.p95_latency_ms), post && fmtMs(post.p95_latency_ms)],
            ["error rate", fmtPct(investigation?.error_rate_pct), post && fmtPct(post.error_rate_pct)],
            ["CPU", fmtPct(investigation?.cpu_pct, 0), post && fmtPct(post.cpu_pct, 0)],
            ["memory", fmtPct(investigation?.memory_pct, 0), post && fmtPct(post.memory_pct, 0)],
            ["DB pool", fmtPct(investigation?.db_pool_utilization_pct, 0), post && fmtPct(post.db_pool_utilization_pct, 0)],
          ].map(([label, before, after]) => (
            <div key={String(label)} className="rounded-md bg-slate-50 px-2 py-1.5">
              <div className="text-slate-500">{label}</div>
              <div className="tabular font-semibold text-slate-900">{before}</div>
              {after && <div className="tabular font-semibold text-emerald-700">→ {after}</div>}
            </div>
          ))}
        </div>
        {data.length ? (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <div className="h-40">
              <div className="mb-1 text-xs text-slate-500">p95 latency (ms)</div>
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={data} margin={{ left: -10, right: 8, top: 4, bottom: 0 }}>
                  <CartesianGrid stroke="#f1f5f9" />
                  <XAxis dataKey="t" tick={{ fontSize: 10 }} interval={14} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip />
                  {slo && <ReferenceLine y={slo.p95_latency_ms} stroke="#f59e0b" strokeDasharray="4 3" />}
                  <Line type="monotone" dataKey="p95" stroke="#0f172a" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="h-40">
              <div className="mb-1 text-xs text-slate-500">error rate (%) · DB pool (%) · CPU (%)</div>
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={data} margin={{ left: -10, right: 8, top: 4, bottom: 0 }}>
                  <CartesianGrid stroke="#f1f5f9" />
                  <XAxis dataKey="t" tick={{ fontSize: 10 }} interval={14} />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} />
                  <Tooltip />
                  <Line type="monotone" dataKey="errors" stroke="#be123c" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  <Line type="monotone" dataKey="pool" stroke="#b45309" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  <Line type="monotone" dataKey="cpu" stroke="#64748b" dot={false} strokeWidth={1} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <Empty>Metrics will appear once the investigation starts.</Empty>
        )}
      </CardBody>
    </Card>
  );
}
