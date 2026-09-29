"""Validation engine: static deterministic checks + sandbox simulation → ValidationResult."""

from __future__ import annotations

from collections.abc import Callable

from veriops.domain.enums import RiskLevel, ValidationStatus
from veriops.domain.models import SandboxResult, ValidationCheck, ValidationResult
from veriops.observability import metrics as obs_metrics
from veriops.validators.base import RemediationValidator, ValidationContext, check
from veriops.validators.rules import ALL_VALIDATORS, DatabaseRemediationValidator

SandboxRunner = Callable[[dict[str, object]], SandboxResult | None]


class ValidationEngine:
    def __init__(self, validators: list[RemediationValidator] | None = None) -> None:
        self.validators = validators or ALL_VALIDATORS

    def validator_for(self, ctx: ValidationContext) -> RemediationValidator | None:
        return next((v for v in self.validators if v.supports(ctx.plan.action.action_type)), None)

    def validate(self, ctx: ValidationContext, sandbox: SandboxRunner | None = None) -> ValidationResult:
        validator = self.validator_for(ctx)
        if validator is None:
            result = ValidationResult(
                plan_id=ctx.plan.id,
                validator="none",
                status=ValidationStatus.REJECTED,
                failed_checks=[check("validator_exists", False, "no validator for this action type (fail closed)")],
                risk_hint=RiskLevel.CRITICAL,
                summary="Rejected: unsupported action type",
            )
            obs_metrics.VALIDATIONS.labels("none", result.status.value).inc()
            return result

        checks: list[ValidationCheck] = validator.checks(ctx)
        sandbox_result: SandboxResult | None = None
        # Destructive / unsupported SQL is never sent even to the sandbox.
        static_sql_ok = not isinstance(validator, DatabaseRemediationValidator) or all(c.passed for c in checks[:3])
        if validator.simulate and sandbox is not None and static_sql_ok:
            sandbox_result = sandbox(ctx.plan.action.model_dump(mode="json"))
            if sandbox_result is None:
                checks.append(check("sandbox_available", False, "sandbox simulation failed (fail closed)"))
            else:
                for test in sandbox_result.tests:
                    checks.append(
                        check(f"sandbox:{test.name}", test.passed, f"{test.threshold} (observed {test.observed})")
                    )

        failed = [c for c in checks if not c.passed]
        passed = [c for c in checks if c.passed]
        status = ValidationStatus.ACCEPTED if not failed else ValidationStatus.REJECTED
        refutes = any(c.name in validator.refuting_checks for c in failed)
        evidence = [ref for ref in ctx.signals.evidence_refs.values()]
        summary = (
            f"{ctx.plan.action.describe()}: all {len(passed)} checks passed"
            if status == ValidationStatus.ACCEPTED
            else f"{ctx.plan.action.describe()} REJECTED: " + "; ".join(c.detail for c in failed[:4])
        )
        obs_metrics.VALIDATIONS.labels(validator.name, status.value).inc()
        return ValidationResult(
            plan_id=ctx.plan.id,
            validator=validator.name,
            status=status,
            failed_checks=failed,
            passed_checks=passed,
            evidence=evidence,
            risk_hint=validator.risk_hint if status == ValidationStatus.ACCEPTED else RiskLevel.HIGH,
            sandbox=sandbox_result,
            refutes_hypothesis=refutes,
            summary=summary,
        )
