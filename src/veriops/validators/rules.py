"""Deterministic remediation validators, one per action class."""

from __future__ import annotations

from veriops.domain.enums import ActionType, EvidenceKind, HypothesisCategory, RiskLevel
from veriops.domain.models import ValidationCheck
from veriops.validators.base import RemediationValidator, ValidationContext, check

CPU_SCALE_THRESHOLD = 70.0
POOL_SATURATION_THRESHOLD = 90.0
QUERY_REGRESSION_FACTOR = 3.0
MEMORY_PRESSURE_THRESHOLD = 85.0
MUTABLE_CONFIG_KEYS = frozenset(
    {
        "payment.gateway.provider",
        "cache.ttl_seconds",
        "http.timeout_ms",
        "log.level",
        "db.pool.max_size",
    }
)
SENSITIVE_KEY_TOKENS = ("auth", "tls", "security", "password", "secret", "token", "credential", "audit", "backup")
E = EvidenceKind


class ScaleDeploymentValidator(RemediationValidator):
    name = "ScaleDeploymentValidator"
    action_types = frozenset({ActionType.SCALE_DEPLOYMENT})
    required_evidence = frozenset(
        {E.METRICS, E.KUBERNETES, E.DATABASE, E.CONNECTION_POOL, E.SLOW_QUERIES, E.DEPLOYMENTS}
    )
    risk_hint = RiskLevel.MEDIUM
    refuting_checks = frozenset({"cpu_justifies_scaling"})

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        s, action = ctx.signals, ctx.plan.action
        replicas = s.replicas or 0
        target = action.replicas or 0
        out = [
            check(
                "cpu_justifies_scaling",
                s.cpu_pct >= CPU_SCALE_THRESHOLD,
                f"CPU utilization {s.cpu_pct:.0f}% "
                + ("justifies" if s.cpu_pct >= CPU_SCALE_THRESHOLD else "does not justify")
                + f" scaling (threshold {CPU_SCALE_THRESHOLD:.0f}%)",
                cpu_pct=s.cpu_pct,
                memory_pct=s.memory_pct,
            ),
            check(
                "db_pool_headroom",
                s.db_pool_utilization_pct <= POOL_SATURATION_THRESHOLD,
                f"DB connection pool at {s.db_pool_utilization_pct:.0f}%"
                + (
                    ""
                    if s.db_pool_utilization_pct <= POOL_SATURATION_THRESHOLD
                    else ": scaling application replicas would increase database connection pressure"
                ),
                db_pool_utilization_pct=s.db_pool_utilization_pct,
            ),
            check(
                "scale_up_only",
                target > replicas,
                f"target {target} replicas vs current {replicas}",
                current=replicas,
                target=target,
            ),
            check(
                "within_hpa_bounds",
                s.hpa_max is not None and target <= s.hpa_max,
                f"target {target} within HPA max {s.hpa_max}",
                hpa_max=s.hpa_max,
            ),
        ]
        if s.db_active_connections is None or s.db_max_connections is None or s.pool_per_pod is None:
            out.append(check("db_connection_capacity", False, "database connection evidence missing (fail closed)"))
        else:
            projected = round(s.db_active_connections + s.pool_per_pod * (target - replicas))
            out.append(
                check(
                    "db_connection_capacity",
                    projected <= s.db_max_connections,
                    f"projected DB connections {projected} vs max_connections {s.db_max_connections}",
                    projected=projected,
                    max_connections=s.db_max_connections,
                )
            )
        regression = s.slow_query_max_regression
        deploy = s.recent_deployment
        if regression is None:
            out.append(check("no_query_regression", False, "slow query evidence missing (fail closed)"))
        else:
            detail = (
                (
                    f"Query latency increased {regression}x"
                    + (f" after deployment #{deploy.id}" if deploy and s.slow_query_after_deployment else "")
                )
                if regression >= QUERY_REGRESSION_FACTOR
                else f"no query regression (max {regression}x baseline)"
            )
            out.append(
                check(
                    "no_query_regression",
                    regression < QUERY_REGRESSION_FACTOR,
                    detail,
                    max_regression_factor=regression,
                    deployment=deploy.id if deploy else None,
                )
            )
        if s.unhealthy_dependencies:
            out.append(
                check(
                    "dependencies_healthy",
                    False,
                    f"unhealthy dependencies {s.unhealthy_dependencies}: scaling does not help",
                )
            )
        return out


