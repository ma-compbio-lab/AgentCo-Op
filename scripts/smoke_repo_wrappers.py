"""Dry-run smoke tests for both repo wrappers.

Produces the exact `docker run` command that would launch each adapter,
plus validates adapter.py parses a well-formed request.json. Does not
require the Docker daemon.

Usage:
    python scripts/smoke_repo_wrappers.py
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agentcoop.backends.repo_sandbox import build_run_command

WRAPPERS = REPO / "agentcoop" / "wrappers"


def smoke(name: str, request: dict) -> dict:
    manifest_path = WRAPPERS / name / "manifest.yaml"
    adapter_path = WRAPPERS / name / "adapter.py"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    requirements = manifest.get("resources", {}) or {}
    security = manifest.get("security", {}) or {}
    commit = manifest.get("source", {}).get("commit", "HEAD")

    cmd = build_run_command(
        manifest={
            "image": f"agentcoop/{name}",
            "commit_sha": commit,
            "digest": manifest.get("digest", "sha256:placeholder"),
            "network": security.get("network", "none"),
            "cpus": requirements.get("cpus", 4),
            "memory_gb": requirements.get("memory_gb", 16),
            "pids_limit": requirements.get("pids_limit", 512),
            "non_root": security.get("non_root", True),
            "secrets": security.get("secrets", []),
        },
        inputs_dir="./inputs",
        outputs_dir="./outputs",
        request_path="/inputs/request.json",
        result_path="/outputs/result.json",
    )

    with tempfile.TemporaryDirectory() as td:
        req_path = Path(td) / "request.json"
        out_path = Path(td) / "result.json"
        req_path.write_text(json.dumps(request), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(adapter_path), "--input", str(req_path), "--output", str(out_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        adapter_result = json.loads(out_path.read_text()) if out_path.exists() else {}
    return {
        "wrapper": name,
        "manifest_ok": True,
        "commit": commit,
        "dry_run_cmd": shlex.join(cmd),
        "adapter_returncode": proc.returncode,
        "adapter_result_keys": sorted(adapter_result.keys()),
        "adapter_ok": adapter_result.get("ok", False),
    }


def main() -> int:
    cases = [
        ("biodiscovery", {"command": "run_closed_loop_design", "params": {"dataset": "IFNG", "num_genes": 3}}),
        ("spatialagent", {"command": "answer_spatial_task", "params": {"dataset_dir": "/inputs", "question": "markers?", "output_dir": "/outputs"}}),
    ]
    results = [smoke(name, req) for name, req in cases]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
