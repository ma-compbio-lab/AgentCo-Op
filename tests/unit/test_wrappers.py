from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BIO = REPO / "agentcoop" / "wrappers" / "biodiscovery"
SPATIAL = REPO / "agentcoop" / "wrappers" / "spatialagent"


def test_manifests_parse() -> None:
    for p in (BIO / "manifest.yaml", SPATIAL / "manifest.yaml"):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["backend_type"] == "sandbox_repo"
        assert data["security"]["non_root"] is True
        assert data["source"]["commit"]


def test_biodiscovery_adapter(tmp_path: Path) -> None:
    req = tmp_path / "request.json"
    out = tmp_path / "result.json"
    req.write_text(
        json.dumps(
            {"command": "run_closed_loop_design", "params": {"dataset": "IFNG", "num_genes": 3}}
        )
    )
    subprocess.run(
        [sys.executable, str(BIO / "adapter.py"), "--input", str(req), "--output", str(out)],
        check=True,
    )
    result = json.loads(out.read_text())
    assert result["ok"] is True
    assert len(result["ranked_genes"]) == 3


def test_spatialagent_adapter(tmp_path: Path) -> None:
    req = tmp_path / "request.json"
    out = tmp_path / "result.json"
    req.write_text(
        json.dumps(
            {
                "command": "answer_spatial_task",
                "params": {"dataset_dir": "/tmp/x", "question": "markers?", "output_dir": "/tmp/o"},
            }
        )
    )
    subprocess.run(
        [sys.executable, str(SPATIAL / "adapter.py"), "--input", str(req), "--output", str(out)],
        check=True,
    )
    result = json.loads(out.read_text())
    assert result["ok"] is True
    assert "spatially_variable" in result["gene_sets"]


def test_biodiscovery_rejects_unknown_command(tmp_path: Path) -> None:
    req = tmp_path / "request.json"
    out = tmp_path / "result.json"
    req.write_text(json.dumps({"command": "nope"}))
    proc = subprocess.run(
        [sys.executable, str(BIO / "adapter.py"), "--input", str(req), "--output", str(out)],
        capture_output=True,
    )
    result = json.loads(out.read_text())
    assert result["ok"] is False and proc.returncode == 1
