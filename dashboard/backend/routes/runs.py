from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _scan_runs(base_dirs: list[Path]) -> list[dict[str, Any]]:
    runs = []
    for base in base_dirs:
        if not base.exists():
            continue
        for summary_path in sorted(base.rglob("summaries/summary.json"), reverse=True):
            run_dir = summary_path.parents[1]
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata_path = run_dir / "summaries" / "run_metadata.json"
            metadata = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            runs.append({
                "run_id": run_dir.name,
                "run_path": str(run_dir),
                "name": summary.get("benchmark", run_dir.parent.name),
                "timestamp": metadata.get("start_time", run_dir.name[:15]),
                "benchmark": summary.get("benchmark", "unknown"),
                "solve_rate": summary.get("solve_rate", summary.get("pass_at_1", summary.get("accuracy"))),
                "cost": summary.get("average_usd", 0) * summary.get("task_count", 0),
                "task_count": summary.get("task_count", 0),
                "status": "completed",
            })
    return runs


def _read_report(path: Path) -> dict[str, Any]:
    if str(path).endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


_run_dirs: list[Path] = []


def set_run_dirs(dirs: list[Path]) -> None:
    global _run_dirs
    _run_dirs = dirs


@router.get("")
def list_runs() -> list[dict[str, Any]]:
    return _scan_runs(_run_dirs)


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    for base in _run_dirs:
        for run_dir in base.rglob(f"*{run_id}"):
            if run_dir.is_dir() and (run_dir / "summaries" / "summary.json").exists():
                summary = json.loads((run_dir / "summaries" / "summary.json").read_text(encoding="utf-8"))
                metadata = {}
                metadata_path = run_dir / "summaries" / "run_metadata.json"
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                task_results_path = run_dir / "eval" / "task_results.json"
                task_count = 0
                if task_results_path.exists():
                    task_count = len(json.loads(task_results_path.read_text(encoding="utf-8")))
                return {"summary": summary, "metadata": metadata, "task_results_count": task_count, "run_path": str(run_dir)}
    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
