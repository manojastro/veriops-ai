"""Stateful model of the simulated production estate.

The world holds, per service: replicas, deployment history, configuration, the active fault (if
any) and a piecewise history of metric "segments". Metrics are *derived* from that state by
``compute_metrics`` so that remediation actions have physically plausible effects:

* rolling back the deployment that introduced a fault resolves it,
* scaling out relieves CPU saturation but *increases* database pressure for DB-bound faults,
* a restart temporarily mitigates leaks,
* reverting a bad configuration value resolves a configuration regression.

The sandbox runs actions against a deep copy of a service's state and never mutates production.
"""

from __future__ import annotations

import copy
import hashlib
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from veriops.domain.enums import HypothesisCategory as Fault
from veriops.domain.telemetry import ConfigChange
from veriops.shared.clock import utcnow
from veriops.simulator.catalog import (
    DB_MAX_CONNECTIONS,
    POOL_MAX_CONNECTIONS,
    SERVICES,
    ServiceProfile,
)
from veriops.simulator.scenarios import BASE_DEPLOYMENTS, DeploymentSpec, ScenarioSpec, get_scenario

METRIC_KEYS = ("p95_latency_ms", "error_rate_pct", "cpu_pct", "memory_pct", "db_pool_utilization_pct", "rps")
DB_PRESSURE_FAULTS = {Fault.DB_QUERY_REGRESSION, Fault.CONNECTION_LEAK, Fault.CONFIG_REGRESSION}
LEAK_FAULTS = {Fault.MEMORY_LEAK, Fault.CONNECTION_LEAK}
DESTRUCTIVE_SQL = ("drop ", "truncate ", "delete ", "alter table", "grant ", "revoke ")
OTHER_DB_CLIENTS = 24  # connections held by jobs/other services on each database


class SimulatorActionError(Exception):
    """An action could not be applied (e.g. rolling back a non-active revision)."""


@dataclass
class DeploymentState:
    spec: DeploymentSpec
    deployed_at: datetime
    status: str  # active | superseded | rolled_back


@dataclass
class ActiveFault:
    spec: ScenarioSpec
    started_at: datetime
    resolved_at: datetime | None = None
    mitigated_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.resolved_at is None


@dataclass
class ServiceState:
    profile: ServiceProfile
    environment: str
    replicas: int
    deployments: list[DeploymentState]
    config: dict[str, str]
    config_history: list[ConfigChange]
    fault: ActiveFault | None = None
    segments: list[tuple[datetime, dict[str, float]]] = field(default_factory=list)
    pod_restarts: int = 0
    scenario_id: str | None = None
    suppress_next_action_effect: bool = False

    @property
    def active_deployment(self) -> DeploymentState | None:
        active = [d for d in self.deployments if d.status == "active"]
        return active[-1] if active else None


def _baseline_config(service: str) -> dict[str, str]:
    base = {"db.pool.max_size": str(POOL_MAX_CONNECTIONS), "http.timeout_ms": "5000", "log.level": "info"}
    if service == "inventory-service":
        base["cache.ttl_seconds"] = "300"
    if service == "payment-service":
        base["payment.gateway.provider"] = "primary"
    return base


def _new_service_state(profile: ServiceProfile, now: datetime, environment: str = "production") -> ServiceState:
    deployments = [
        DeploymentState(spec=d, deployed_at=now - timedelta(minutes=d.minutes_ago), status="superseded")
        for d in BASE_DEPLOYMENTS[profile.name]
    ]
    deployments[-1].status = "active"
    state = ServiceState(
        profile=profile,
        environment=environment,
        replicas=profile.replicas,
        deployments=deployments,
        config=_baseline_config(profile.name),
        config_history=[],
    )
    state.segments.append((now - timedelta(hours=24), dict(profile.healthy)))
    return state


