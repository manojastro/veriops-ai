"""Shared fixtures.

Unit tests run fully in-process (no network, no database). Integration tests need PostgreSQL with
pgvector at ``DATABASE_URL`` and are skipped when it is unreachable.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("LLM_PROVIDER", "simulated")
os.environ.setdefault("TELEMETRY_BACKEND", "inprocess")
os.environ.setdefault("EXECUTION_MODE", "inprocess")

from veriops.bootstrap import Platform, alert_from_activation, build_platform
from veriops.domain.models import Alert
from veriops.graph.runner import InvestigationRunner
from veriops.shared.config import Settings, TelemetryBackendKind
from veriops.simulator.world import SimulatorWorld


@pytest.fixture
def settings() -> Settings:
    return Settings(telemetry_backend=TelemetryBackendKind.INPROCESS, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def world() -> SimulatorWorld:
    return SimulatorWorld()


@pytest.fixture
def platform(settings: Settings, world: SimulatorWorld) -> Platform:
    p = build_platform(settings, world=world)
    p.ingestor.ingest_directory()
    return p


@pytest.fixture
def flagship_alert(world: SimulatorWorld) -> Alert:
    return alert_from_activation(world.activate("checkout_db_regression"))


@pytest.fixture
def runner(platform: Platform) -> InvestigationRunner:
    return InvestigationRunner(platform.graph_deps())
