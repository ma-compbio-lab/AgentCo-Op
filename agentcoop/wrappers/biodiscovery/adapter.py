"""Adapter for BioDiscoveryAgent.

Contract: read `--input /inputs/request.json`, write `--output /outputs/result.json`.
Framework phase: the real repo call is not wired; instead we validate the
request and emit a structured stub so downstream code can be developed and
tested without pulling the upstream repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


REQUIRED_COMMANDS = {"run_closed_loop_design"}


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
            "ranked_genes": [],
            "per_round_metrics": [],
            "artifacts": [],
            "logs_summary": "",
        }

    # Framework-only synthetic result — replace with the real BioDiscoveryAgent
    # call when the repo is pinned and the image built.
    num_genes = int(params.get("num_genes", 128))
    dataset = params.get("dataset", "unknown")
    ranked_genes = [f"GENE_{i}" for i in range(min(num_genes, 5))]
    return {
        "ok": True,
        "ranked_genes": ranked_genes,
        "per_round_metrics": [{"round": 1, "hit_rate": 0.0}],
        "artifacts": [],
        "metrics": {"dataset": dataset},
        "logs_summary": "framework stub: real BioDiscoveryAgent not invoked",
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
    except Exception as exc:  # adapter must never crash the sandbox
        result = {
            "ok": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "ranked_genes": [],
            "per_round_metrics": [],
            "artifacts": [],
            "logs_summary": "",
        }
    result["elapsed_s"] = round(time.time() - start, 4)
    _write_result(Path(args.output), result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
