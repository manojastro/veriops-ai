"""Dependency container injected into every graph node."""

from __future__ import annotations

from dataclasses import dataclass, field

from veriops.agents.evidence import EvidenceAgent
from veriops.agents.remediation import RemediationAgent
from veriops.agents.root_cause import RootCauseAgent
from veriops.graph.recorder import IncidentRecorder
from veriops.llm.gateway import LLMGateway
from veriops.policy.engine import PolicyEngine
from veriops.policy.risk import RiskEngine
from veriops.shared.config import Settings
from veriops.simulator.catalog import SERVICES
from veriops.tools.registry import ToolRegistry
from veriops.validators.engine import ValidationEngine


@dataclass
class ServiceInfo:
    name: str
    criticality: str
    team: str
    slo_p95_latency_ms: float
    slo_error_rate_pct: float


def default_service_catalog() -> dict[str, ServiceInfo]:
    return {
        p.name: ServiceInfo(p.name, p.criticality, p.team, p.slo_p95_latency_ms, p.slo_error_rate_pct)
        for p in SERVICES.values()
    }


@dataclass
class GraphDeps:
    settings: Settings
    tools: ToolRegistry
    llm: LLMGateway
    recorder: IncidentRecorder
    validation: ValidationEngine = field(default_factory=ValidationEngine)
    risk: RiskEngine = field(default_factory=RiskEngine)
    policy: PolicyEngine = field(default_factory=PolicyEngine)
    services: dict[str, ServiceInfo] = field(default_factory=default_service_catalog)

    def __post_init__(self) -> None:
        self.evidence_agent = EvidenceAgent(self.tools)
        self.root_cause_agent = RootCauseAgent(self.llm)
        self.remediation_agent = RemediationAgent(self.llm)
