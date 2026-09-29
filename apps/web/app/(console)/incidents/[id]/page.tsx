"use client";

import { Play, ShieldCheck } from "lucide-react";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ApprovalPanel } from "@/components/incident/approval-panel";
import { ServiceHealth } from "@/components/incident/metrics-chart";
import { LiveProgress } from "@/components/incident/progress";
import { describe, Hypotheses, RiskCard, ValidationCard } from "@/components/incident/reasoning";
import { Audit, Cost, Evidence, Report, Timeline } from "@/components/incident/records";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, Empty, StatusBadge, Tabs } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { fmtDateTime, fmtMs, fmtPct, TERMINAL_STATUSES } from "@/lib/format";
import { useAuth, usePoll } from "@/lib/hooks";
import { useLiveTimeline } from "@/lib/timeline";
import type {
  EvidenceItem,
  Hypothesis,
  HypothesisTest,
  Incident,
  MetricsSnapshot,
  RemediationPlan,
  ToolCall,
  ValidationResult,
} from "@/lib/types";

type TabId = "timeline" | "hypotheses" | "validation" | "evidence" | "risk" | "audit" | "cost" | "report";

interface EvidenceResponse {
  items: EvidenceItem[];
  metrics_snapshots: MetricsSnapshot[];
  tool_calls: ToolCall[];
}

