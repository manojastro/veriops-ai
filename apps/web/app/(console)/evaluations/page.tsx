"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, Empty, Stat, StatusBadge, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { fmtDateTime, fmtRatio, fmtUsd } from "@/lib/format";
import { useAuth, usePoll } from "@/lib/hooks";
import type { EvaluationRun } from "@/lib/types";

export default function EvaluationsPage() {
  const { can } = useAuth();
  const runs = usePoll<EvaluationRun[]>("/api/evaluations", 3000);
  const latestId = runs.data?.[0]?.id;
  const latest = usePoll<EvaluationRun>(latestId ? `/api/evaluations/${latestId}` : null, runs.data?.[0]?.status === "RUNNING" ? 2000 : 0).data;
  const [busy, setBusy] = useState(false);
  const m = (latest?.metrics ?? {}) as Record<string, number | Record<string, boolean>>;
  const gates = (m.gates ?? {}) as Record<string, boolean>;

  async function run() {
    setBusy(true);
    try {
      await api("/api/evaluations/run", { method: "POST", json: {} });
      await runs.refresh();
    } finally {
      setBusy(false);
    }
  }

  const num = (k: string) => (typeof m[k] === "number" ? (m[k] as number) : null);

  return (
    <>
      <PageHeader
        title="Agent evaluation"
        description="Full investigations against ground-truth scenarios; CI blocks merges when a gate fails"
        actions={
          can("evaluations:run") && (
            <Button onClick={run} disabled={busy || runs.data?.[0]?.status === "RUNNING"}>
              <FlaskConical className="h-4 w-4" /> {runs.data?.[0]?.status === "RUNNING" ? "Running…" : "Run evaluation"}
            </Button>
          )
        }
      />
      {!latest ? (
        <Empty>No evaluation runs yet.</Empty>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2 text-sm">
            <span className="font-medium">{latest.id}</span>
            <StatusBadge status={latest.status} />
            {latest.status === "COMPLETED" && <Badge tone={latest.passed_thresholds ? "success" : "danger"}>{latest.passed_thresholds ? "all gates passed" : "gates failed"}</Badge>}
            <span className="text-xs text-slate-500">provider {latest.provider} · {latest.scenario_count} cases · {fmtDateTime(latest.started_at)}</span>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label="Root-cause accuracy" value={fmtRatio(num("root_cause_accuracy"))} />
            <Stat label="Remediation accuracy" value={fmtRatio(num("remediation_accuracy"))} />
            <Stat label="Unsafe rejection rate" value={fmtRatio(num("unsafe_recommendation_rejection_rate"))} hint={`${num("unsafe_actions_rejected") ?? 0}/${num("unsafe_actions_proposed") ?? 0} rejected`} tone="success" />
            <Stat label="Unsafe actions executed" value={num("unsafe_actions_executed") ?? "—"} tone={num("unsafe_actions_executed") ? "danger" : "success"} />
            <Stat label="Evidence precision / recall" value={`${fmtRatio(num("evidence_precision"))} / ${fmtRatio(num("evidence_recall"))}`} />
            <Stat label="Hallucination rate" value={fmtRatio(num("hallucination_rate"))} />
            <Stat label="Tool-call success" value={fmtRatio(num("tool_call_success_rate"))} />
            <Stat label="False remediation rate" value={fmtRatio(num("false_remediation_rate"))} />
            <Stat label="Completion rate" value={fmtRatio(num("investigation_completion_rate"))} />
            <Stat label="Mean time to root cause" value={num("mean_time_to_root_cause_s") !== null ? `${num("mean_time_to_root_cause_s")} s` : "—"} />
            <Stat label="Mean graph steps" value={num("mean_graph_steps") ?? "—"} />
            <Stat label="Mean AI cost / case" value={fmtUsd(num("mean_llm_cost_usd"))} />
          </div>
          <Card className="mt-4">
            <CardHeader title="Gates" />
            <CardBody className="flex flex-wrap gap-2">
              {Object.entries(gates).map(([k, v]) => (
                <Badge key={k} tone={v ? "success" : "danger"}>
                  {k.replace(/_/g, " ")}: {v ? "PASS" : "FAIL"}
                </Badge>
              ))}
            </CardBody>
          </Card>
          <Card className="mt-4">
            <CardHeader title="Cases" />
            <Table>
              <thead>
                <tr>
                  <Th>Case</Th>
                  <Th>Scenario</Th>
                  <Th>Final status</Th>
                  <Th>Root cause</Th>
                  <Th>Proposed → executed</Th>
                  <Th>Unsafe rejected</Th>
                  <Th className="text-right">Steps</Th>
                  <Th>Result</Th>
                </tr>
              </thead>
              <tbody className="text-xs">
                {(latest.results ?? []).map((r) => (
                  <tr key={r.incident_id}>
                    <Td className="font-mono">{r.incident_id}</Td>
                    <Td>{r.scenario}</Td>
                    <Td><StatusBadge status={r.final_status} /></Td>
                    <Td>
                      {r.root_cause_category ?? "—"} {r.root_cause_correct ? <Badge tone="success">✓</Badge> : <Badge tone="danger">✗</Badge>}
                    </Td>
                    <Td className="font-mono">{r.proposed_actions.join(" → ")} ⇒ {r.remediation_action ?? "none"}</Td>
                    <Td>{r.unsafe_proposed.length ? `${r.unsafe_rejected.length}/${r.unsafe_proposed.length}` : "—"}</Td>
                    <Td className="tabular text-right">{r.graph_steps}</Td>
                    <Td>{r.passed ? <Badge tone="success">PASS</Badge> : <Badge tone="danger">{r.failures.join("; ")}</Badge>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}
    </>
  );
}
