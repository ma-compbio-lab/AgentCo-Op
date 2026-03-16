from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aggregate_benchmark_shards_discovers_timestamped_run_dirs(tmp_path) -> None:
    script = _load_script_module(
        REPO_ROOT / "scripts" / "aggregate_benchmark_shards.py",
        "aggregate_benchmark_shards_test",
    )

    runs_root = tmp_path / "runs"
    newer = runs_root / "math-test-shard00of08" / "20260316_120000_new"
    older = runs_root / "math-test-shard00of08" / "20260316_110000_old"
    second = runs_root / "math-test-shard01of08" / "20260316_121500_second"
    for run_dir in (newer, older, second):
        (run_dir / "eval").mkdir(parents=True)
        (run_dir / "eval" / "task_results.json").write_text("[]", encoding="utf-8")

    discovered = script.discover_latest_shards(runs_root, "math", "test")

    assert discovered == [newer, second]


def test_aggregate_benchmark_budget_sweeps_discovers_timestamped_run_dirs(tmp_path) -> None:
    script = _load_script_module(
        REPO_ROOT / "scripts" / "aggregate_benchmark_budget_sweeps.py",
        "aggregate_benchmark_budget_sweeps_test",
    )

    sweeps_root = tmp_path / "sweeps"
    newer = sweeps_root / "medqa_v2" / "openai_gpt4o_mini" / "balanced" / "medqa-full-shard00of08" / "20260316_120000_new"
    older = sweeps_root / "medqa_v2" / "openai_gpt4o_mini" / "balanced" / "medqa-full-shard00of08" / "20260316_110000_old"
    second = sweeps_root / "medqa_v2" / "openai_gpt4o_mini" / "balanced" / "medqa-full-shard01of08" / "20260316_121500_second"
    for run_dir in (newer, older, second):
        (run_dir / "eval").mkdir(parents=True)
        (run_dir / "eval" / "task_results.json").write_text("[]", encoding="utf-8")

    discovered = script.discover_latest_shards(sweeps_root)

    assert discovered == {("medqa_v2", "openai_gpt4o_mini", "balanced"): [newer, second]}
