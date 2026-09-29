"use client";

import { Check, Circle, Loader2, X } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardBody, CardHeader } from "@/components/ui/primitives";
import { cn, TERMINAL_STATUSES } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";

export type StageState = "done" | "active" | "pending" | "failed" | "skipped";

export interface Stage {
  id: string;
  label: string;
  state: StageState;
  note?: string;
}

const STAGES: Array<{ id: string; label: string; done: (e: TimelineEvent) => boolean; node?: string[] }> = [
  { id: "alert", label: "Alert received", done: (e) => e.type === "incident_created" },
  { id: "metrics", label: "Metrics collected", done: (e) => e.node === "query_metrics", node: ["query_metrics"] },
  { id: "logs", label: "Logs analyzed", done: (e) => e.node === "query_logs", node: ["query_logs"] },
  { id: "traces", label: "Traces inspected", done: (e) => e.node === "query_traces", node: ["query_traces"] },
  {
    id: "deploy",
    label: "Recent deployments checked",
    done: (e) => e.node === "inspect_recent_deployments",
    node: ["inspect_recent_deployments", "inspect_git_changes"],
  },
  {
    id: "knowledge",
    label: "Runbooks & similar incidents retrieved",
    done: (e) => e.node === "retrieve_similar_incidents",
    node: ["retrieve_runbooks", "retrieve_similar_incidents"],
  },
  { id: "hypotheses", label: "Hypotheses generated", done: (e) => e.type === "hypotheses_generated", node: ["generate_hypotheses", "rank_hypotheses"] },
  {
    id: "testing",
    label: "Hypotheses tested",
    done: (e) => e.type === "root_cause_selected",
    node: ["test_hypothesis", "check_evidence_sufficiency", "gather_additional_evidence"],
  },
  { id: "remediation", label: "Remediation generated", done: (e) => e.type === "remediation_proposed", node: ["generate_remediation_plan"] },
  { id: "validation", label: "Deterministic validation", done: (e) => e.type === "remediation_validated", node: ["validate_remediation"] },
  { id: "risk", label: "Risk & policy", done: (e) => e.type === "risk_calculated", node: ["calculate_risk"] },
  {
    id: "approval",
    label: "Human approval",
    done: (e) => e.type === "approval_decided" || (e.type === "risk_calculated" && /automatic/.test(e.title)),
    node: ["request_human_approval", "await_approval"],
  },
  { id: "execute", label: "Remediation executed", done: (e) => e.type === "remediation_executed", node: ["execute_remediation"] },
  {
    id: "verify",
    label: "Recovery verified",
    done: (e) => e.type === "service_recovered",
    node: ["verify_recovery", "rollback_remediation"],
  },
  { id: "report", label: "Post-incident report", done: (e) => e.type === "report_generated", node: ["generate_incident_report", "close_incident"] },
];

/** Derive the live investigation checklist from timeline events. Pure: unit-tested. */
export function deriveStages(events: TimelineEvent[], status: string, currentNode: string | null): Stage[] {
  const terminal = TERMINAL_STATUSES.has(status);
  const stages: Stage[] = STAGES.map((s) => ({ id: s.id, label: s.label, state: events.some(s.done) ? "done" : "pending" }));
  const rejected = events.filter((e) => e.type === "remediation_rejected").length;
  if (rejected) {
    const v = stages.find((s) => s.id === "validation")!;
    v.note = `${rejected} AI proposal${rejected > 1 ? "s" : ""} rejected`;
  }
  if (events.some((e) => e.type === "verification_failed")) {
    const v = stages.find((s) => s.id === "verify")!;
    v.state = "failed";
    v.note = "verification failed → rolled back";
  }
  if (events.some((e) => e.type === "approval_decided" && e.status === "danger")) {
    stages.find((s) => s.id === "approval")!.state = "failed";
  }
  if (!terminal) {
    const activeDef = STAGES.find((s) => s.node?.includes(currentNode ?? ""));
    const active = activeDef ? stages.find((s) => s.id === activeDef.id) : stages.find((s) => s.state === "pending");
    if (active && active.state === "pending") active.state = "active";
    if (status === "AWAITING_APPROVAL") {
      const a = stages.find((s) => s.id === "approval")!;
      a.state = "active";
      a.note = "waiting for approver";
    }
  } else {
    for (const s of stages) if (s.state === "pending") s.state = "skipped";
  }
  return stages;
}

const ICON: Record<StageState, ReactNode> = {
  done: <Check className="h-3.5 w-3.5 text-emerald-700" />,
  active: <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-700" />,
  pending: <Circle className="h-3.5 w-3.5 text-slate-300" />,
  failed: <X className="h-3.5 w-3.5 text-rose-700" />,
  skipped: <Circle className="h-3.5 w-3.5 text-slate-200" />,
};

export function LiveProgress({ events, status, currentNode }: { events: TimelineEvent[]; status: string; currentNode: string | null }) {
  const stages = deriveStages(events, status, currentNode);
  return (
    <Card>
      <CardHeader title="Live investigation" description={currentNode ? `LangGraph node: ${currentNode}` : undefined} />
      <CardBody className="space-y-1">
        {stages.map((s) => (
          <div key={s.id} className="flex items-center gap-2 text-sm" data-testid={`stage-${s.id}`} data-state={s.state}>
            {ICON[s.state]}
            <span className={cn(s.state === "pending" || s.state === "skipped" ? "text-slate-400" : "text-slate-800", s.state === "active" && "font-medium")}>
              {s.label}
            </span>
            {s.note && <span className={cn("text-xs", s.state === "failed" ? "text-rose-700" : "text-amber-700")}>— {s.note}</span>}
          </div>
        ))}
      </CardBody>
    </Card>
  );
}
