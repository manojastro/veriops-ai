"""Graph construction.

                    create_incident → collect_initial_context
                                     │ (fan-out, parallel)
query_metrics · query_logs · query_traces · inspect_kubernetes · inspect_database · inspect_recent_deployments
                                     │ (join)
      inspect_git_changes → retrieve_runbooks → retrieve_similar_incidents → generate_hypotheses
                                                                                  │
                            ┌──────────────── rank_hypotheses ◄───────────────────┘
                            ▼
    ┌──────────────► test_hypothesis ──► check_evidence_sufficiency ──┬─ gather ──► gather_additional_evidence ─┐
    │                       ▲                                          ├─ test_next ─────────────────────────────┤
    │                       └──────────────────────────────────────────┼─────────────────────────────────────────┘
    │                                                                  ├─ regenerate ─► generate_hypotheses
    │                                                                  ├─ escalate ──► generate_incident_report
    │                                                                  └─ remediate
    │                                                                        ▼
    │                 ┌──────────────── generate_remediation_plan ◄── revise ─┐
    │                 ▼                                                       │
    └── test_next ── validate_remediation ──────────────────────────────────┘
                      │ accepted
                      ▼
                 calculate_risk ── auto ──► execute_remediation
                      │ approval                  ▲
                      ▼                           │ approved
            request_human_approval → await_approval (interrupt) ── investigate ─► gather_additional_evidence
                                                  │ rejected
                                                  ▼
     execute_remediation → verify_recovery ─ pass ─► generate_incident_report → close_incident → END
                                  └─ fail ─► rollback_remediation ─┘
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from veriops.graph.deps import GraphDeps
from veriops.graph.nodes import (
    approval,
    evidence,
    execution,
    intake,
    knowledge,
    reasoning,
    remediation,
    reporting,
)
from veriops.graph.nodes.base import instrumented
from veriops.graph.state import InvestigationState

FAN_OUT = [
    "query_metrics",
    "query_logs",
    "query_traces",
    "inspect_kubernetes",
    "inspect_database",
    "inspect_recent_deployments",
]

NODES: dict[str, Callable[..., dict[str, Any]]] = {
    "create_incident": intake.create_incident,
    "collect_initial_context": intake.collect_initial_context,
    "query_metrics": evidence.query_metrics,
    "query_logs": evidence.query_logs,
    "query_traces": evidence.query_traces,
    "inspect_kubernetes": evidence.inspect_kubernetes,
    "inspect_database": evidence.inspect_database,
    "inspect_recent_deployments": evidence.inspect_recent_deployments,
    "inspect_git_changes": evidence.inspect_git_changes,
    "retrieve_runbooks": knowledge.retrieve_runbooks,
    "retrieve_similar_incidents": knowledge.retrieve_similar_incidents,
    "generate_hypotheses": reasoning.generate_hypotheses,
    "rank_hypotheses": reasoning.rank_hypotheses,
    "test_hypothesis": reasoning.test_hypothesis,
    "check_evidence_sufficiency": reasoning.check_evidence_sufficiency,
    "gather_additional_evidence": evidence.gather_additional_evidence,
    "generate_remediation_plan": remediation.generate_remediation_plan,
    "validate_remediation": remediation.validate_remediation,
    "calculate_risk": remediation.calculate_risk,
    "request_human_approval": approval.request_human_approval,
    "await_approval": approval.await_approval,
    "execute_remediation": execution.execute_remediation,
    "verify_recovery": execution.verify_recovery,
    "rollback_remediation": execution.rollback_remediation,
    "generate_incident_report": reporting.generate_incident_report,
    "close_incident": reporting.close_incident,
}

REPORT = "generate_incident_report"


def _route(mapping: dict[str, str], default: str = REPORT) -> Callable[[InvestigationState], str]:
    def router(state: InvestigationState) -> str:
        if state.get("fatal_error"):
            return REPORT
        return mapping.get(state.get("next_step", ""), default)

    return router


def _guard(next_node: str) -> Callable[[InvestigationState], str]:
    """Linear edge that diverts to the report when a node hit an unrecoverable error."""

    def router(state: InvestigationState) -> str:
        return REPORT if state.get("fatal_error") else next_node

    return router


def build_graph(deps: GraphDeps) -> StateGraph:
    graph = StateGraph(InvestigationState)
    for name, fn in NODES.items():
        graph.add_node(name, instrumented(name, fn, deps))

    graph.add_edge(START, "create_incident")
    graph.add_edge("create_incident", "collect_initial_context")
    for node in FAN_OUT:
        graph.add_edge("collect_initial_context", node)
    graph.add_edge(FAN_OUT, "inspect_git_changes")  # join: waits for all parallel branches
    for src, dst in [
        ("inspect_git_changes", "retrieve_runbooks"),
        ("retrieve_runbooks", "retrieve_similar_incidents"),
        ("retrieve_similar_incidents", "generate_hypotheses"),
        ("generate_hypotheses", "rank_hypotheses"),
        ("rank_hypotheses", "test_hypothesis"),
        ("test_hypothesis", "check_evidence_sufficiency"),
        ("gather_additional_evidence", "test_hypothesis"),
        ("generate_remediation_plan", "validate_remediation"),
        ("request_human_approval", "await_approval"),
    ]:
        graph.add_conditional_edges(src, _guard(dst), [dst, REPORT])

    investigation_loop = {
        "gather": "gather_additional_evidence",
        "test_next": "test_hypothesis",
        "regenerate": "generate_hypotheses",
        "remediate": "generate_remediation_plan",
        "escalate": REPORT,
    }
    graph.add_conditional_edges(
        "check_evidence_sufficiency", _route(investigation_loop), list(set(investigation_loop.values()))
    )
    validation_loop = {
        "risk": "calculate_risk",
        "revise": "generate_remediation_plan",
        "test_next": "test_hypothesis",
        "regenerate": "generate_hypotheses",
        "escalate": REPORT,
    }
    graph.add_conditional_edges("validate_remediation", _route(validation_loop), list(set(validation_loop.values())))
    policy_routes = {
        "execute": "execute_remediation",
        "approval": "request_human_approval",
        "blocked": REPORT,
        "escalate": REPORT,
    }
    graph.add_conditional_edges("calculate_risk", _route(policy_routes), list(set(policy_routes.values())))
    approval_routes = {
        "execute": "execute_remediation",
        "investigate": "gather_additional_evidence",
        "rejected": REPORT,
    }
    graph.add_conditional_edges("await_approval", _route(approval_routes), list(set(approval_routes.values())))
    graph.add_conditional_edges(
        "execute_remediation", _route({"verify": "verify_recovery"}), ["verify_recovery", REPORT]
    )
    graph.add_conditional_edges(
        "verify_recovery",
        _route({"resolved": REPORT, "rollback": "rollback_remediation"}),
        [REPORT, "rollback_remediation"],
    )
    graph.add_edge("rollback_remediation", REPORT)
    graph.add_edge(REPORT, "close_incident")
    graph.add_edge("close_incident", END)
    return graph
