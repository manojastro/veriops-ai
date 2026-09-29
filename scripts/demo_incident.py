#!/usr/bin/env python3
"""VeriOps flagship demo.

Against the running stack (default):
    python scripts/demo_incident.py                 # creates the incident, pauses for approval in the UI
    python scripts/demo_incident.py --approve       # also approves as 'alice' and waits for recovery

Without any infrastructure (in-process simulator, in-memory everything):
    python scripts/demo_incident.py --offline --approve
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

TERMINAL = {"RESOLVED", "ROLLED_BACK", "ESCALATED", "REMEDIATION_REJECTED", "FAILED"}
COLORS = {"danger": "\033[31m", "success": "\033[32m", "warning": "\033[33m", "info": "\033[0m"}
RESET = "\033[0m"


def show(event: dict[str, Any]) -> None:
    color = COLORS.get(event.get("status", "info"), "")
    ts = str(event.get("ts", ""))[11:19]
    print(f"{ts}  {color}{event['title']}{RESET}")
    if event.get("status") == "danger" and event.get("detail"):
        for part in str(event["detail"]).split("; ")[:6]:
            print(f"          · {part}")


def online(args: argparse.Namespace) -> int:
    client = httpx.Client(base_url=args.api, timeout=30)

    def token(user: str) -> dict[str, str]:
        r = client.post("/api/auth/login", json={"username": user, "password": args.password})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    engineer = token("bob")
    incident = client.post(
        "/api/incidents", headers=engineer, json={"scenario": args.scenario, "auto_investigate": True}
    ).json()
    incident_id = incident["id"]
    print(f"\nIncident {incident_id} created: {incident['title']}\n")

    seq, status = 0, incident["status"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for event in client.get(
            f"/api/incidents/{incident_id}/timeline", params={"after_seq": seq}, headers=engineer
        ).json():
            seq = event["seq"]
            show(event)
        status = client.get(f"/api/incidents/{incident_id}", headers=engineer).json()["status"]
        if status == "AWAITING_APPROVAL" and not args.approve:
            print(f"\nPaused for human approval. Open {args.web}/incidents/{incident_id} and approve as 'alice'.")
            return 0
        if status == "AWAITING_APPROVAL" and args.approve:
            print("\n→ approving as alice (approver)\n")
            client.post(
                f"/api/incidents/{incident_id}/approve",
                headers=token("alice"),
                json={"comment": "Rollback validated in sandbox (demo script)"},
            ).raise_for_status()
            args.approve = False
        if status in TERMINAL:
            break
        time.sleep(0.7)
    for event in client.get(
        f"/api/incidents/{incident_id}/timeline", params={"after_seq": seq}, headers=engineer
    ).json():
        show(event)
    cost = client.get(f"/api/incidents/{incident_id}/cost", headers=engineer).json()
    print(f"\nFinal status: {status} · AI cost {cost['total_cost_usd']:.4f} USD over {cost['calls']} LLM calls")
    print(f"Report: {args.api}/api/incidents/{incident_id}/report.md  ·  UI: {args.web}/incidents/{incident_id}")
    return 0 if status in TERMINAL else 1


def offline(args: argparse.Namespace) -> int:
    from veriops.bootstrap import alert_from_activation, build_platform
    from veriops.domain.enums import ApprovalStatus
    from veriops.domain.models import ApprovalDecision
    from veriops.graph.runner import InvestigationRunner
    from veriops.shared.config import Settings, TelemetryBackendKind
    from veriops.simulator.world import SimulatorWorld

    world = SimulatorWorld()
    platform = build_platform(Settings(telemetry_backend=TelemetryBackendKind.INPROCESS), world=world)
    platform.ingestor.ingest_directory()
    runner = InvestigationRunner(platform.graph_deps())
    incident_id = "INC-DEMO-OFFLINE"
    out = runner.start(incident_id, alert_from_activation(world.activate(args.scenario)))
    printed = 0

    def flush() -> None:
        nonlocal printed
        events = platform.recorder.events[incident_id]  # type: ignore[attr-defined]
        for e in events[printed:]:
            show(e.model_dump(mode="json"))
        printed = len(events)

    flush()
    if out.interrupted:
        if not args.approve:
            print("\nPaused for human approval (run with --approve to continue).")
            return 0
        print("\n→ approving as alice (approver)\n")
        out = runner.resume(
            incident_id,
            ApprovalDecision(status=ApprovalStatus.APPROVED, actor="alice", role="approver", comment="demo"),
        )
        flush()
    print(f"\nFinal status: {out.state['status'].value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="checkout_db_regression")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--web", default="http://localhost:3000")
    parser.add_argument("--password", default="veriops-demo")
    parser.add_argument("--approve", action="store_true", help="approve automatically as 'alice'")
    parser.add_argument("--offline", action="store_true", help="run fully in-process without the stack")
    args = parser.parse_args()
    return offline(args) if args.offline else online(args)


if __name__ == "__main__":
    sys.exit(main())
