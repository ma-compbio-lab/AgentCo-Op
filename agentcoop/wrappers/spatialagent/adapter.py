"""Adapter for SpatialAgent.

Framework-only stub. See `agentcoop/wrappers/biodiscovery/adapter.py` for
the shared I/O contract.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


REQUIRED_COMMANDS = {"answer_spatial_task"}


def _read_request(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def run(request: dict) -> dict:
    command = request.get("command")
    params = request.get("params", {}) or {}
    if command not in REQUIRED_COMMANDS:
        return {
            "ok": False,
            "errors": [f"unsupported command '{command}'; expected one of {sorted(REQUIRED_COMMANDS)}"],
            "answer": "",
            "gene_sets": {},
            "artifacts": [],
            "logs_summary": "",
        }
    dataset = params.get("dataset_dir", "unknown")
    question = params.get("question", "")
    return {
        "ok": True,
        "answer": f"stub answer for {question[:80]}",
        "gene_sets": {"spatially_variable": ["GENE_A", "GENE_B"], "markers": ["GENE_C"]},
        "artifacts": [],
        "metrics": {"dataset_dir": dataset},
        "logs_summary": "framework stub: real SpatialAgent not invoked",
        "errors": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/inputs/request.json")
    parser.add_argument("--output", default="/outputs/result.json")
    args = parser.parse_args(argv)

    start = time.time()
    request = _read_request(Path(args.input))
    try:
        result = run(request)
    except Exception as exc:
        result = {
            "ok": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "answer": "",
            "gene_sets": {},
            "artifacts": [],
            "logs_summary": "",
        }
    result["elapsed_s"] = round(time.time() - start, 4)
    _write_result(Path(args.output), result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
