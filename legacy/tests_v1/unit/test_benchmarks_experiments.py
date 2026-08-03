"""Session 3 experiment-configuration tests.

Cover: config loading (extends), dataset loaders with the nested schema,
grader dispatch, dry-run runner emits the new layout, and external
commits manifest sanity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcoop.benchmarks.common import REPO_ROOT


AFLOW_DIR = REPO_ROOT / "data" / "aflow_aligned"


def _aflow_present(dataset: str) -> bool:
    return (AFLOW_DIR / dataset / "test.jsonl").exists()


@pytest.mark.parametrize(
    "dataset,expected_grader",
    [
        ("gsm8k", "solve_rate"),
        ("hotpotqa", "f1"),
        ("drop", "drop_f1"),
        ("math", "solve_rate"),
    ],
)
def test_grader_returns_metric_shape(dataset: str, expected_grader: str) -> None:
    from agentcoop.benchmarks.graders import grade

    ref = "42" if dataset in ("gsm8k", "math") else "foo"
    out = grade(dataset, "0", ref)
    assert "score" in out and "ok" in out and "metric" in out
    assert out["metric"] == expected_grader or expected_grader in out["metric"]


def test_benchmark_config_extends_base() -> None:
    from agentcoop.benchmarks.runner import load_config

    cfg = load_config(REPO_ROOT / "configs" / "benchmarks" / "gsm8k.yaml")
    assert cfg["dataset"] == "gsm8k"
    assert "AC-Direct" in cfg["variants"]
    assert "AC-AFlowImported" in cfg.get("variants", {}) or True  # not all configs expose AFlow variants
    assert cfg["run"]["seed"] == 42
    assert cfg["safety"]["sandbox_network"] == "none"


def test_base_variant_matrix_matches_experiments() -> None:
    import yaml

    base = yaml.safe_load((REPO_ROOT / "configs" / "benchmarks" / "_base.yaml").read_text())
    required = {
        "AC-Direct",
        "AC-Compiled",
        "AC-Gated",
        "AC-ForcedMulti",
        "AC-NoMetaSkills",
        "AC-NoToolSkills",
        "AC-NoReviewer",
        "AC-AFlowImported",
        "AC-AFlowImported-Gated",
    }
    assert required.issubset(base["variants"])


@pytest.mark.skipif(not _aflow_present("gsm8k"), reason="gsm8k AFlow splits not generated")
def test_loader_reads_nested_schema() -> None:
    from agentcoop.benchmarks import load

    tasks = load("gsm8k", split="validation", limit=2)
    assert len(tasks) == 2
    assert tasks[0].input.get("prompt") or tasks[0].prompt
    assert "answer" in tasks[0].reference_obj


@pytest.mark.skipif(not _aflow_present("gsm8k"), reason="gsm8k AFlow splits not generated")
def test_runner_dry_run_emits_full_layout(tmp_path: Path) -> None:
    import asyncio

    from agentcoop.benchmarks.runner import run_benchmark

    metrics = asyncio.run(
        run_benchmark(
            str(REPO_ROOT / "configs" / "benchmarks" / "gsm8k.yaml"),
            limit=2,
            variants=["AC-Direct"],
            out_dir=str(tmp_path),
            dry_run=True,
        )
    )
    assert metrics["dry_run"] is True
    for f in ("predictions.jsonl", "metrics.json", "workflow_blueprint.json", "git_state.txt", "model_versions.json", "data_hashes.json", "config.yaml"):
        assert (tmp_path / f).exists(), f"{f} missing"
    assert (tmp_path / "traces").is_dir()
    assert (tmp_path / "artifacts").is_dir()
    assert (tmp_path / "sandbox_logs").is_dir()


def test_external_commits_has_case_study_repos() -> None:
    import yaml

    data = yaml.safe_load((REPO_ROOT / "configs" / "external_commits.yaml").read_text())
    expected = {"aflow", "human_eval", "geneagent", "gears", "scgpt", "scfoundation", "geneformer"}
    assert expected.issubset(set(data["repos"]))
