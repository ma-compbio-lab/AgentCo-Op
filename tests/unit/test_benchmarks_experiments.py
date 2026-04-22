"""Tests for the experiment-configuration layer.

These cover: benchmark config loading (with `extends:`), dataset loaders
(read from the downloaded JSONL), grader dispatch, and the dry-run
runner path.
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
    assert "variants" in cfg
    # Inherited from _base:
    assert "run" in cfg and cfg["run"]["seed"] == 42
    assert cfg["safety"]["sandbox_network"] == "none"


@pytest.mark.skipif(not _aflow_present("gsm8k"), reason="gsm8k AFlow splits not generated")
def test_gsm8k_loader_reads_aflow_splits() -> None:
    from agentcoop.benchmarks import load

    tasks = load("gsm8k", split="test", limit=3)
    assert len(tasks) == 3
    assert all(t.dataset == "gsm8k" for t in tasks)
    assert all(isinstance(t.prompt, str) and t.prompt for t in tasks)


@pytest.mark.skipif(not _aflow_present("gsm8k"), reason="gsm8k AFlow splits not generated")
def test_runner_dry_run_emits_predictions_csv(tmp_path: Path) -> None:
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
    assert (tmp_path / "predictions.csv").exists()
    assert (tmp_path / "metrics.json").exists()


def test_external_commits_yaml_sane() -> None:
    import yaml

    data = yaml.safe_load((REPO_ROOT / "configs" / "external_commits.yaml").read_text())
    expected = {"aflow", "biodiscovery_agent", "spatial_agent", "human_eval", "spatialbench"}
    assert expected.issubset(set(data["repos"]))
