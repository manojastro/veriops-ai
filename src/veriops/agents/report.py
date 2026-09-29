"""Report Agent: assembles the post-incident report.

Facts (timeline, evidence, decisions, metrics) are assembled deterministically from graph state; the
LLM only writes the narrative fields (summary, impact, lessons, preventive actions).
"""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import ActorType, HypothesisStatus, ValidationStatus
from veriops.domain.models import IncidentReport
from veriops.graph.state import InvestigationState
from veriops.llm.gateway import LLMGateway
from veriops.llm.schemas import LLMTask, ReportNarrative
from veriops.shared.errors import LLMError

OUTCOME_STATUS = {
    "resolved": "RESOLVED",
    "rolled_back": "ROLLED_BACK",
    "remediation_rejected": "REMEDIATION_REJECTED",
    "escalated": "ESCALATED",
    "failed": "FAILED",
}


class ReportAgent:
    name = "report_agent"

    def __init__(self, llm: LLMGateway) -> None:
        self.llm = llm

    def build(self, state: InvestigationState) -> IncidentReport:
        alert = state["alert_data"]
        outcome = "failed" if state.get("fatal_error") else state.get("outcome", "escalated")
        hypothesis = state.get("selected_hypothesis")
        validations = {v.plan_id: v for v in state.get("validation_results", [])}
        plans = state.get("remediation_history", [])
        rejected_plans = [
            p for p in plans if p.id in validations and validations[p.id].status == ValidationStatus.REJECTED
        ]
        final_plan = state.get("proposed_remediation")
        final_validation = validations.get(final_plan.id) if final_plan else None
        verification = state.get("verification_result")
        approval = state.get("approval")
        timeline = state.get("timeline", [])
        audits = state.get("audit_events", [])

        root_cause = hypothesis.title if hypothesis else "Root cause not confirmed"
        test = next(
            (
                t
                for t in reversed(state.get("tested_hypotheses", []))
                if hypothesis and t.hypothesis_id == hypothesis.id
            ),
            None,
        )
        rc_evidence = sorted(
            set((test.evidence_refs if test else []) + (hypothesis.evidence_for if hypothesis else []))
        )
        impact_default = (
            f"{alert.service} ({alert.environment}): p95 latency {alert.p95_latency_ms:.0f} ms, "
            f"error rate {alert.error_rate_pct:.1f}%"
        )
        context: dict[str, Any] = {
            "title": alert.title,
            "root_cause": root_cause,
            "impact": impact_default,
            "root_cause_category": hypothesis.category.value if hypothesis else "",
            "rejected_remediations": [
                {"action_type": p.action.action_type.value, "description": p.action.describe()} for p in rejected_plans
            ],
            "final_remediation": final_plan.action.describe() if final_plan else None,
            "outcome": outcome,
        }
        try:
            narrative = self.llm.structured(
                LLMTask.WRITE_REPORT, context, agent=self.name, incident_id=state["incident_id"], schema=ReportNarrative
            )
        except LLMError:
            narrative = ReportNarrative(
                summary=f"{alert.title}: {root_cause}.",
                impact=impact_default,
                lessons_learned=[],
                preventive_actions=[],
            )

        ai_decisions = [
            f"{a.agent}: {a.action} -> {a.decision}" + (f" ({a.reason[:160]})" if a.reason else "")
            for a in audits
            if a.actor_type in (ActorType.AGENT,) and a.decision
        ]
        human_decisions = (
            [
                f"{approval.actor} ({approval.role}) {approval.status.value}"
                + (f": {approval.comment}" if approval.comment else "")
            ]
            if approval
            else []
        )
        execution = state.get("execution_result")
        executed_ok = execution is not None and execution.success
        final_plan_payload = None
        if final_plan and (executed_ok or outcome in ("resolved", "rolled_back")):
            final_plan_payload = {
                "action": final_plan.action.describe(),
                "rationale": final_plan.rationale,
                "rollback_strategy": final_plan.rollback_strategy,
                "approved_by": approval.actor if approval else "policy (auto)",
            }
        return IncidentReport(
            incident_id=state["incident_id"],
            title=alert.title,
            status=OUTCOME_STATUS.get(outcome, outcome.upper()),
            summary=narrative.summary,
            impact=narrative.impact,
            detection=f"Alert '{alert.title}' fired at {alert.fired_at.isoformat()} for {alert.service}",
            timeline=timeline,
            evidence=list(state.get("evidence", {}).values()),
            root_cause=root_cause if hypothesis else "Root cause not confirmed; escalated to on-call",
            root_cause_evidence=rc_evidence,
            rejected_hypotheses=[h for h in state.get("hypotheses", []) if h.status == HypothesisStatus.REFUTED],
            rejected_remediations=[
                {
                    "action": p.action.describe(),
                    "action_type": p.action.action_type.value,
                    "validator": validations[p.id].validator,
                    "reasons": [c.detail for c in validations[p.id].failed_checks],
                }
                for p in rejected_plans
            ],
            final_remediation=final_plan_payload,
            validation=final_validation.model_dump(mode="json", include={"validator", "status", "summary", "sandbox"})
            if final_validation and final_validation.status == ValidationStatus.ACCEPTED
            else None,
            recovery_metrics=verification.model_dump(mode="json", include={"passed", "before", "after"})
            if verification
            else None,
            lessons_learned=narrative.lessons_learned,
            preventive_actions=narrative.preventive_actions,
            ai_decisions=ai_decisions[:40],
            human_decisions=human_decisions,
        )