interface RemediationResponse {
  plans: Array<{ id: string; status: string; payload: RemediationPlan }>;
  validations: ValidationResult[];
}

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const [tab, setTab] = useState<TabId>("timeline");
  const [starting, setStarting] = useState(false);

  const incidentPoll = usePoll<Incident>(`/api/incidents/${id}`, 2000);
  const incident = incidentPoll.data;
  const live = !!incident && !TERMINAL_STATUSES.has(incident.status);
  const interval = live ? 2500 : 0;
  const events = useLiveTimeline(id, live, incident ? `${incident.status}:${incident.current_node}:${incident.updated_at}` : undefined);
  const evidence = usePoll<EvidenceResponse>(`/api/incidents/${id}/evidence`, interval).data;
  const hyps = usePoll<{ hypotheses: Array<{ status: Hypothesis["status"]; payload: Hypothesis }>; tests: HypothesisTest[] }>(`/api/incidents/${id}/hypotheses`, interval).data;
  const remediation = usePoll<RemediationResponse>(`/api/incidents/${id}/remediation`, interval).data;

  const validationsByPlan = useMemo(() => new Map((remediation?.validations ?? []).map((v) => [v.plan_id, v])), [remediation]);
  const hypotheses = (hyps?.hypotheses ?? []).map((h) => ({ ...h.payload, status: h.status }));
  const state = incident?.state ?? {};
  const selected = state.selected_hypothesis ?? hypotheses.find((h) => h.status === "SUPPORTED") ?? null;
  const selectedTest = (hyps?.tests ?? []).filter((t) => t.hypothesis_id === selected?.id).at(-1);
  const rootCauseEvidence = (evidence?.items ?? []).filter((e) => selectedTest?.evidence_refs.includes(e.id));

  if (!incident) {
    return <div className="text-sm text-slate-500">{incidentPoll.error ?? "Loading incident…"}</div>;
  }

  async function startInvestigation() {
    setStarting(true);
    try {
      await api(`/api/incidents/${id}/investigate`, { method: "POST" });
      await incidentPoll.refresh();
    } finally {
      setStarting(false);
    }
  }

  const alert = incident.alert;
  return (
    <>
      <PageHeader
        title={`${incident.id} · ${incident.title}`}
        description={`${incident.service} · ${incident.environment} · ${incident.severity} · opened ${fmtDateTime(incident.created_at)} by ${incident.created_by}`}
        actions={
          <>
            <StatusBadge status={incident.status} />
            {incident.risk_level && <Badge tone="neutral">risk {incident.risk_level} {incident.risk_score?.toFixed(0)}</Badge>}
            {incident.status === "OPEN" && can("incidents:investigate") && (
              <Button onClick={startInvestigation} disabled={starting}>
                <Play className="h-4 w-4" /> {starting ? "Starting…" : "Start investigation"}
              </Button>
            )}
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title="Incident summary" />
            <CardBody className="grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
              {[
                ["p95 latency", fmtMs(alert.p95_latency_ms)],
                ["error rate", fmtPct(alert.error_rate_pct)],
                ["CPU", fmtPct(alert.cpu_pct, 0)],
                ["memory", fmtPct(alert.memory_pct, 0)],
                ["DB pool", fmtPct(alert.db_pool_utilization_pct, 0)],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="text-xs text-slate-500">{label} at alert</div>
                  <div className="tabular text-lg font-semibold">{value}</div>
                </div>
              ))}
            </CardBody>
          </Card>
          <ServiceHealth snapshots={evidence?.metrics_snapshots ?? []} slo={state.slo} />
          {state.awaiting_approval && state.approval_request && (
            <ApprovalPanel incidentId={id} request={state.approval_request} onDecided={() => void incidentPoll.refresh()} />
          )}
          <Card>
            <CardHeader
              title="Root cause"
              description="Confidence is shown separately from evidence; the validated sandbox run is the deterministic confirmation"
            />
            <CardBody className="text-sm">
              {selected ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{selected.title}</span>
                    {selectedTest && <Badge tone="info">calibrated confidence {selectedTest.calibrated_confidence.toFixed(2)}</Badge>}
                    {state.verification_result?.passed && (
                      <Badge tone="success">
                        <ShieldCheck className="h-3 w-3" /> verified by recovery
                      </Badge>
                    )}
                  </div>
                  <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-700">
                    {rootCauseEvidence.map((e) => (
                      <li key={e.id}>
                        <span className="font-mono text-slate-500">{e.id}</span> {e.summary}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <Empty>No root cause selected yet.</Empty>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Proposed remediations" description="Every AI proposal is validated deterministically before it can reach a human or production" />
            <CardBody className="space-y-3">
              {(remediation?.plans ?? []).map((p) => (
                <ValidationCard key={p.id} plan={p.payload} validation={validationsByPlan.get(p.id)} />
              ))}
              {!remediation?.plans.length && <Empty>No remediation proposed yet.</Empty>}
              {state.execution_result && (
                <div className="rounded-md bg-slate-50 px-3 py-2 text-xs">
                  <span className="font-medium">Execution:</span> {state.execution_result.success ? "succeeded" : "failed"} — {state.execution_result.action}
                  {state.verification_result && (
                    <span className={state.verification_result.passed ? "text-emerald-700" : "text-rose-700"}>
                      {" "}· verification {state.verification_result.passed ? "passed" : "FAILED"}
                    </span>
                  )}
                  {state.rollback_result && <span className="text-amber-700"> · automatically rolled back ({state.rollback_result.action})</span>}
                </div>
              )}
            </CardBody>
          </Card>
        </div>
        <div className="space-y-4">
          <LiveProgress events={events} status={incident.status} currentNode={incident.current_node} />
          <RiskCard risk={state.risk} />
          {state.proposed_remediation && (
            <Card>
              <CardHeader title="Current plan" />
              <CardBody className="space-y-1 text-xs text-slate-700">
                <div className="font-medium">{describe(state.proposed_remediation)}</div>
                <div>Expected impact: {state.proposed_remediation.expected_impact}</div>
                <div>Rollback: {state.proposed_remediation.rollback_strategy}</div>
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      <div className="mt-6">
        <Tabs<TabId>
          active={tab}
          onChange={setTab}
          tabs={[
            { id: "timeline", label: "Timeline", count: events.length },
            { id: "hypotheses", label: "Hypotheses", count: hypotheses.length },
            { id: "validation", label: "Validation", count: remediation?.validations.length ?? 0 },
            { id: "evidence", label: "Evidence", count: evidence?.items.length ?? 0 },
            { id: "risk", label: "Risk" },
            { id: "audit", label: "Audit log", count: incident.counts?.audit_events },
            { id: "cost", label: "AI cost" },
            { id: "report", label: "Report" },
          ]}
        />
        <div className="mt-4">
          {tab === "timeline" && <Timeline events={events} />}
          {tab === "hypotheses" && <Hypotheses hypotheses={hypotheses} tests={hyps?.tests ?? []} />}
          {tab === "validation" && (
            <div className="space-y-3">
              {(remediation?.plans ?? []).map((p) => (
                <ValidationCard key={p.id} plan={p.payload} validation={validationsByPlan.get(p.id)} />
              ))}
              {!remediation?.plans.length && <Empty>No validations yet.</Empty>}
            </div>
          )}
          {tab === "evidence" && <Evidence items={evidence?.items ?? []} tools={evidence?.tool_calls ?? []} />}
          {tab === "risk" && <RiskCard risk={state.risk} />}
          {tab === "audit" && <Audit incidentId={id} />}
          {tab === "cost" && <Cost incidentId={id} />}
          {tab === "report" && <Report incidentId={id} available={!!incident.has_report} />}
        </div>
      </div>
    </>
  );
}
