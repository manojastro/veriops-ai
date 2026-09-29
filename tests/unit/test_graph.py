"""End-to-end LangGraph workflow tests (in-process simulator, simulated LLM, in-memory checkpointer)."""

from __future__ import annotations

import pytest

from veriops.bootstrap import Platform, alert_from_activation, build_platform
from veriops.domain.enums import (
    ActionType,
    ApprovalStatus,
    HypothesisCategory,
    HypothesisStatus,
    IncidentStatus,
    ValidationStatus,
)
from veriops.domain.models import Alert, ApprovalDecision
from veriops.graph.builder import NODES
from veriops.graph.runner import InvestigationRunner
from veriops.llm.providers.base import ProviderRequest, ProviderResponse
from veriops.llm.providers.simulated import SimulatedProvider
from veriops.llm.schemas import LLMTask
from veriops.shared.config import Settings
from veriops.shared.errors import LLMError
from veriops.simulator.world import SimulatorWorld

APPROVE = ApprovalDecision(status=ApprovalStatus.APPROVED, actor="alice", role="approver", comment="looks right")


def types(platform: Platform, incident_id: str) -> list[str]:
    return [e.type for e in platform.recorder.events[incident_id]]  # type: ignore[attr-defined]


def test_graph_contains_all_required_nodes() -> None:
    required = {
        "create_incident",
        "collect_initial_context",
        "query_metrics",
        "query_logs",
        "query_traces",
        "inspect_kubernetes",
        "inspect_database",
        "inspect_recent_deployments",
        "inspect_git_changes",
        "retrieve_runbooks",
        "retrieve_similar_incidents",
        "generate_hypotheses",
        "rank_hypotheses",
        "test_hypothesis",
        "check_evidence_sufficiency",
        "gather_additional_evidence",
        "generate_remediation_plan",
        "validate_remediation",
        "calculate_risk",
        "request_human_approval",
        "execute_remediation",
        "verify_recovery",
        "rollback_remediation",
        "generate_incident_report",
        "close_incident",
    }
    assert required <= set(NODES)


@pytest.mark.critical
def test_flagship_flow_rejects_scaling_and_pauses_for_approval(
    runner: InvestigationRunner, platform: Platform, flagship_alert: Alert
) -> None:
    out = runner.start("inc-flagship", flagship_alert)
    state = out.state

    # Human-in-the-loop: paused at the approval interrupt.
    assert out.interrupted and out.next_nodes == ("await_approval",)
    assert state["status"] == IncidentStatus.AWAITING_APPROVAL

    # The AI first proposed scaling, and deterministic validation rejected it.
    plans = state["remediation_history"]
    validations = {v.plan_id: v for v in state["validation_results"]}
    assert plans[0].action.action_type == ActionType.SCALE_DEPLOYMENT and plans[0].action.replicas == 8
    assert validations[plans[0].id].status == ValidationStatus.REJECTED
    capacity = next(h for h in state["hypotheses"] if h.category == HypothesisCategory.CAPACITY)
    assert capacity.status == HypothesisStatus.REFUTED

    # DB regression selected, rollback validated in the sandbox with the expected before/after.
    assert state["selected_hypothesis"].category == HypothesisCategory.DB_QUERY_REGRESSION
    final = state["proposed_remediation"]
    assert final.action.action_type == ActionType.ROLLBACK_DEPLOYMENT and final.action.deployment_id == "842"
    accepted = validations[final.id]
    assert accepted.status == ValidationStatus.ACCEPTED
    assert accepted.sandbox.before["p95_latency_ms"] == 4200 and accepted.sandbox.after["p95_latency_ms"] == 310
    assert accepted.sandbox.before["error_rate_pct"] == 11.8 and accepted.sandbox.after["error_rate_pct"] == 0.4
    assert state["risk"].level.value == "MEDIUM"
    assert out.interrupt_payload["proposed_remediation"] == "Roll back deployment #842 of checkout-service"

    # Evidence loop ran (git diff fetched on demand) before the root cause was accepted.
    events = types(platform, "inc-flagship")
    assert "additional_evidence" in events
    assert events.index("remediation_rejected") < events.index(
        "root_cause_selected", events.index("remediation_rejected")
    )

    # Resume -> execute -> verify -> report -> close.
    done = runner.resume("inc-flagship", APPROVE)
    assert not done.interrupted
    assert done.state["status"] == IncidentStatus.RESOLVED
    assert done.state["verification_result"].passed
    assert done.state["verification_result"].after["p95_latency_ms"] == 310
    report = done.state["report"]
    assert report.status == "RESOLVED" and report.rejected_remediations
    assert report.rejected_remediations[0]["action_type"] == "scale_deployment"
    assert report.root_cause_evidence, "root cause must reference evidence"
    assert any("alice" in d for d in report.human_decisions)


def test_rejection_by_human_executes_nothing(
    runner: InvestigationRunner, world: SimulatorWorld, flagship_alert: Alert
) -> None:
    runner.start("inc-reject", flagship_alert)
    done = runner.resume(
        "inc-reject",
        ApprovalDecision(status=ApprovalStatus.REJECTED, actor="alice", role="approver", comment="not during peak"),
    )
    assert done.state["status"] == IncidentStatus.REMEDIATION_REJECTED
    assert done.state.get("execution_result") is None
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 4200


