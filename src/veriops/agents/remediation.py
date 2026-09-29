"""Remediation Agent: proposes one remediation for the selected hypothesis.

The proposal is only a candidate. It must pass the deterministic ValidationEngine (including a
sandbox run), the RiskEngine and the PolicyEngine before anything executes.
"""

from __future__ import annotations

import logging

from veriops.agents.signals import IncidentSignals
from veriops.domain.enums import ActionType
from veriops.domain.models import (
    DocumentCitation,
    Hypothesis,
    RemediationAction,
    RemediationPlan,
    ValidationResult,
)
from veriops.llm.gateway import LLMGateway
from veriops.llm.schemas import LLMTask, RemediationProposal
from veriops.shared.errors import LLMError

logger = logging.getLogger(__name__)

ALLOWED_ACTIONS = [
    ActionType.SCALE_DEPLOYMENT,
    ActionType.RESTART_DEPLOYMENT,
    ActionType.ROLLBACK_DEPLOYMENT,
    ActionType.CONFIGURATION_CHANGE,
    ActionType.DATABASE_REMEDIATION,
    ActionType.ESCALATE,
]


class RemediationAgent:
    name = "remediation_agent"

    def __init__(self, llm: LLMGateway) -> None:
        self.llm = llm

    def propose(
        self,
        incident_id: str,
        hypothesis: Hypothesis,
        signals: IncidentSignals,
        knowledge: list[DocumentCitation],
        rejected: list[tuple[RemediationPlan, ValidationResult]],
        attempt: int,
    ) -> RemediationPlan:
        context = {
            "hypothesis": hypothesis.model_dump(
                mode="json", include={"title", "category", "description", "suspect_deployment_id"}
            ),
            "signals": signals.model_dump(mode="json"),
            "knowledge": [{"chunk_id": c.chunk_id, "title": c.title, "snippet": c.snippet} for c in knowledge[:4]],
            "rejected_proposals": [
                {
                    "action_type": p.action.action_type.value,
                    "description": p.action.describe(),
                    "reasons": [c.detail for c in v.failed_checks][:5],
                }
                for p, v in rejected
            ],
            "allowed_actions": [a.value for a in ALLOWED_ACTIONS],
        }
        try:
            proposal = self.llm.structured(
                LLMTask.PROPOSE_REMEDIATION,
                context,
                agent=self.name,
                incident_id=incident_id,
                schema=RemediationProposal,
            )
        except LLMError as exc:
            logger.warning("remediation proposal failed, escalating: %s", exc)
            proposal = RemediationProposal(
                action_type=ActionType.ESCALATE,
                rationale=f"LLM unavailable: {exc}",
                expected_impact="Human responder takes over.",
                rollback_strategy="n/a",
            )
        action = RemediationAction(
            action_type=proposal.action_type,
            service=signals.service,
            environment=signals.environment,
            replicas=proposal.replicas,
            deployment_id=proposal.deployment_id,
            config_key=proposal.config_key,
            config_value=proposal.config_value,
            sql=proposal.sql,
        )
        known_chunks = {c.chunk_id for c in knowledge}
        return RemediationPlan(
            hypothesis_id=hypothesis.id,
            action=action,
            rationale=proposal.rationale,
            expected_impact=proposal.expected_impact,
            rollback_strategy=proposal.rollback_strategy,
            evidence_refs=[r for r in proposal.evidence_refs if r in signals.evidence_refs.values()],
            citations=[c for c in proposal.citations if c in known_chunks],
            attempt=attempt,
        )