class RollbackDeploymentValidator(RemediationValidator):
    name = "RollbackDeploymentValidator"
    action_types = frozenset({ActionType.ROLLBACK_DEPLOYMENT})
    required_evidence = frozenset({E.DEPLOYMENTS, E.METRICS})
    risk_hint = RiskLevel.MEDIUM
    refuting_checks = frozenset({"deployment_correlates_with_onset"})

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        s, action = ctx.signals, ctx.plan.action
        active = ctx.active_deployment
        ids = [d["id"] for d in ctx.deployments]
        target = action.deployment_id
        previous_available = target in ids and ids.index(target) + 1 < len(ids)
        deploy = s.recent_deployment
        correlated = bool(
            deploy
            and deploy.id == target
            and (
                s.deployment_precedes_onset
                or s.slow_query_after_deployment
                or "exception" in s.log_keywords
                or s.oom_killed
                or s.db_pool_utilization_pct >= 95
            )
        )
        return [
            check(
                "deployment_is_active",
                bool(active and active["id"] == target),
                f"deployment #{target} is "
                + (
                    "the active revision"
                    if active and active["id"] == target
                    else f"not active (active: #{active['id'] if active else 'none'})"
                ),
            ),
            check(
                "previous_revision_available",
                previous_available,
                "a previous known-good revision exists" if previous_available else "no previous revision",
            ),
            check(
                "deployment_recent",
                bool(deploy and deploy.id == target),
                f"deployment #{target} deployed {deploy.minutes_ago:.0f} min ago"
                if deploy and deploy.id == target
                else "deployment is not recent (older than 6h)",
            ),
            check(
                "deployment_correlates_with_onset",
                correlated,
                "symptom onset follows the deployment"
                if correlated
                else "no evidence that this deployment caused the symptoms",
            ),
        ]


class RestartDeploymentValidator(RemediationValidator):
    name = "RestartDeploymentValidator"
    action_types = frozenset({ActionType.RESTART_DEPLOYMENT})
    required_evidence = frozenset({E.METRICS, E.KUBERNETES})
    risk_hint = RiskLevel.LOW
    refuting_checks = frozenset({"restart_addresses_cause"})

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        s = ctx.signals
        category = ctx.hypothesis.category if ctx.hypothesis else None
        leak = category in (HypothesisCategory.MEMORY_LEAK, HypothesisCategory.CONNECTION_LEAK)
        leak_signals = (
            s.memory_pct >= MEMORY_PRESSURE_THRESHOLD
            or s.oom_killed
            or (s.db_pool_utilization_pct >= 95 and (s.slow_query_max_regression or 1) < 1.5)
        )
        regression = s.slow_query_max_regression or 1.0
        return [
            check(
                "restart_addresses_cause",
                leak and leak_signals,
                "leak signals present (memory/OOM or leaked connections)"
                if leak and leak_signals
                else "a restart does not address the suspected cause",
            ),
            check(
                "capacity_during_restart",
                (s.replicas or 0) >= 2,
                f"{s.replicas} replicas allow a rolling restart without downtime",
            ),
            check("no_query_regression", regression < QUERY_REGRESSION_FACTOR, f"max query regression {regression}x"),
        ]


