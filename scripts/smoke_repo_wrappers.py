"""Dry-run smoke tests for case-study wrappers.

Generates the exact `docker run` command that would launch each adapter
and verifies the adapter parses a request.json. No Docker daemon needed.

Usage:
    python scripts/smoke_repo_wrappers.py
    python scripts/smoke_repo_wrappers.py --wrapper geneagent
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agentcoop.backends.repo_sandbox import build_run_command  # noqa: E402

WRAPPERS = REPO / "agentcoop" / "wrappers"


# Default request.json templates per wrapper — deliberately minimal so the
# adapter stub exercises its required-field checks.
TEMPLATES = {
    "geneagent": {
        "command": "analyze_gene_set",
        "params": {
            "gene_symbols": ["STAT1", "IRF1", "JAK2"],
            "context": "Human airway smooth muscle cells (dexamethasone vs control).",
            "organism": "Homo sapiens",
        },
    },
    "gears": {
        "command": "predict_perturbation",
        "params": {
            "dataset": "norman",
            "perturbation": "FOSB+CEBPB",
            "split": "test",
            "seed": 1,
        },
    },
    "scgpt": {
        "command": "predict_perturbation",
        "params": {"dataset": "norman", "perturbation": "KLF1", "split": "test"},
    },
    "scfoundation": {
        "command": "predict_perturbation",
        "params": {"dataset": "norman", "perturbation": "KLF1", "split": "test"},
    },
    "geneformer": {
        "command": "embed_cells",
        "params": {"dataset": "norman", "output": "embeddings.npy"},
    },
}


def smoke(name: str) -> dict:
    wrapper_dir = WRAPPERS / name
    manifest_path = wrapper_dir / "manifest.yaml"
    adapter_path = wrapper_dir / "adapter.py"
    if not manifest_path.exists() or not adapter_path.exists():
        return {"wrapper": name, "status": "missing", "manifest": str(manifest_path)}

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    resources = manifest.get("resources", {}) or {}
    security = manifest.get("security", {}) or {}
    commit = manifest.get("source", {}).get("commit", "HEAD")

    cmd = build_run_command(
        manifest={
            "image": f"agentcoop/{name}",
            "commit_sha": commit,
            "digest": manifest.get("digest", "sha256:placeholder"),
            "network": security.get("network", "none"),
            "cpus": resources.get("cpus", 4),
            "memory_gb": resources.get("memory_gb", 16),
            "pids_limit": resources.get("pids_limit", 512),
            "non_root": security.get("non_root", True),
            "secrets": security.get("secrets", []),
        },
        inputs_dir="./inputs",
        outputs_dir="./outputs",
        request_path="/inputs/request.json",
        result_path="/outputs/result.json",
    )

    request = TEMPLATES.get(name, {"command": "unknown", "params": {}})

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
        "status": "ok" if proc.returncode == 0 else "error",
        "manifest_ok": True,
        "commit": commit,
        "dry_run_cmd": shlex.join(cmd),
        "adapter_returncode": proc.returncode,
        "adapter_result_keys": sorted(adapter_result.keys()),
        "adapter_ok": adapter_result.get("ok", False),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--wrapper",
        action="append",
        help="Run one or more specific wrappers (default: all that exist)",
    )
    args = parser.parse_args()

    if args.wrapper:
        targets = args.wrapper
    else:
        targets = sorted(p.name for p in WRAPPERS.iterdir() if p.is_dir() and not p.name.startswith("_"))

    results = [smoke(name) for name in targets]
    print(json.dumps(results, indent=2))
    return 0 if all(r.get("status") != "error" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