def compute_metrics(state: ServiceState, now: datetime | None = None) -> dict[str, float]:
    """Derive current metrics from service state."""
    profile = state.profile
    healthy = profile.healthy
    ratio = state.replicas / profile.replicas
    fault = state.fault
    metrics = dict(healthy)
    metrics["cpu_pct"] = healthy["cpu_pct"] / ratio

    if fault is not None and fault.active:
        kind = fault.spec.fault
        symptoms = fault.spec.symptoms
        if kind in LEAK_FAULTS and fault.mitigated_at is not None:
            metrics = dict(healthy)  # restart bought time; the leak is still there
            metrics["memory_pct"] = healthy["memory_pct"] + (4 if kind == Fault.MEMORY_LEAK else 0)
        elif kind == Fault.CPU_SATURATION:
            metrics = dict(symptoms)
            cpu = symptoms["cpu_pct"] / ratio
            metrics["cpu_pct"] = cpu
            if cpu <= 70:
                metrics["p95_latency_ms"] = healthy["p95_latency_ms"] * 1.15
                metrics["error_rate_pct"] = healthy["error_rate_pct"] * 1.5
            else:
                pressure = (cpu - 70) / (symptoms["cpu_pct"] - 70) if symptoms["cpu_pct"] > 70 else 1
                metrics["p95_latency_ms"] = healthy["p95_latency_ms"] + pressure * (
                    symptoms["p95_latency_ms"] - healthy["p95_latency_ms"]
                )
                metrics["error_rate_pct"] = healthy["error_rate_pct"] + pressure * (
                    symptoms["error_rate_pct"] - healthy["error_rate_pct"]
                )
        else:
            metrics = dict(symptoms)
            if kind in DB_PRESSURE_FAULTS and ratio > 1:
                # More replicas -> more pooled connections competing for the same slow database.
                extra = ratio - 1
                metrics["p95_latency_ms"] = symptoms["p95_latency_ms"] * (1 + 0.35 * extra)
                metrics["error_rate_pct"] = min(60.0, symptoms["error_rate_pct"] * (1 + 0.6 * extra))
                metrics["cpu_pct"] = symptoms["cpu_pct"] / math.sqrt(ratio)
            elif ratio != 1:
                metrics["cpu_pct"] = symptoms["cpu_pct"] / ratio
            if kind == Fault.DEPENDENCY_OUTAGE and state.config.get("payment.gateway.provider") == "secondary":
                metrics = dict(healthy)
                metrics["p95_latency_ms"] = healthy["p95_latency_ms"] * 1.2

    return {k: round(float(metrics[k]), 2) for k in METRIC_KEYS}


def _noise(*parts: object) -> float:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return (digest[0] / 255.0) - 0.5  # [-0.5, 0.5]


