"""Benchmark runner.

Given a dataset + variant config, compile a blueprint per task, execute
with the configured backend, grade the output, and emit
`predictions.csv` + `metrics.json`.

The runner defaults to **dry-run + MockLLM** when no API key is present
in the environment, so everything is exercisable offline. Switch to a
live run by setting `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` and removing
`--dry-run`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentcoop.backends import MockLLM, default_registry
from agentcoop.benchmarks import load as load_dataset
from agentcoop.benchmarks.common import BenchmarkTask, REPO_ROOT
from agentcoop.benchmarks.graders import grade as grade_prediction
from agentcoop.core.compiler import compile_workflow
from agentcoop.core.profiler import profile_task
from agentcoop.core.runtime import RunOutcome, RuntimeConfig, run_blueprint
from agentcoop.core.schema import Budget, WorkflowBlueprint
from agentcoop.skills import SkillRegistry


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


CONFIG_DIR = REPO_ROOT / "configs" / "benchmarks"


def _merge(a: dict, b: dict) -> dict:
    """Deep merge `b` on top of `a` (returns new dict)."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = data.pop("extends", None)
    if parent:
        parent_path = CONFIG_DIR / f"{parent}.yaml"
        if not parent_path.exists():
            parent_path = path.parent / f"{parent}.yaml"
        base = load_config(parent_path)
        data = _merge(base, data)
    return data


def _has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def _build_llm_client(config: dict, dry_run: bool) -> Any | None:
    """Return an LLMClient or None to use the default MockLLM."""
    provider = (config.get("model", {}) or {}).get("provider", "mock")
    if dry_run or provider == "mock" or not _has_api_key():
        return None
    # Real providers are not wired in framework-config phase.
    raise NotImplementedError(
        f"Provider '{provider}' is not wired yet. Either use --dry-run or add a "
        "real LLMClient shim in agentcoop/backends/llm.py and re-run."
    )


# ---------------------------------------------------------------------------
# Prediction + reporting
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkRow:
    dataset: str
    split: str
    task_id: str
    variant: str
    blueprint_id: str
    topology_level: int
    complexity: float
    provenance: list[str]
    prediction: str
    final_output: dict[str, Any]
    score: float
    metric: str
    ok: bool
    tokens_used: int
    cost_usd: float
    latency_s: float
    gate_activations: dict[str, int]
    patches_applied: int
    issues: list[str]


def _as_prediction_string(outcome: RunOutcome | None) -> tuple[str, dict[str, Any]]:
    if outcome is None or outcome.final is None:
        return "", {}
    out = outcome.final.output or {}
    for k in ("final_answer", "answer", "text", "result", "code"):
        if k in out and out[k] not in (None, ""):
            return str(out[k]), out
    return "", out


def _force_topology_level(blueprint: WorkflowBlueprint, level: int | None) -> None:
    if level is None:
        return
    # Lightweight override: annotate the blueprint. A richer impl would
    # re-compile with a level filter; for dry-run reporting this is enough.
    blueprint.provenance.append(f"force_topology_level:{level}")


def _disable_gates_if_requested(blueprint: WorkflowBlueprint, disable: bool) -> None:
    if disable:
        for p in blueprint.gate_policies:
            p.max_activations = 0


# ---------------------------------------------------------------------------
# Core run loop
# ---------------------------------------------------------------------------


async def _run_task(
    task: BenchmarkTask,
    variant: str,
    config: dict,
    registry: SkillRegistry,
    cfg: RuntimeConfig,
    *,
    dry_run: bool,
    mock_pred: Any,
) -> BenchmarkRow:
    profile = profile_task(task.prompt, task_id=task.task_id).profile
    budget = config.get("budget", {}) or {}
    profile_budget = Budget(
        max_tokens=int(budget.get("max_tokens", 20000)),
        max_cost_usd=budget.get("max_cost_usd"),
        max_wall_time_s=int(budget.get("max_wall_time_s", 600)),
        max_iterations=int(budget.get("max_iterations", 3)),
    )
    profile = profile.model_copy(update={"budget": profile_budget})

    blueprint = compile_workflow(profile, registry, budget=profile_budget)

    variant_cfg = (config.get("variants", {}) or {}).get(variant, {}) or {}
    _force_topology_level(blueprint, variant_cfg.get("force_topology_level"))
    _disable_gates_if_requested(blueprint, bool(variant_cfg.get("disable_gates")))

    if dry_run:
        # Seed MockLLM so the orchestrator can walk the graph offline.
        for node in blueprint.nodes:
            cfg.backends.get("llm").client.register(node.role, mock_pred)
            cfg.backends.get("llm").client.register(node.node_id, mock_pred)

    outcome = await run_blueprint(blueprint, config=cfg, payload={"task": task.prompt, "task_id": task.task_id})
    pred_s, pred_obj = _as_prediction_string(outcome)
    grader_payload = pred_obj if pred_obj else pred_s
    grader_out = grade_prediction(task.dataset, grader_payload or pred_s, task.reference, task)

    return BenchmarkRow(
        dataset=task.dataset,
        split=task.split,
        task_id=task.task_id,
        variant=variant,
        blueprint_id=blueprint.blueprint_id,
        topology_level=blueprint.topology_level,
        complexity=blueprint.complexity,
        provenance=list(blueprint.provenance),
        prediction=pred_s[:500],
        final_output=pred_obj,
        score=grader_out.get("score", 0.0),
        metric=grader_out.get("metric", ""),
        ok=bool(grader_out.get("ok", False)),
        tokens_used=outcome.state.tokens_used,
        cost_usd=outcome.state.cost_used_usd,
        latency_s=outcome.state.elapsed_s,
        gate_activations=dict(outcome.state.gate_activations),
        patches_applied=outcome.state.patches_applied,
        issues=list(grader_out.get("issues", [])),
    )