@pytest.mark.critical
def test_viewer_role_cannot_approve_even_if_api_is_bypassed(
    runner: InvestigationRunner, world: SimulatorWorld, flagship_alert: Alert
) -> None:
    runner.start("inc-viewer", flagship_alert)
    done = runner.resume("inc-viewer", ApprovalDecision(status=ApprovalStatus.APPROVED, actor="victor", role="viewer"))
    assert done.state["status"] == IncidentStatus.REMEDIATION_REJECTED
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 4200


def test_failed_verification_triggers_automatic_rollback(
    runner: InvestigationRunner, world: SimulatorWorld, flagship_alert: Alert
) -> None:
    world.inject_verification_failure("checkout-service")
    runner.start("inc-chaos", flagship_alert)
    done = runner.resume("inc-chaos", APPROVE)
    assert done.state["verification_result"].passed is False
    assert done.state["rollback_result"].success
    assert done.state["status"] == IncidentStatus.ROLLED_BACK
    active = [d for d in world.state("checkout-service").deployments if d.status == "active"]
    assert active[0].spec.id == "842", "the remediation itself was undone"


def test_more_investigation_loops_back_and_requests_approval_again(
    runner: InvestigationRunner, platform: Platform, flagship_alert: Alert
) -> None:
    runner.start("inc-more", flagship_alert)
    again = runner.resume(
        "inc-more",
        ApprovalDecision(
            status=ApprovalStatus.MORE_INVESTIGATION, actor="alice", role="approver", comment="check dependencies too"
        ),
    )
    assert again.interrupted
    assert types(platform, "inc-more").count("approval_requested") == 2
    done = runner.resume("inc-more", APPROVE)
    assert done.state["status"] == IncidentStatus.RESOLVED


def test_low_risk_staging_rollback_executes_without_approval(settings: Settings) -> None:
    world = SimulatorWorld()
    p = build_platform(settings, world=world)
    p.ingestor.ingest_directory()
    out = InvestigationRunner(p.graph_deps()).start(
        "inc-stg", alert_from_activation(world.activate("checkout_bad_deployment_staging"))
    )
    assert not out.interrupted
    assert out.state["risk"].level.value == "LOW"
    assert out.state["status"] == IncidentStatus.RESOLVED


class _FailingProvider:
    name = "failing"

    def __init__(self, fail_tasks: set[LLMTask]) -> None:
        self.inner = SimulatedProvider()
        self.fail = fail_tasks

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.task in self.fail:
            raise LLMError("simulated outage", retryable=False)
        return self.inner.complete(request)


def test_llm_outage_escalates_safely(settings: Settings) -> None:
    world = SimulatorWorld()
    p = build_platform(settings, world=world, provider=_FailingProvider({LLMTask.GENERATE_HYPOTHESES}))
    p.ingestor.ingest_directory()
    out = InvestigationRunner(p.graph_deps()).start(
        "inc-llm", alert_from_activation(world.activate("checkout_db_regression"))
    )
    assert not out.interrupted
    assert out.state["status"] == IncidentStatus.ESCALATED
    assert out.state.get("execution_result") is None


class _GarbageThenValid:
    name = "garbage"

    def __init__(self) -> None:
        self.inner = SimulatedProvider()
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if request.repair_hint is None and request.task == LLMTask.GENERATE_HYPOTHESES:
            return ProviderResponse(
                raw={"hypotheses": [{"title": "x", "probability": 7}]}, prompt_tokens=1, completion_tokens=1
            )
        return self.inner.complete(request)


def test_invalid_structured_output_is_repaired(settings: Settings) -> None:
    world = SimulatorWorld()
    provider = _GarbageThenValid()
    p = build_platform(settings, world=world, provider=provider)
    p.ingestor.ingest_directory()
    out = InvestigationRunner(p.graph_deps()).start(
        "inc-repair", alert_from_activation(world.activate("checkout_db_regression"))
    )
    assert out.interrupted
    assert any(not c.success for c in p.llm.ledger)


def test_missing_telemetry_is_tolerated(settings: Settings) -> None:
    world = SimulatorWorld()
    p = build_platform(settings, world=world)
    p.ingestor.ingest_directory()
    original = p.backend.trace

    def broken(*args: object, **kwargs: object) -> object:
        from veriops.shared.errors import ToolError

        raise ToolError("trace", "tracing backend down", retryable=True)

    p.backend.trace = broken  # type: ignore[method-assign]
    p.tools.backoff_base_s = 0.001
    out = InvestigationRunner(p.graph_deps()).start(
        "inc-notrace", alert_from_activation(world.activate("checkout_db_regression"))
    )
    assert out.interrupted, "partial evidence must not stop the investigation"
    assert "evidence_error" in types(p, "inc-notrace")
    p.backend.trace = original  # type: ignore[method-assign]