class SimulatorWorld:
    """Thread-safe container for all simulated services."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.services: dict[str, ServiceState] = {}
        self.reset()

    # --- lifecycle ---------------------------------------------------------------------------

    def reset(self, service: str | None = None) -> None:
        with self._lock:
            now = utcnow()
            targets = [service] if service else list(SERVICES)
            for name in targets:
                self.services[name] = _new_service_state(SERVICES[name], now)

    def activate(self, scenario_id: str) -> dict[str, object]:
        """Inject a scenario's fault into its service. Returns the resulting alert payload."""
        spec = get_scenario(scenario_id)
        with self._lock:
            now = utcnow()
            state = _new_service_state(SERVICES[spec.service], now, environment=spec.environment)
            state.scenario_id = spec.id
            if spec.bad_deployment is not None:
                for d in state.deployments:
                    if d.status == "active":
                        d.status = "superseded"
                state.deployments.append(
                    DeploymentState(
                        spec=spec.bad_deployment,
                        deployed_at=now - timedelta(minutes=spec.bad_deployment.minutes_ago),
                        status="active",
                    )
                )
            if spec.config_change is not None:
                cc = spec.config_change
                state.config[cc.key] = cc.new_value
                state.config_history.append(
                    ConfigChange(
                        key=cc.key,
                        old_value=cc.old_value,
                        new_value=cc.new_value,
                        changed_at=now - timedelta(minutes=cc.minutes_ago),
                        changed_by=cc.changed_by,
                    )
                )
            state.pod_restarts = spec.pod_restarts
            started = now - timedelta(minutes=spec.onset_minutes_ago)
            state.fault = ActiveFault(spec=spec, started_at=started)
            state.segments.append((started, compute_metrics(state, now)))
            self.services[spec.service] = state
            metrics = compute_metrics(state, now)
            return {
                "scenario": spec.id,
                "service": spec.service,
                "environment": spec.environment,
                "severity": spec.severity,
                "title": spec.title,
                "metrics": metrics,
                "fired_at": now.isoformat(),
            }

    def state(self, service: str) -> ServiceState:
        with self._lock:
            if service not in self.services:
                raise KeyError(f"unknown service {service!r}")
            return self.services[service]

    def current_metrics(self, service: str) -> dict[str, float]:
        return compute_metrics(self.state(service))

    def series(self, service: str, minutes: int = 60) -> list[tuple[datetime, dict[str, float]]]:
        state = self.state(service)
        now = utcnow().replace(second=0, microsecond=0)
        points: list[tuple[datetime, dict[str, float]]] = []
        segments = sorted(state.segments, key=lambda s: s[0])
        leak = state.fault is not None and state.fault.spec.fault == Fault.MEMORY_LEAK
        for i in range(minutes, -1, -1):
            ts = now - timedelta(minutes=i)
            idx = max((j for j, (start, _) in enumerate(segments) if start <= ts), default=0)
            start, values = segments[idx]
            prev_values = segments[idx - 1][1] if idx > 0 else values
            ramp_minutes = 3.0
            if leak and state.fault is not None and start == state.fault.started_at:
                ramp_minutes = max(3.0, state.fault.spec.onset_minutes_ago)
            frac = min(1.0, max(0.0, (ts - start).total_seconds() / 60 / ramp_minutes))
            point = {}
            for key in METRIC_KEYS:
                value = prev_values[key] + (values[key] - prev_values[key]) * frac
                jitter = 0.0 if key == "db_pool_utilization_pct" and value >= 99 else 0.06
                point[key] = round(max(0.0, value * (1 + jitter * _noise(service, key, ts.isoformat()))), 2)
            points.append((ts, point))
        return points

    def _record(self, state: ServiceState) -> None:
        state.segments.append((utcnow(), compute_metrics(state)))

    # --- actions (production) -------------------------------------------------------------------

    def scale(self, service: str, replicas: int) -> dict[str, object]:
        with self._lock:
            state = self.state(service)
            return self._scale(state, replicas, record=True)

    def restart(self, service: str) -> dict[str, object]:
        with self._lock:
            return self._restart(self.state(service), record=True)

    def rollback(self, service: str, deployment_id: str) -> dict[str, object]:
        with self._lock:
            return self._rollback(self.state(service), deployment_id, record=True)

    def restore_deployment(self, service: str, deployment_id: str) -> dict[str, object]:
        """Re-activate a previously rolled-back revision (used to undo a failed remediation)."""
        with self._lock:
            state = self.state(service)
            target = next((d for d in state.deployments if d.spec.id == deployment_id), None)
            if target is None:
                raise SimulatorActionError(f"deployment {deployment_id} not found")
            for d in state.deployments:
                if d.status == "active":
                    d.status = "superseded"
            target.status = "active"
            fault = state.fault
            if fault and fault.spec.bad_deployment and fault.spec.bad_deployment.id == deployment_id:
                fault.resolved_at = None
            self._record(state)
            return {"service": service, "active_deployment": deployment_id}

    def set_config(self, service: str, key: str, value: str, actor: str = "veriops") -> dict[str, object]:
        with self._lock:
            return self._set_config(self.state(service), key, value, actor, record=True)

    def run_sql(self, service: str, sql: str) -> dict[str, object]:
        with self._lock:
            return self._run_sql(self.state(service), sql, record=True)

    def inject_verification_failure(self, service: str) -> None:
        """Chaos knob: the next production action has no effect (sandbox is unaffected)."""
        with self._lock:
            self.state(service).suppress_next_action_effect = True

    # --- action implementations (shared by production and sandbox) ----------------------------

    def _consume_suppression(self, state: ServiceState, record: bool) -> bool:
        if record and state.suppress_next_action_effect:
            state.suppress_next_action_effect = False
            self._record(state)
            return True
        return False

    def _scale(self, state: ServiceState, replicas: int, record: bool) -> dict[str, object]:
        if replicas < 1 or replicas > state.profile.hpa_max * 2:
            raise SimulatorActionError(f"replicas must be within 1..{state.profile.hpa_max * 2}")
        previous = state.replicas
        state.replicas = replicas
        if record:
            self._record(state)
        return {"service": state.profile.name, "previous_replicas": previous, "replicas": replicas}

    def _restart(self, state: ServiceState, record: bool) -> dict[str, object]:
        if not self._consume_suppression(state, record):
            fault = state.fault
            if fault and fault.active and fault.spec.fault in LEAK_FAULTS:
                fault.mitigated_at = utcnow()
            state.pod_restarts = 0
            if record:
                self._record(state)
        return {"service": state.profile.name, "restarted_pods": state.replicas}

    def _rollback(self, state: ServiceState, deployment_id: str, record: bool) -> dict[str, object]:
        active = state.active_deployment
        if active is None or active.spec.id != deployment_id:
            raise SimulatorActionError(
                f"deployment #{deployment_id} is not the active revision of {state.profile.name}"
            )
        index = state.deployments.index(active)
        if index == 0:
            raise SimulatorActionError("no previous revision to roll back to")
        previous = state.deployments[index - 1]
        active.status = "rolled_back"
        previous.status = "active"
        if not self._consume_suppression(state, record):
            fault = state.fault
            if fault and fault.active and fault.spec.bad_deployment and fault.spec.bad_deployment.id == deployment_id:
                fault.resolved_at = utcnow()
            if record:
                self._record(state)
        return {"service": state.profile.name, "rolled_back": deployment_id, "active_deployment": previous.spec.id}

    def _set_config(self, state: ServiceState, key: str, value: str, actor: str, record: bool) -> dict[str, object]:
        old = state.config.get(key, "")
        state.config[key] = value
        state.config_history.append(
            ConfigChange(key=key, old_value=old, new_value=value, changed_at=utcnow(), changed_by=actor)
        )
        if not self._consume_suppression(state, record):
            fault = state.fault
            if fault and fault.active and fault.spec.fix_config == (key, value):
                fault.resolved_at = utcnow()
            if record:
                self._record(state)
        return {"service": state.profile.name, "key": key, "old_value": old, "new_value": value}

    def _run_sql(self, state: ServiceState, sql: str, record: bool) -> dict[str, object]:
        lowered = f" {sql.lower()} "
        if any(token in lowered for token in DESTRUCTIVE_SQL):
            raise SimulatorActionError("destructive SQL refused by the database guard")
        applied = False
        if not self._consume_suppression(state, record):
            fault = state.fault
            if (
                fault
                and fault.active
                and fault.spec.fix_sql_contains
                and "create index" in lowered
                and fault.spec.fix_sql_contains in lowered
            ):
                fault.resolved_at = utcnow()
                applied = True
            if record:
                self._record(state)
        return {"service": state.profile.name, "statement": sql, "resolved_fault": applied}

    # --- sandbox --------------------------------------------------------------------------------

    def sandbox(
        self, service: str, action: dict[str, object]
    ) -> tuple[dict[str, float], dict[str, float], ServiceState]:
        """Apply ``action`` to a copy of the service and return (before, after, sandbox_state)."""
        with self._lock:
            original = self.state(service)
            before = compute_metrics(original)
            clone = copy.deepcopy(original)
        clone.suppress_next_action_effect = False
        kind = str(action.get("action_type"))
        if kind == "scale_deployment":
            self._scale(clone, int(action["replicas"]), record=False)  # type: ignore[call-overload]
        elif kind == "restart_deployment":
            self._restart(clone, record=False)
        elif kind == "rollback_deployment":
            self._rollback(clone, str(action["deployment_id"]), record=False)
        elif kind == "configuration_change":
            self._set_config(clone, str(action["config_key"]), str(action["config_value"]), "sandbox", record=False)
        elif kind == "database_remediation":
            self._run_sql(clone, str(action["sql"]), record=False)
        else:
            raise SimulatorActionError(f"action {kind!r} cannot be simulated")
        return before, compute_metrics(clone), clone

    # --- derived views --------------------------------------------------------------------------

    @staticmethod
    def pool_limits(state: ServiceState, replicas: int | None = None) -> tuple[float, int]:
        """(per-pod pool size, total pool max) for the service at ``replicas`` (default: current)."""
        per_pod = POOL_MAX_CONNECTIONS / state.profile.replicas
        return per_pod, round(per_pod * (replicas if replicas is not None else state.replicas))

    def db_connections(self, state: ServiceState) -> tuple[int, int]:
        metrics = compute_metrics(state)
        _, pool_max = self.pool_limits(state)
        active = round(metrics["db_pool_utilization_pct"] / 100 * pool_max) + OTHER_DB_CLIENTS
        if state.fault and state.fault.active and state.fault.spec.fault in DB_PRESSURE_FAULTS:
            # The slow database keeps every pooled connection busy.
            active = pool_max + OTHER_DB_CLIENTS
        return active, DB_MAX_CONNECTIONS


WORLD = SimulatorWorld()
