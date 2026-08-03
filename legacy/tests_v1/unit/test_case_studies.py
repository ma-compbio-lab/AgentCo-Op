"""End-to-end offline tests for Case Studies 1 & 2.

CS3 is covered by test_aflow_importer.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from agentcoop.benchmarks.common import REPO_ROOT


# ---------------------------------------------------------------------------
# Case Study 1
# ---------------------------------------------------------------------------


def test_case1_end_to_end(tmp_path: Path) -> None:
    from agentcoop.benchmarks.bio import enrich, run_geneagent, select_markers

    # Generate a synthetic DE TSV via the ship-with-repo script so the
    # test doesn't require R or DESeq2.
    de_dir = tmp_path / "de"
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "case1_synthetic_de.py"), str(de_dir)],
        check=True,
    )
    de_path = de_dir / "de_results.tsv"
    assert de_path.exists()

    markers = select_markers(de_path, out_dir=tmp_path / "gene_sets")
    assert markers["n_up"] > 0 and markers["n_down"] > 0

    up_path = tmp_path / "gene_sets" / "up_genes.json"
    enr = enrich(up_path, out_path=tmp_path / "enrichment" / "up.json")
    assert any(r["pvalue"] < 0.5 for r in enr["results"])

    gen = run_geneagent(up_path, context="airway dex vs control", out_path=tmp_path / "geneagent.json")
    assert gen["ok"]
    assert gen["functional_labels"]


def test_case1_config_has_all_knobs() -> None:
    cfg = yaml.safe_load(
        (REPO_ROOT / "configs" / "case_studies" / "case1_airway.yaml").read_text(encoding="utf-8")
    )
    assert cfg["case_study"] == "case1_airway"
    assert "AC-BulkGeneSet" in cfg["variants"]
    assert cfg["gates_file"].endswith("bio_gates.yaml")


# ---------------------------------------------------------------------------
# Case Study 2
# ---------------------------------------------------------------------------


def test_case2_simple_baselines_produce_valid_predictions() -> None:
    from agentcoop.benchmarks.perturb import (
        BASELINES,
        PerturbDataset,
        evaluate_prediction,
        synthetic_dataset,
        validate_prediction,
    )

    ds = synthetic_dataset()
    pert = ds.perturbations[0]
    for name, fn in BASELINES.items():
        pred = fn(ds, pert)
        assert not validate_prediction(pred), (name, pred)
        metrics = evaluate_prediction(pred, ds)
        assert 0.0 <= metrics["gene_universe_coverage"] <= 1.0


def test_case2_ensembles_run() -> None:
    from agentcoop.benchmarks.perturb import (
        BASELINES,
        ensemble_rank_fusion,
        ensemble_validation_winner,
        ensemble_weighted,
        evaluate_prediction,
        synthetic_dataset,
    )

    ds = synthetic_dataset()
    pert = ds.perturbations[0]
    preds = [fn(ds, pert) for fn in BASELINES.values()]
    by_model = {p["model"]: [evaluate_prediction(p, ds)] for p in preds}
    w = ensemble_validation_winner(by_model)
    assert w["winner"] in by_model
    r = ensemble_rank_fusion(preds)
    assert r["top_genes"]
    we = ensemble_weighted(preds)
    assert len(we["predicted_delta"]) == len(ds.genes)


def test_case2_config_has_models_and_gates() -> None:
    cfg = yaml.safe_load(
        (REPO_ROOT / "configs" / "case_studies" / "case2_norman_replogle.yaml").read_text()
    )
    assert cfg["case_study"] == "case2_perturb"
    for model in ("GEARS", "scGPT", "scFoundation"):
        assert model in cfg["models"]
    assert cfg["gates_file"].endswith("perturb_gates.yaml")


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrapper,command,params_extra",
    [
        ("geneagent", "analyze_gene_set", {"gene_symbols": ["STAT1", "IRF1"]}),
        ("gears", "predict_perturbation", {"dataset": "synthetic_norman"}),
        ("scgpt", "predict_perturbation", {"dataset": "synthetic_norman"}),
        ("scfoundation", "predict_perturbation", {"dataset": "synthetic_norman"}),
        ("geneformer", "embed_cells", {"dataset": "synthetic_norman"}),
    ],
)
def test_wrapper_adapter_stubs(tmp_path: Path, wrapper: str, command: str, params_extra: dict) -> None:
    adapter = REPO_ROOT / "agentcoop" / "wrappers" / wrapper / "adapter.py"
    req = tmp_path / "request.json"
    out = tmp_path / "result.json"
    req.write_text(json.dumps({"command": command, "params": params_extra}), encoding="utf-8")
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(adapter), "--input", str(req), "--output", str(out)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    result = json.loads(out.read_text())
    assert result["ok"] is True, (wrapper, proc.stderr, result)


def test_wrapper_manifests_pinnable() -> None:
    base = REPO_ROOT / "agentcoop" / "wrappers"
    for name in ("geneagent", "gears", "scgpt", "scfoundation", "geneformer"):
        data = yaml.safe_load((base / name / "manifest.yaml").read_text())
        assert data["backend_type"] == "sandbox_repo"
        assert data["security"]["non_root"] is True
        assert data["source"]["commit"]
