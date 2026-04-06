from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dashboard.backend.routes.runs import _read_report
from dashboard.backend.routes.topology import _find_run_dir

router = APIRouter(prefix="/api/runs", tags=["logs"])


@router.get("/{run_id}/logs")
def get_logs(run_id: str) -> list[dict[str, Any]]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    trace_dir = run_dir / "traces"
    all_events: list[dict[str, Any]] = []

    for report_path in sorted(trace_dir.glob("*.report.json*")):
        task_id = report_path.stem.replace(".report", "")
        report = _read_report(report_path)
        for event in report.get("events", []):
            event_copy = dict(event)
            event_copy["task_id"] = task_id
            all_events.append(event_copy)

    return all_events
