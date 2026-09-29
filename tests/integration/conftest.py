"""Integration fixtures: real PostgreSQL + pgvector (skipped when DATABASE_URL is unreachable)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from veriops.db.session import check_database
from veriops.shared.config import get_settings, reset_settings_cache


@pytest.fixture(scope="session")
def database() -> str:
    reset_settings_cache()
    url = get_settings().database_url
    if not check_database(url):
        pytest.skip(f"PostgreSQL not reachable at {url}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return url


@pytest.fixture(scope="session")
def client(database: str) -> Iterator[TestClient]:
    from veriops.api.main import create_app
    from veriops.api.runtime import build_runtime, set_runtime
    from veriops.shared.config import TelemetryBackendKind

    settings = get_settings().model_copy(update={"telemetry_backend": TelemetryBackendKind.INPROCESS})
    runtime = build_runtime(settings)
    set_runtime(runtime)
    with TestClient(create_app()) as test_client:
        yield test_client
    set_runtime(None)


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": "veriops-demo"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def wait_for_status(
    client: TestClient, headers: dict[str, str], incident_id: str, statuses: set[str], timeout: float = 30
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/incidents/{incident_id}", headers=headers).json()
        if data["status"] in statuses:
            return data
        time.sleep(0.2)
    raise AssertionError(f"incident {incident_id} did not reach {statuses}")