class DatabaseRemediationValidator(RemediationValidator):
    name = "DatabaseRemediationValidator"
    action_types = frozenset({ActionType.DATABASE_REMEDIATION})
    required_evidence = frozenset({E.SLOW_QUERIES, E.DATABASE})
    risk_hint = RiskLevel.HIGH

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        sql = (ctx.plan.action.sql or "").strip()
        lowered = f" {sql.lower()} "
        destructive = any(t in lowered for t in (" drop ", " truncate ", " delete ", " alter ", " grant ", " revoke "))
        regression = ctx.signals.slow_query_max_regression or 1.0
        return [
            check(
                "statement_allowlisted",
                lowered.strip().startswith("create index concurrently"),
                "only CREATE INDEX CONCURRENTLY is permitted during incidents",
            ),
            check("single_statement", ";" not in sql.rstrip(";"), "exactly one statement"),
            check(
                "no_destructive_tokens",
                not destructive,
                "destructive statement detected" if destructive else "no destructive tokens",
            ),
            check(
                "query_regression_confirmed",
                regression >= QUERY_REGRESSION_FACTOR,
                f"query regression {regression}x baseline",
            ),
        ]


class ConfigurationChangeValidator(RemediationValidator):
    name = "ConfigurationChangeValidator"
    action_types = frozenset({ActionType.CONFIGURATION_CHANGE})
    required_evidence = frozenset({E.CONFIG_HISTORY, E.METRICS})
    risk_hint = RiskLevel.MEDIUM

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        key = ctx.plan.action.config_key or ""
        value = ctx.plan.action.config_value or ""
        s = ctx.signals
        reverts = any(c.key == key and c.old_value == value for c in s.recent_config_changes or [])
        documented = any(key in c.snippet and value in c.snippet for c in ctx.citations)
        sensitive = any(tok in key.lower() for tok in SENSITIVE_KEY_TOKENS)
        pool_increase = key == "db.pool.max_size" and s.db_pool_utilization_pct >= POOL_SATURATION_THRESHOLD
        return [
            check(
                "key_is_mutable",
                key in MUTABLE_CONFIG_KEYS,
                f"{key} is on the runtime-mutable allow-list"
                if key in MUTABLE_CONFIG_KEYS
                else f"{key} is not a runtime-mutable key",
            ),
            check(
                "not_security_sensitive",
                not sensitive,
                "security-sensitive keys cannot be changed by automation"
                if sensitive
                else "key is not security-sensitive",
            ),
            check(
                "revert_or_documented",
                reverts or documented,
                "reverts a recent change"
                if reverts
                else ("documented in a cited runbook" if documented else "neither a revert nor a documented procedure"),
            ),
            check(
                "no_pool_increase_under_saturation",
                not pool_increase,
                "raising pool size against a saturated database" if pool_increase else "ok",
            ),
        ]


class EscalationValidator(RemediationValidator):
    name = "EscalationValidator"
    action_types = frozenset({ActionType.ESCALATE})
    risk_hint = RiskLevel.LOW
    simulate = False

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        return [check("escalation_has_no_side_effects", True, "escalation pages humans; no system change")]


class DestructiveActionValidator(RemediationValidator):
    name = "DestructiveActionValidator"
    action_types = frozenset(
        {
            ActionType.DROP_TABLE,
            ActionType.DELETE_DATABASE,
            ActionType.DESTROY_INFRASTRUCTURE,
            ActionType.ROTATE_CORE_CREDENTIALS,
            ActionType.DISABLE_SECURITY_CONTROLS,
            ActionType.REMOVE_BACKUPS,
        }
    )
    risk_hint = RiskLevel.CRITICAL
    simulate = False

    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]:
        return [
            check(
                "never_autonomous",
                False,
                f"{ctx.plan.action.action_type.value} is destructive and can never be executed by VeriOps",
            )
        ]


ALL_VALIDATORS: list[RemediationValidator] = [
    ScaleDeploymentValidator(),
    RollbackDeploymentValidator(),
    RestartDeploymentValidator(),
    DatabaseRemediationValidator(),
    ConfigurationChangeValidator(),
    EscalationValidator(),
    DestructiveActionValidator(),
]
