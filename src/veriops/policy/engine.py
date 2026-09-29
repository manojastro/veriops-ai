"""Policy engine: maps (action, validation, risk, environment) to an execution decision.

| Class                          | Decision                                   |
|--------------------------------|--------------------------------------------|
| read-only operations           | automatic (tool registry)                  |
| LOW risk remediation           | may run automatically*                     |
| MEDIUM risk remediation        | human approval required                    |
| HIGH risk remediation          | approval + mandatory comment + re-sandbox  |
| CRITICAL risk / destructive    | blocked                                    |

*Production changes that need a HIGH_RISK_WRITE tool always require a human, even at LOW risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from veriops.domain.enums import ActionType, PolicyDecision, RiskLevel, ToolPermission, ValidationStatus
from veriops.domain.models import RemediationPlan, ValidationResult

NEVER_AUTONOMOUS = frozenset(
    {
        ActionType.DROP_TABLE,
        ActionType.DELETE_DATABASE,
        ActionType.DESTROY_INFRASTRUCTURE,
        ActionType.ROTATE_CORE_CREDENTIALS,
        ActionType.DISABLE_SECURITY_CONTROLS,
        ActionType.REMOVE_BACKUPS,
    }
)
ACTION_TOOL_PERMISSION = {
    ActionType.SCALE_DEPLOYMENT: ToolPermission.LOW_RISK_WRITE,
    ActionType.RESTART_DEPLOYMENT: ToolPermission.LOW_RISK_WRITE,
    ActionType.ROLLBACK_DEPLOYMENT: ToolPermission.HIGH_RISK_WRITE,
    ActionType.CONFIGURATION_CHANGE: ToolPermission.HIGH_RISK_WRITE,
    ActionType.DATABASE_REMEDIATION: ToolPermission.HIGH_RISK_WRITE,
    ActionType.ESCALATE: ToolPermission.READ_ONLY,
}


@dataclass(frozen=True)
class PolicyOutcome:
    decision: PolicyDecision
    reasons: list[str]
    required_approvals: int
    tool_permission: ToolPermission


class PolicyEngine:
    def decide(
        self, plan: RemediationPlan, validation: ValidationResult, level: RiskLevel, environment: str
    ) -> PolicyOutcome:
        action = plan.action.action_type
        permission = ACTION_TOOL_PERMISSION.get(action, ToolPermission.BLOCKED)
        if action in NEVER_AUTONOMOUS or permission == ToolPermission.BLOCKED:
            return PolicyOutcome(
                PolicyDecision.BLOCKED, [f"{action.value} is on the never-autonomous list"], 0, ToolPermission.BLOCKED
            )
        if validation.status != ValidationStatus.ACCEPTED:
            return PolicyOutcome(
                PolicyDecision.BLOCKED, ["remediation did not pass deterministic validation"], 0, permission
            )
        if level == RiskLevel.CRITICAL:
            return PolicyOutcome(PolicyDecision.BLOCKED, ["CRITICAL risk actions are blocked"], 0, permission)
        if level == RiskLevel.HIGH:
            return PolicyOutcome(
                PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS,
                ["HIGH risk: approver sign-off with justification and a fresh sandbox run"],
                1,
                permission,
            )
        if level == RiskLevel.MEDIUM:
            return PolicyOutcome(
                PolicyDecision.REQUIRE_APPROVAL, ["MEDIUM risk: human approval required"], 1, permission
            )
        if environment == "production" and permission == ToolPermission.HIGH_RISK_WRITE:
            return PolicyOutcome(
                PolicyDecision.REQUIRE_APPROVAL,
                ["LOW risk, but production HIGH_RISK_WRITE tools always need a human"],
                1,
                permission,
            )
        return PolicyOutcome(
            PolicyDecision.AUTO_EXECUTE, ["LOW risk: policy permits automatic execution"], 0, permission
        )
