"""Evaluation routes. Runs execute in the background against isolated in-process simulators."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update

from veriops.api.deps import require, runtime_dep
from veriops.api.runtime import Runtime
from veriops.api.schemas import EvaluationRequest
from veriops.db.models import EvaluationRunRow
from veriops.db.session import session_scope
from veriops.evaluation.harness import load_dataset, run_evaluation
from veriops.security.auth import Principal
from veriops.shared.clock import utcnow
from veriops.shared.ids import new_id

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def _run(run_id: str, rt: Runtime, scenario_ids: list[str] | None) -> None:
    cases = load_dataset()
    if scenario_ids:
        cases = [c for c in cases if c.incident_id in scenario_ids or c.scenario in scenario_ids]
    try:
        result = run_evaluation(cases, rt.settings)
        values = {
            "status": "COMPLETED",
            "metrics": {**result["metrics"], "gates": result["gates"], "thresholds": result["thresholds"]},
            "results": result["results"],
            "passed_thresholds": result["passed"],
        }
    except Exception as exc:
        values = {"status": "FAILED", "metrics": {"error": str(exc)}}
    with session_scope() as s:
        s.execute(update(EvaluationRunRow).where(EvaluationRunRow.id == run_id).values(**values, finished_at=utcnow()))


@router.post("/run", status_code=202, summary="Run the evaluation suite")
def run(
    body: EvaluationRequest, rt: Runtime = Depends(runtime_dep), _: Principal = Depends(require("evaluations:run"))
) -> dict[str, Any]:
    run_id = new_id("eval")
    cases = load_dataset()
    count = len(
        [
            c
            for c in cases
            if not body.scenario_ids or c.incident_id in body.scenario_ids or c.scenario in body.scenario_ids
        ]
    )
    with session_scope() as s:
        s.add(
            EvaluationRunRow(id=run_id, status="RUNNING", provider=rt.settings.llm_provider.value, scenario_count=count)
        )
    threading.Thread(target=_run, args=(run_id, rt, body.scenario_ids), daemon=True).start()
    return {"id": run_id, "status": "RUNNING", "scenario_count": count}


def _row(r: EvaluationRunRow, results: bool) -> dict[str, Any]:
    data = {
        "id": r.id,
        "status": r.status,
        "provider": r.provider,
        "scenario_count": r.scenario_count,
        "metrics": r.metrics,
        "passed_thresholds": r.passed_thresholds,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }
    if results:
        data["results"] = r.results
    return data


@router.get("", summary="List evaluation runs")
def list_runs(_: Principal = Depends(require("evaluations:read"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(EvaluationRunRow).order_by(EvaluationRunRow.started_at.desc()).limit(20)).all()
        return [_row(r, False) for r in rows]


@router.get("/dataset", summary="Evaluation dataset")
def dataset(_: Principal = Depends(require("evaluations:read"))) -> list[dict[str, Any]]:
    return [c.model_dump() for c in load_dataset()]


@router.get("/{run_id}", summary="Evaluation run with per-case results")
def get_run(run_id: str, _: Principal = Depends(require("evaluations:read"))) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(EvaluationRunRow, run_id)
        if row is None:
            raise HTTPException(404, "evaluation run not found")
        return _row(row, True)
