"""The tool catalog: input schemas, permission classes and handlers.

Handlers are thin: they validate nothing themselves (the registry does) and delegate to an
``InfrastructureBackend`` or the knowledge retriever. Every handler returns structured data.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from veriops.domain.enums import ToolPermission
from veriops.domain.models import DocumentCitation
from veriops.shared.errors import ToolError
from veriops.tools.backends import InfrastructureBackend
from veriops.tools.registry import ToolRegistry, ToolSpec

SERVICE_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"


class KnowledgeSearch(Protocol):
    def search(
        self, query: str, *, document_types: list[str] | None = None, service: str | None = None, top_k: int = 5
    ) -> list[DocumentCitation]: ...


# --- input models ---------------------------------------------------------------------------


class ServiceInput(BaseModel):
    service: str = Field(pattern=SERVICE_PATTERN)


class MetricsInput(ServiceInput):
    window_minutes: int = Field(default=60, ge=5, le=360)


class LogSearchInput(ServiceInput):
    query: str = Field(default="", max_length=256)
    window_minutes: int = Field(default=30, ge=1, le=360)


class TraceInput(ServiceInput):
    trace_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{8,64}$")


class RecentDeploymentsInput(ServiceInput):
    limit: int = Field(default=5, ge=1, le=50)


class CommitInput(BaseModel):
    sha: str = Field(pattern=r"^[a-f0-9]{7,40}$")


class KnowledgeInput(BaseModel):
    query: str = Field(min_length=3, max_length=512)
    service: str | None = Field(default=None, pattern=SERVICE_PATTERN)
    top_k: int = Field(default=4, ge=1, le=10)


class SandboxInput(ServiceInput):
    action: dict[str, Any]


class ScaleInput(ServiceInput):
    replicas: int = Field(ge=1, le=24)


class DeploymentInput(ServiceInput):
    deployment_id: str = Field(pattern=r"^[0-9]{1,10}$")


class ConfigInput(ServiceInput):
    key: str = Field(pattern=r"^[a-z0-9_.]{2,128}$")
    value: str = Field(max_length=256)


class SqlInput(ServiceInput):
    sql: str = Field(max_length=2000)

    @field_validator("sql")
    @classmethod
    def only_additive_ddl(cls, value: str) -> str:
        lowered = value.strip().lower()
        if not lowered.startswith("create index concurrently"):
            raise ValueError("only CREATE INDEX CONCURRENTLY statements are permitted")
        if ";" in lowered.rstrip(";"):
            raise ValueError("multiple statements are not permitted")
        return value


class BlockedInput(BaseModel):
    target: str = ""


# --- registry factory -----------------------------------------------------------------------


def build_tool_registry(
    backend: InfrastructureBackend,
    knowledge: KnowledgeSearch | None = None,
    *,
    max_retries: int = 3,
    timeout_s: float = 10.0,
) -> ToolRegistry:
    reg = ToolRegistry(max_retries=max_retries, default_timeout_s=timeout_s)
    ro = ToolPermission.READ_ONLY

    def add(
        name: str,
        description: str,
        permission: ToolPermission,
        model: type[BaseModel],
        handler: Any,
        cache_ttl_s: float = 0.0,
    ) -> None:
        reg.register(
            ToolSpec(
                name=name,
                description=description,
                permission=permission,
                input_model=model,
                handler=handler,
                cache_ttl_s=cache_ttl_s,
            )
        )

    # Read-only telemetry. Volatile production state is cached for at most a few seconds.
    add(
        "get_service_metrics",
        "Golden-signal metrics (latency, errors, saturation) with a time series.",
        ro,
        MetricsInput,
        lambda p: backend.metrics(p.service, p.window_minutes),
        cache_ttl_s=5,
    )
    add(
        "search_logs",
        "Search service logs; returns counts, top patterns and samples.",
        ro,
        LogSearchInput,
        lambda p: backend.logs(p.service, p.query, p.window_minutes),
        cache_ttl_s=5,
    )
    add(
        "get_trace",
        "Fetch a representative slow distributed trace with its critical path.",
        ro,
        TraceInput,
        lambda p: backend.trace(p.service, p.trace_id),
    )
    add(
        "get_pod_status",
        "Kubernetes pod status (restarts, OOMKills, resource usage).",
        ro,
        ServiceInput,
        lambda p: [pod.model_dump(mode="json") for pod in backend.deployment_status(p.service).pods],
    )
    add(
        "get_deployment_status",
        "Kubernetes deployment status (replicas, revision, HPA bounds).",
        ro,
        ServiceInput,
        lambda p: backend.deployment_status(p.service),
    )
    add(
        "get_recent_deployments",
        "Recent deployments of a service, newest first.",
        ro,
        RecentDeploymentsInput,
        lambda p: backend.deployments(p.service, p.limit),
    )
    add("get_git_commit", "Git commit metadata.", ro, CommitInput, lambda p: backend.commit(p.sha), cache_ttl_s=3600)
    add("get_git_diff", "Git diff for a commit.", ro, CommitInput, lambda p: backend.diff(p.sha), cache_ttl_s=3600)
    add(
        "get_database_metrics",
        "Database-level metrics for the service's primary database.",
        ro,
        ServiceInput,
        lambda p: backend.database_metrics(p.service),
    )
    add(
        "get_slow_queries",
        "Slow query statistics with baseline comparison and plans.",
        ro,
        ServiceInput,
        lambda p: backend.slow_queries(p.service),
    )
    add(
        "get_connection_pool_stats",
        "Application DB connection pool statistics.",
        ro,
        ServiceInput,
        lambda p: backend.connection_pool(p.service),
    )
    add(
        "get_config_history",
        "Recent runtime configuration changes.",
        ro,
        ServiceInput,
        lambda p: backend.config_history(p.service),
    )
    add(
        "get_dependency_health",
        "Health of upstream dependencies.",
        ro,
        ServiceInput,
        lambda p: backend.dependencies(p.service),
    )
    add(
        "run_health_check",
        "Run health checks against SLO thresholds.",
        ro,
        ServiceInput,
        lambda p: backend.health(p.service),
    )
    add(
        "simulate_remediation",
        "Apply a remediation to an isolated sandbox copy and run validation tests.",
        ro,
        SandboxInput,
        lambda p: backend.sandbox(p.service, p.action),
    )

    def _knowledge(doc_types: list[str]) -> Any:
        def handler(p: KnowledgeInput) -> list[dict[str, Any]]:
            if knowledge is None:
                raise ToolError("knowledge", "knowledge retriever is not configured")
            return [
                c.model_dump(mode="json")
                for c in knowledge.search(p.query, document_types=doc_types, service=p.service, top_k=p.top_k)
            ]

        return handler

    add(
        "retrieve_runbook",
        "Hybrid search over runbooks, service docs and procedures.",
        ro,
        KnowledgeInput,
        _knowledge(
            [
                "runbook",
                "service_doc",
                "architecture",
                "database_guide",
                "rollback_procedure",
                "deployment_procedure",
                "slo",
            ]
        ),
    )
    add(
        "retrieve_similar_incidents",
        "Hybrid search over previous incident reports.",
        ro,
        KnowledgeInput,
        _knowledge(["incident_report"]),
    )

    # Writes.
    add(
        "scale_deployment",
        "Change the replica count of a deployment.",
        ToolPermission.LOW_RISK_WRITE,
        ScaleInput,
        lambda p: backend.scale(p.service, p.replicas),
    )
    add(
        "restart_deployment",
        "Rolling restart of a deployment.",
        ToolPermission.LOW_RISK_WRITE,
        ServiceInput,
        lambda p: backend.restart(p.service),
    )
    add(
        "rollback_deployment",
        "Roll back the active deployment to the previous revision.",
        ToolPermission.HIGH_RISK_WRITE,
        DeploymentInput,
        lambda p: backend.rollback(p.service, p.deployment_id),
    )
    add(
        "restore_deployment_revision",
        "Re-activate a specific revision (undo of a rollback).",
        ToolPermission.HIGH_RISK_WRITE,
        DeploymentInput,
        lambda p: backend.restore(p.service, p.deployment_id),
    )
    add(
        "apply_config_change",
        "Change a runtime configuration key.",
        ToolPermission.HIGH_RISK_WRITE,
        ConfigInput,
        lambda p: backend.set_config(p.service, p.key, p.value),
    )
    add(
        "execute_database_statement",
        "Run an additive, online DDL statement (CREATE INDEX CONCURRENTLY only).",
        ToolPermission.HIGH_RISK_WRITE,
        SqlInput,
        lambda p: backend.run_sql(p.service, p.sql),
    )

    # Known-destructive capabilities are registered as BLOCKED so that requests for them are
    # recognised, audited and refused, rather than silently unknown.
    def _refuse(p: BlockedInput) -> None:
        raise ToolError("blocked", "blocked tools have no executor")

    for name, description in [
        ("drop_table", "Drop a database table."),
        ("delete_database", "Delete a production database."),
        ("destroy_infrastructure", "Destroy infrastructure resources."),
        ("rotate_core_credentials", "Rotate core platform credentials."),
        ("disable_security_controls", "Disable security controls."),
        ("remove_backups", "Remove backups."),
    ]:
        add(name, description, ToolPermission.BLOCKED, BlockedInput, _refuse)

    return reg


ACTION_TOOL = {
    "scale_deployment": "scale_deployment",
    "restart_deployment": "restart_deployment",
    "rollback_deployment": "rollback_deployment",
    "configuration_change": "apply_config_change",
    "database_remediation": "execute_database_statement",
}
