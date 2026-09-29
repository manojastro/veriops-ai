"""Enumerations shared across the platform."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ROLLED_BACK = "ROLLED_BACK"
    ESCALATED = "ESCALATED"
    REMEDIATION_REJECTED = "REMEDIATION_REJECTED"
    FAILED = "FAILED"


class EvidenceKind(StrEnum):
    ALERT = "alert"
    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    KUBERNETES = "kubernetes"
    DATABASE = "database"
    CONNECTION_POOL = "connection_pool"
    SLOW_QUERIES = "slow_queries"
    DEPLOYMENTS = "deployments"
    GIT_COMMIT = "git_commit"
    GIT_DIFF = "git_diff"
    CONFIG_HISTORY = "config_history"
    DEPENDENCIES = "dependencies"
    RUNBOOK = "runbook"
    SIMILAR_INCIDENT = "similar_incident"
    VALIDATION = "validation"


class HypothesisCategory(StrEnum):
    CAPACITY = "capacity"
    DB_QUERY_REGRESSION = "db_query_regression"
    CONNECTION_LEAK = "connection_leak"
    MEMORY_LEAK = "memory_leak"
    CPU_SATURATION = "cpu_saturation"
    DEPENDENCY_OUTAGE = "dependency_outage"
    BAD_DEPLOYMENT = "bad_deployment"
    CONFIG_REGRESSION = "config_regression"
    UNKNOWN = "unknown"


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class ActionType(StrEnum):
    SCALE_DEPLOYMENT = "scale_deployment"
    RESTART_DEPLOYMENT = "restart_deployment"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    DATABASE_REMEDIATION = "database_remediation"
    CONFIGURATION_CHANGE = "configuration_change"
    ESCALATE = "escalate"
    # Destructive action classes. They exist so that a proposal of them can be *recognised and
    # blocked*; no executor exists for any of them.
    DROP_TABLE = "drop_table"
    DELETE_DATABASE = "delete_database"
    DESTROY_INFRASTRUCTURE = "destroy_infrastructure"
    ROTATE_CORE_CREDENTIALS = "rotate_core_credentials"
    DISABLE_SECURITY_CONTROLS = "disable_security_controls"
    REMOVE_BACKUPS = "remove_backups"


class ValidationStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PolicyDecision(StrEnum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUIRE_APPROVAL_WITH_CHECKS = "REQUIRE_APPROVAL_WITH_CHECKS"
    BLOCKED = "BLOCKED"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MORE_INVESTIGATION = "MORE_INVESTIGATION"
    TIMED_OUT = "TIMED_OUT"


class ToolPermission(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK_WRITE = "HIGH_RISK_WRITE"
    BLOCKED = "BLOCKED"


class Role(StrEnum):
    VIEWER = "viewer"
    ENGINEER = "engineer"
    APPROVER = "approver"
    ADMINISTRATOR = "administrator"


class ActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    HUMAN = "human"
    TOOL = "tool"
