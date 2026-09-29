"use client";

import Link from "next/link";

import { ScenarioLauncher } from "@/components/scenario-launcher";
import { PageHeader } from "@/components/shell";
import { Badge, Card, CardBody, CardHeader, Empty, Stat, StatusBadge, Table, Td, Th } from "@/components/ui/primitives";
import { fmtDateTime, fmtDuration, fmtMs, fmtPct, fmtRatio, fmtUsd } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import type { Incident, Kpis, ServiceOverview } from "@/lib/types";

interface CostOverview {
  by_agent: Array<{ agent: string; calls: number; tokens: number; cost_usd: number }>;
  by_model: Array<{ provider: string; model: string; calls: number; tokens: number; cost_usd: number }>;
}

export default function DashboardPage() {
  const kpis = usePoll<Kpis>("/api/kpis", 5000).data;
  const services = usePoll<ServiceOverview[]>("/api/services", 5000).data;
  const incidents = usePoll<Incident[]>("/api/incidents?limit=8", 4000).data;
  const cost = usePoll<CostOverview>("/api/cost", 10000).data;

  return (
    <>
      <PageHeader title="Operations dashboard" description="Production health, investigations and AI governance" />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Stat label="Incidents" value={kpis?.incidents_total ?? "—"} hint={`${kpis?.incidents_resolved ?? 0} resolved`} />
        <Stat
          label="Awaiting approval"
          value={kpis?.incidents_awaiting_approval ?? "—"}
          tone={kpis?.incidents_awaiting_approval ? "warning" : undefined}
        />
        <Stat label="MTTR" value={fmtDuration(kpis?.mttr_seconds)} hint="incident open → resolved" />
        <Stat label="Time to root cause" value={fmtDuration(kpis?.mean_time_to_root_cause_seconds)} hint={`MTTD ${fmtDuration(kpis?.mttd_seconds)}`} />
        <Stat
          label="Unsafe AI actions blocked"
          value={kpis?.remediations_rejected_by_validation ?? "—"}
          hint={`of ${kpis?.remediations_proposed ?? 0} proposals`}
          tone="success"
        />
        <Stat label="AI cost / incident" value={fmtUsd(kpis?.mean_ai_cost_per_incident_usd)} hint={`total ${fmtUsd(kpis?.total_ai_cost_usd)}`} />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Stat label="Autonomous investigation success" value={fmtRatio(kpis?.autonomous_investigation_success_rate)} />
        <Stat label="Root-cause accuracy (eval)" value={fmtRatio(kpis?.root_cause_accuracy)} />
        <Stat label="Unsafe-action rejection (eval)" value={fmtRatio(kpis?.unsafe_action_rejection_rate)} />
        <Stat label="Approval rate" value={fmtRatio(kpis?.approval_rate)} />
        <Stat label="Rollback rate" value={fmtRatio(kpis?.rollback_rate)} />
        <Stat label="Mean investigation steps" value={kpis?.mean_investigation_steps ?? "—"} />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Service health" description="Live golden signals from the production simulator" />
          <Table>
            <thead>
              <tr>
                <Th>Service</Th>
                <Th>Status</Th>
                <Th className="text-right">p95</Th>
                <Th className="text-right">Errors</Th>
                <Th className="text-right">CPU</Th>
                <Th className="text-right">Memory</Th>
                <Th className="text-right">DB pool</Th>
                <Th className="text-right">Replicas</Th>
              </tr>
            </thead>
            <tbody className="tabular">
              {(services ?? []).map((s) => (
                <tr key={s.name}>
                  <Td>
                    <div className="font-medium">{s.name}</div>
                    <div className="text-xs text-slate-500">
                      {s.team} · {s.criticality} · {s.environment}
                    </div>
                  </Td>
                  <Td>
                    <Badge tone={s.health.healthy ? "success" : "danger"}>{s.health.healthy ? "healthy" : "SLO breach"}</Badge>
                  </Td>
                  <Td className={`text-right ${s.metrics.p95_latency_ms > s.slo_p95_latency_ms ? "text-rose-700" : ""}`}>
                    {fmtMs(s.metrics.p95_latency_ms)}
                  </Td>
                  <Td className={`text-right ${s.metrics.error_rate_pct > s.slo_error_rate_pct ? "text-rose-700" : ""}`}>
                    {fmtPct(s.metrics.error_rate_pct)}
                  </Td>
                  <Td className="text-right">{fmtPct(s.metrics.cpu_pct, 0)}</Td>
                  <Td className="text-right">{fmtPct(s.metrics.memory_pct, 0)}</Td>
                  <Td className={`text-right ${s.metrics.db_pool_utilization_pct >= 90 ? "text-rose-700" : ""}`}>
                    {fmtPct(s.metrics.db_pool_utilization_pct, 0)}
                  </Td>
                  <Td className="text-right">{s.replicas}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {!services?.length && <CardBody><Empty>Simulator unavailable</Empty></CardBody>}
        </Card>
        <ScenarioLauncher compact />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Recent incidents" action={<Link href="/incidents" className="text-xs text-slate-500 hover:underline">View all</Link>} />
          <Table>
            <thead>
              <tr>
                <Th>Incident</Th>
                <Th>Service</Th>
                <Th>Status</Th>
                <Th>Root cause</Th>
                <Th>Risk</Th>
                <Th>Opened</Th>
              </tr>
            </thead>
            <tbody>
              {(incidents ?? []).map((i) => (
                <tr key={i.id} className="hover:bg-slate-50">
                  <Td>
                    <Link href={`/incidents/${i.id}`} className="font-medium text-slate-900 hover:underline">
                      {i.id}
                    </Link>
                    <div className="max-w-xs truncate text-xs text-slate-500">{i.title}</div>
                  </Td>
                  <Td>{i.service}</Td>
                  <Td><StatusBadge status={i.status} /></Td>
                  <Td className="max-w-xs text-xs">{i.root_cause ?? "—"}</Td>
                  <Td>{i.risk_level ? <StatusBadge status={i.risk_level} /> : "—"}</Td>
                  <Td className="text-xs text-slate-500">{fmtDateTime(i.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {!incidents?.length && <CardBody><Empty>No incidents yet — trigger one to start a demo.</Empty></CardBody>}
        </Card>
        <Card>
          <CardHeader title="AI cost" description="Estimated from token usage and model pricing" />
          <CardBody className="space-y-4 text-sm">
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">By agent</div>
              {(cost?.by_agent ?? []).map((a) => (
                <div key={a.agent} className="tabular flex justify-between py-0.5">
                  <span>{a.agent}</span>
                  <span className="text-slate-600">{a.calls} calls · {fmtUsd(a.cost_usd)}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">By model</div>
              {(cost?.by_model ?? []).map((m) => (
                <div key={`${m.provider}/${m.model}`} className="tabular flex justify-between py-0.5">
                  <span>{m.provider}/{m.model}</span>
                  <span className="text-slate-600">{m.tokens.toLocaleString()} tok · {fmtUsd(m.cost_usd)}</span>
                </div>
              ))}
            </div>
            <div className="tabular flex justify-between border-t border-slate-100 pt-2 font-medium">
              <span>Cost per resolved incident</span>
              <span>{fmtUsd(kpis?.ai_cost_per_resolved_incident_usd)}</span>
            </div>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