async def run_benchmark(
    config_path: str,
    *,
    limit: int | None = None,
    variants: list[str] | None = None,
    out_dir: str | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    dataset = config["dataset"]
    split = config.get("split", "test")
    aflow = bool(config.get("aflow_aligned", True))
    variants = variants or list((config.get("variants") or {}).keys())
    dry_run = dry_run if dry_run is not None else (os.environ.get("AGENTCOOP_MODE") == "dry_run" or not _has_api_key())

    tasks = load_dataset(dataset, split=split, limit=limit, aflow=aflow)
    if not tasks:
        raise RuntimeError(f"no tasks for {dataset}/{split}")

    out_dir_path = Path(out_dir or f"runs/benchmarks/{dataset}/{split}")
    out_dir_path.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry().load_dir(REPO_ROOT / "agentcoop" / "skills")
    llm_client = _build_llm_client(config, dry_run) or MockLLM()
    backends = default_registry(llm_client=llm_client)
    cfg = RuntimeConfig(backends=backends, run_root=str(out_dir_path / "trace"))

    mock_pred = {"final_answer": "0", "confidence": 0.5, "rationale_summary": "mock"}

    rows: list[BenchmarkRow] = []
    t0 = time.time()
    for variant in variants:
        for task in tasks:
            row = await _run_task(
                task,
                variant,
                config,
                registry,
                cfg,
                dry_run=dry_run,
                mock_pred=mock_pred,
            )
            rows.append(row)

    elapsed = time.time() - t0

    # Write predictions.csv
    csv_path = out_dir_path / "predictions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "dataset", "split", "task_id", "variant", "blueprint_id",
            "topology_level", "complexity", "prediction", "score", "metric",
            "ok", "tokens_used", "cost_usd", "latency_s", "gate_activations",
            "patches_applied", "issues", "provenance",
        ])
        for r in rows:
            writer.writerow([
                r.dataset, r.split, r.task_id, r.variant, r.blueprint_id,
                r.topology_level, f"{r.complexity:.3f}", r.prediction,
                f"{r.score:.4f}", r.metric, r.ok, r.tokens_used,
                f"{r.cost_usd:.6f}", f"{r.latency_s:.3f}",
                json.dumps(r.gate_activations),
                r.patches_applied, ";".join(r.issues),
                ";".join(r.provenance),
            ])

    # Aggregate metrics
    metrics_by_variant: dict[str, Any] = {}
    for variant in variants:
        variant_rows = [r for r in rows if r.variant == variant]
        total = len(variant_rows)
        if total == 0:
            continue
        score_avg = sum(r.score for r in variant_rows) / total
        ok_rate = sum(1 for r in variant_rows if r.ok) / total
        cost_total = sum(r.cost_usd for r in variant_rows)
        tokens_total = sum(r.tokens_used for r in variant_rows)
        latency_avg = sum(r.latency_s for r in variant_rows) / total
        by_level: dict[int, int] = {}
        for r in variant_rows:
            by_level[r.topology_level] = by_level.get(r.topology_level, 0) + 1
        metrics_by_variant[variant] = {
            "n_tasks": total,
            "score_avg": round(score_avg, 4),
            "ok_rate": round(ok_rate, 4),
            "tokens_total": tokens_total,
            "cost_total_usd": round(cost_total, 6),
            "latency_avg_s": round(latency_avg, 3),
            "route_distribution": by_level,
        }

    metrics = {
        "dataset": dataset,
        "split": split,
        "n_tasks": len(tasks),
        "variants": list(variants),
        "elapsed_s": round(elapsed, 3),
        "by_variant": metrics_by_variant,
        "dry_run": dry_run,
    }
    (out_dir_path / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _resolve_config(config_arg: str | None, dataset_arg: str | None) -> str:
    if config_arg:
        return config_arg
    if dataset_arg:
        return str(CONFIG_DIR / f"{dataset_arg}.yaml")
    raise SystemExit("error: --config or --dataset is required")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config")
    p.add_argument("--dataset")
    p.add_argument("--split", default=None)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--variants", nargs="*", default=None)
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    config_path = _resolve_config(args.config, args.dataset)
    metrics = asyncio.run(
        run_benchmark(
            config_path,
            limit=args.limit,
            variants=args.variants,
            out_dir=args.out,
            dry_run=args.dry_run or None,
        )
    )
    json.dump(metrics, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
