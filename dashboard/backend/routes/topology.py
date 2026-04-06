from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dashboard.backend.routes.runs import _read_report

router = APIRouter(prefix="/api/runs", tags=["topology"])


def _find_run_dir(run_id: str, run_dirs: list[Path]) -> Path | None:
    for base in run_dirs:
        for run_dir in base.rglob(f"*{run_id}"):
            if run_dir.is_dir() and (run_dir / "summaries" / "summary.json").exists():
                return run_dir
    return None


@router.get("/{run_id}/topology")
def get_topology(run_id: str) -> dict[str, Any]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Read first trace to get blueprint structure
    trace_dir = run_dir / "traces"
    nodes_by_id: dict[str, dict] = {}
    edges: list[dict] = []
    events: list[dict] = []

    for report_path in sorted(trace_dir.glob("*.report.json*"))[:1]:
        report = _read_report(report_path)
        for event in report.get("events", []):
            if event.get("type") == "compile_trace":
                payload = event.get("payload", {})
                # Extract blueprint structure from compile trace if available
                break

        for trace in report.get("traces", []):
            nid = trace.get("node_id", "")
            if nid not in nodes_by_id:
                nodes_by_id[nid] = {
                    "id": nid,
                    "role": trace.get("role", nid),
                    "kind": "agent",
                    "status": trace.get("status", "pending"),
                    "confidence": trace.get("confidence"),
                    "cost": trace.get("cost", {}),
                }
            else:
                nodes_by_id[nid]["status"] = trace.get("status", nodes_by_id[nid]["status"])

        # Build edges from node execution order
        trace_ids = [t["node_id"] for t in report.get("traces", []) if t.get("status") != "skipped"]
        for i in range(len(trace_ids) - 1):
            edges.append({
                "id": f"{trace_ids[i]}_to_{trace_ids[i+1]}",
                "src": trace_ids[i],
                "dst": trace_ids[i+1],
            })
        events = report.get("events", [])

    # Aggregate status across all traces
    for report_path in sorted(trace_dir.glob("*.report.json*")):
        report = _read_report(report_path)
        for trace in report.get("traces", []):
            nid = trace.get("node_id", "")
            if nid in nodes_by_id:
                if trace.get("status") == "failed":
                    nodes_by_id[nid]["status"] = "failed"

    return {
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "subgraphs": [],
        "gates": [],
        "events": events[:50],
    }


@router.get("/{run_id}/tasks")
def get_tasks(run_id: str) -> list[dict[str, Any]]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    task_results_path = run_dir / "eval" / "task_results.json"
    if not task_results_path.exists():
        return []
    return json.loads(task_results_path.read_text(encoding="utf-8"))
