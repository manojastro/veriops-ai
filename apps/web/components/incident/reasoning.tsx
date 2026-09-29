"use client";

import { AlertTriangle, CheckCircle2, ShieldAlert, XCircle } from "lucide-react";

import { Badge, Bar, Card, CardBody, CardHeader, Empty, StatusBadge, statusTone, Table, Td, Th } from "@/components/ui/primitives";
import { cn, fmtMs, fmtPct, titleCase } from "@/lib/format";
import type { Hypothesis, HypothesisTest, RemediationPlan, RiskAssessment, ValidationResult } from "@/lib/types";

export function Hypotheses({ hypotheses, tests }: { hypotheses: Hypothesis[]; tests: HypothesisTest[] }) {
  if (!hypotheses.length) return <Empty>No hypotheses yet.</Empty>;
  return (
    <div className="space-y-3">
      {hypotheses.map((h) => {
        const hTests = tests.filter((t) => t.hypothesis_id === h.id);
        const last = hTests.at(-1);
        return (
          <Card key={h.id} className={cn(h.status === "REFUTED" && "opacity-80")}>
            <div className="flex items-start justify-between gap-3 px-4 py-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{h.title}</span>
                  <StatusBadge status={h.status} />
                  <Badge>{titleCase(h.category)}</Badge>
                </div>
                <p className="mt-1 text-xs text-slate-600">{h.description}</p>
              </div>
              <div className="w-44 shrink-0 text-right text-xs">
                <div className="text-slate-500">Prior probability (LLM)</div>
                <div className="tabular font-semibold">{Math.round(h.probability * 100)}%</div>
                {last && (
                  <>
                    <div className="mt-1 text-slate-500">Calibrated confidence</div>
                    <div className="tabular font-semibold">{last.calibrated_confidence.toFixed(2)}</div>
                    <div className="text-[11px] text-slate-400">
                      LLM {last.llm_confidence.toFixed(2)} × coverage {Math.round(last.evidence_coverage * 100)}%
                    </div>
                  </>
                )}
              </div>
            </div>
            {last && (
              <div className="border-t border-slate-100 px-4 py-3 text-xs">
                <div className="mb-1.5 text-slate-700">
                  <span className="font-medium">Judgement ({hTests.length} test{hTests.length > 1 ? "s" : ""}):</span> {last.reasoning}
                </div>
                {last.missing_evidence.length > 0 && (
                  <div className="mb-1.5 text-amber-700">Missing evidence: {last.missing_evidence.join(", ")}</div>
                )}
                {last.signals.length > 0 && (
                  <div className="mt-2">
                    <div className="mb-1 font-medium text-slate-500">Deterministic signal checks (independent of the LLM)</div>
                    <div className="grid grid-cols-1 gap-1 md:grid-cols-2">
                      {last.signals.map((s) => (
                        <div key={s.name} className="flex items-center gap-1.5">
                          {s.holds ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                          ) : (
                            <XCircle className="h-3.5 w-3.5 text-rose-600" />
                          )}
                          <span className="font-mono">{s.name}</span>
                          <span className="text-slate-500">
                            observed {s.observed}, expected {s.expectation}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function CheckRow({ check }: { check: { name: string; passed: boolean; detail: string } }) {
  return (
    <div className="flex items-start gap-2 py-0.5 text-xs">
      {check.passed ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />}
      <span className="font-mono text-slate-700">{check.name}</span>
      <span className="text-slate-600">{check.detail}</span>
    </div>
  );
}

const SANDBOX_KEYS: Array<[string, string, (v: number) => string]> = [
  ["p95_latency_ms", "p95 latency", fmtMs],
  ["error_rate_pct", "error rate", (v) => fmtPct(v)],
  ["cpu_pct", "CPU", (v) => fmtPct(v, 0)],
  ["db_pool_utilization_pct", "DB pool", (v) => fmtPct(v, 0)],
];

export function ValidationCard({ plan, validation }: { plan: RemediationPlan; validation?: ValidationResult }) {
  const rejected = validation?.status === "REJECTED";
  return (
    <Card className={cn(rejected ? "border-rose-300" : validation ? "border-emerald-300" : "")}>
      <div className={cn("flex items-center justify-between gap-3 px-4 py-2.5", rejected ? "bg-rose-50" : validation ? "bg-emerald-50" : "bg-slate-50")}>
        <div className="flex items-center gap-2 text-sm font-semibold">
          {rejected ? <ShieldAlert className="h-4 w-4 text-rose-700" /> : <CheckCircle2 className="h-4 w-4 text-emerald-700" />}
          {rejected ? "AI RECOMMENDATION REJECTED" : validation ? "REMEDIATION VALIDATED" : "Awaiting validation"}
          <span className="font-normal text-slate-700">— {describe(plan)}</span>
        </div>
        <span className="text-xs text-slate-500">
          attempt {plan.attempt} · {validation?.validator}
        </span>
      </div>
      <CardBody className="space-y-3">
        <div className="text-xs text-slate-600">
          <span className="font-medium text-slate-700">AI rationale:</span> {plan.rationale}
        </div>
        {validation && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              {validation.failed_checks.length > 0 && (
                <>
                  <div className="mb-1 text-xs font-medium uppercase tracking-wide text-rose-700">Failed checks</div>
                  {validation.failed_checks.map((c) => (
                    <CheckRow key={c.name} check={c} />
                  ))}
                </>
              )}
              <div className="mb-1 mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">Passed checks</div>
              {validation.passed_checks.length ? validation.passed_checks.map((c) => <CheckRow key={c.name} check={c} />) : <div className="text-xs text-slate-400">none</div>}
            </div>
            {validation.sandbox && (
              <div>
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
                  Sandbox simulation {validation.sandbox.passed ? "(passed)" : "(failed)"}
                </div>
                <Table>
                  <thead>
                    <tr>
                      <Th>Signal</Th>
                      <Th className="text-right">Before</Th>
                      <Th className="text-right">After</Th>
                    </tr>
                  </thead>
                  <tbody className="tabular text-xs">
                    {SANDBOX_KEYS.map(([key, label, fmt]) => {
                      const before = validation.sandbox!.before[key];
                      const after = validation.sandbox!.after[key];
                      return (
                        <tr key={key}>
                          <Td>{label}</Td>
                          <Td className="text-right">{fmt(before)}</Td>
                          <Td className={cn("text-right font-medium", after < before ? "text-emerald-700" : after > before ? "text-rose-700" : "")}>{fmt(after)}</Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Table>
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function describe(plan: RemediationPlan): string {
  const a = plan.action;
  switch (a.action_type) {
    case "scale_deployment":
      return `Scale ${a.service} to ${a.replicas} replicas`;
    case "rollback_deployment":
      return `Roll back deployment #${a.deployment_id} of ${a.service}`;
    case "restart_deployment":
      return `Rolling restart of ${a.service}`;
    case "configuration_change":
      return `Set ${a.config_key}=${a.config_value} on ${a.service}`;
    case "database_remediation":
      return `Database change: ${a.sql}`;
    default:
      return `${titleCase(a.action_type)} on ${a.service}`;
  }
}

export function RiskCard({ risk }: { risk: RiskAssessment | null | undefined }) {
  if (!risk) return <Empty>Risk is calculated after a remediation passes validation.</Empty>;
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Deterministic risk <StatusBadge status={risk.level} />
          </span>
        }
        description={`Score ${risk.score.toFixed(1)}/100 · ${risk.policy_decision.replace(/_/g, " ").toLowerCase()}`}
      />
      <CardBody className="space-y-2">
        {risk.factors.map((f) => (
          <div key={f.name} className="grid grid-cols-12 items-center gap-2 text-xs">
            <div className="col-span-4 font-mono">{f.name}</div>
            <div className="col-span-4">
              <Bar value={f.contribution} max={f.weight} tone={f.value > 0.66 ? "danger" : f.value > 0.33 ? "warning" : "success"} />
            </div>
            <div className="tabular col-span-1 text-right">{f.contribution.toFixed(1)}</div>
            <div className="col-span-3 truncate text-slate-500" title={f.explanation}>
              /{f.weight} · {f.explanation}
            </div>
          </div>
        ))}
        <div className="flex items-start gap-1.5 border-t border-slate-100 pt-2 text-xs text-slate-600">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-amber-600" />
          {risk.policy_reasons.join("; ")}
        </div>
      </CardBody>
    </Card>
  );
}

export { statusTone };