def report_to_markdown(report: IncidentReport) -> str:
    lines = [
        f"# Post-incident report: {report.title}",
        "",
        f"**Incident:** {report.incident_id}  ",
        f"**Status:** {report.status}  ",
        f"**Generated:** {report.generated_at.isoformat()}",
        "",
        "## Summary",
        report.summary,
        "",
        "## Impact",
        report.impact,
        "",
        "## Detection",
        report.detection,
        "",
        "## Root cause",
        report.root_cause,
        "",
    ]
    if report.root_cause_evidence:
        lines += ["Evidence: " + ", ".join(f"`{e}`" for e in report.root_cause_evidence), ""]
    lines += ["## Timeline", "", "| Time (UTC) | Event |", "|---|---|"]
    lines += [f"| {e.ts.strftime('%H:%M:%S')} | {e.title.replace('|', '/')} |" for e in report.timeline]
    lines += ["", "## Evidence", ""]
    lines += [f"- `{e.id}` **{e.kind.value}** — {e.summary}" for e in report.evidence]
    lines += ["", "## Rejected hypotheses", ""]
    lines += [f"- {h.title} ({h.category.value})" for h in report.rejected_hypotheses] or ["- none"]
    lines += ["", "## Rejected remediations", ""]
    for r in report.rejected_remediations:
        lines.append(f"- **{r['action']}** — rejected by {r['validator']}")
        lines += [f"  - {reason}" for reason in r["reasons"]]
    if not report.rejected_remediations:
        lines.append("- none")
    lines += ["", "## Final remediation", ""]
    if report.final_remediation:
        lines += [
            f"- Action: {report.final_remediation['action']}",
            f"- Rationale: {report.final_remediation['rationale']}",
            f"- Rollback strategy: {report.final_remediation['rollback_strategy']}",
            f"- Approved by: {report.final_remediation['approved_by']}",
        ]
    else:
        lines.append("- none executed")
    if report.validation and report.validation.get("sandbox"):
        sb = report.validation["sandbox"]
        lines += [
            "",
            "## Validation",
            "",
            f"Validator: {report.validation['validator']} — {report.validation['summary']}",
            "",
            "| Test | Result | Threshold | Observed |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {t['name']} | {'PASS' if t['passed'] else 'FAIL'} | {t['threshold']} | {t['observed']} |"
            for t in sb["tests"]
        ]
    if report.recovery_metrics:
        rm = report.recovery_metrics
        lines += ["", "## Recovery metrics", "", "| Metric | Before | After |", "|---|---|---|"]
        lines += [f"| {k} | {rm['before'].get(k, '-')} | {rm['after'].get(k, '-')} |" for k in rm.get("after", {})]
    lines += ["", "## Lessons learned", ""] + [f"- {x}" for x in report.lessons_learned]
    lines += ["", "## Preventive actions", ""] + [f"- [ ] {x}" for x in report.preventive_actions]
    lines += ["", "## AI decisions", ""] + [f"- {x}" for x in report.ai_decisions]
    lines += ["", "## Human decisions", ""] + ([f"- {x}" for x in report.human_decisions] or ["- none"])
    return "\n".join(lines) + "\n"
