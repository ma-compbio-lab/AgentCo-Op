"""Benchmark runner.

Compiles a blueprint per task, runs it through the orchestrator, grades
the output, and emits the `benchmarks.md` §11 reproducibility directory:

    runs/{dataset}/{method}/{timestamp}/
      config.yaml
      git_state.txt
      data_hashes.json
      model_versions.json
      workflow_blueprint.json
      predictions.jsonl
      metrics.json
      traces/            (per-task event logs)
      sandbox_logs/      (populated by sandbox backends when live)
      artifacts/         (populated by execution nodes)

Dry-run is the default when no API key is set; it uses MockLLM so the
whole pipeline exercises end-to-end offline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentcoop.backends import MockLLM, default_registry
from agentcoop.benchmarks import load as load_dataset
from agentcoop.benchmarks.common import BenchmarkTask, REPO_ROOT, RUNS_ROOT
from agentcoop.benchmarks.graders import grade as grade_prediction
from agentcoop.core.compiler import compile_workflow
from agentcoop.core.profiler import profile_task
from agentcoop.core.runtime import RunOutcome, RuntimeConfig, run_blueprint
from agentcoop.core.schema import Budget, WorkflowBlueprint
from agentcoop.skills import SkillRegistry


CONFIG_DIR = REPO_ROOT / "configs" / "benchmarks"


# ---------------------------------------------------------------------------
# Config loading with extends
# ---------------------------------------------------------------------------


def _merge(a: dict, b: dict) -> dict:
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
    provider = (config.get("model", {}) or {}).get("provider", "mock")
    if dry_run or provider == "mock" or not _has_api_key():
        return None
    raise NotImplementedError(
        f"Provider '{provider}' is not wired yet. Either use --dry-run or add a "
        "real LLMClient shim in agentcoop/backends/llm.py and re-run."
    )


# ---------------------------------------------------------------------------
# Reproducibility snapshot
# ---------------------------------------------------------------------------


def _git_state() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=REPO_ROOT
        )
        sha = out.stdout.strip()
        out2 = subprocess.run(
            ["git", "status", "--short"], capture_output=True, text=True, check=False, cwd=REPO_ROOT
        )
        dirty = out2.stdout.strip()
        return f"HEAD={sha}\nstatus:\n{dirty}\n"
    except FileNotFoundError:
        return "git not installed\n"


def _model_versions(config: dict) -> dict:
    m = (config.get("model") or {})
    return {
        "provider": m.get("provider", "mock"),
        "name": m.get("name", "mock-llm"),
        "temperature": m.get("temperature"),
        "max_tokens": m.get("max_tokens"),
        "env_OPENAI_API_KEY_present": bool(os.environ.get("OPENAI_API_KEY")),
        "env_ANTHROPIC_API_KEY_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def _data_hash(dataset_split_paths: list[Path]) -> dict:
    out = {}
    for p in dataset_split_paths:
        if not p.exists():
            continue
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out[str(p.relative_to(REPO_ROOT))] = h.hexdigest()
    return out


# ---------------------------------------------------------------------------
# Per-task / per-variant execution
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
    reference: Any = None

    def to_jsonl(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "task_id": self.task_id,
            "variant": self.variant,
            "blueprint_id": self.blueprint_id,
            "topology_level": self.topology_level,
            "complexity": round(self.complexity, 3),
            "provenance": self.provenance,
            "prediction": self.prediction,
            "final_output": self.final_output,
            "reference": self.reference,
            "score": round(self.score, 4),
            "metric": self.metric,
            "ok": self.ok,
            "tokens_used": self.tokens_used,
            "cost_usd": round(self.cost_usd, 6),
            "latency_s": round(self.latency_s, 3),
            "gate_activations": self.gate_activations,
            "patches_applied": self.patches_applied,
            "issues": self.issues,
        }


def _as_prediction_string(outcome: RunOutcome | None) -> tuple[str, dict[str, Any]]:
    if outcome is None or outcome.final is None:
        return "", {}
    out = outcome.final.output or {}
    for k in ("final_answer", "answer", "text", "result", "code"):
        if k in out and out[k] not in (None, ""):
            return str(out[k]), out
    return "", out


def _apply_variant(blueprint: WorkflowBlueprint, variant_cfg: dict) -> None:
    level = variant_cfg.get("force_topology_level")
    if level is not None:
        blueprint.provenance.append(f"force_topology_level:{level}")
    if variant_cfg.get("disable_gates"):
        for p in blueprint.gate_policies:
            p.max_activations = 0
    if variant_cfg.get("disable_reviewer"):
        blueprint.nodes = [n for n in blueprint.nodes if "review" not in n.role]
        # Prune dangling edges.
        keep = {n.node_id for n in blueprint.nodes}
        blueprint.edges = [e for e in blueprint.edges if e.source in keep and e.target in keep]


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
    _apply_variant(blueprint, variant_cfg)

    if dry_run:
        for node in blueprint.nodes:
            cfg.backends.get("llm").client.register(node.role, mock_pred)
            cfg.backends.get("llm").client.register(node.node_id, mock_pred)

    outcome = await run_blueprint(
        blueprint,
        config=cfg,
        payload={"task": task.prompt, "task_id": task.task_id, "input": task.input},
    )
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
        reference=task.reference,
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_out_dir(
    out_arg: str | None, dataset: str, method_tag: str
) -> Path:
    if out_arg:
        return Path(out_arg)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RUNS_ROOT / dataset / method_tag / ts


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

    method_tag = "ac-gated" if len(variants) > 1 else variants[0].lower()
    out_dir_path = _resolve_out_dir(out_dir, dataset, method_tag)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    (out_dir_path / "traces").mkdir(exist_ok=True)
    (out_dir_path / "sandbox_logs").mkdir(exist_ok=True)
    (out_dir_path / "artifacts").mkdir(exist_ok=True)

    # Reproducibility snapshot
    (out_dir_path / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (out_dir_path / "git_state.txt").write_text(_git_state(), encoding="utf-8")
    (out_dir_path / "model_versions.json").write_text(
        json.dumps(_model_versions(config), indent=2), encoding="utf-8"
    )
    data_paths = [REPO_ROOT / "data" / "aflow_aligned" / dataset / f"{split}.jsonl"]
    (out_dir_path / "data_hashes.json").write_text(
        json.dumps(_data_hash(data_paths), indent=2), encoding="utf-8"
    )

    tasks = load_dataset(dataset, split=split, limit=limit, aflow=aflow)
    if not tasks:
        raise RuntimeError(f"no tasks for {dataset}/{split}")

    registry = SkillRegistry().load_dir(REPO_ROOT / "agentcoop" / "skills")
    llm_client = _build_llm_client(config, dry_run) or MockLLM()
    backends = default_registry(llm_client=llm_client)
    cfg = RuntimeConfig(backends=backends, run_root=str(out_dir_path / "traces"))

    # Canned dry-run prediction — deliberately wrong so dry-runs don't
    # falsely inflate scores. Tests assert low accuracy in dry-run mode.
    mock_pred = {"final_answer": "", "confidence": 0.5, "rationale_summary": "dry-run mock"}

    # Write the first compiled blueprint for inspection.
    first_blueprint_written = False

    rows: list[BenchmarkRow] = []
    t0 = time.time()
    for variant in variants:
        for task in tasks:
            row = await _run_task(
                task, variant, config, registry, cfg, dry_run=dry_run, mock_pred=mock_pred
            )
            rows.append(row)

            if not first_blueprint_written:
                # Re-compile once just to persist a representative blueprint.
                profile = profile_task(task.prompt, task_id=task.task_id).profile
                bp = compile_workflow(profile, registry, budget=profile.budget)
                (out_dir_path / "workflow_blueprint.json").write_text(
                    bp.model_dump_json(indent=2), encoding="utf-8"
                )
                first_blueprint_written = True

    elapsed = time.time() - t0

    # predictions.jsonl
    pred_path = out_dir_path / "predictions.jsonl"
    with pred_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r.to_jsonl(), ensure_ascii=False) + "\n")

    # Aggregate metrics per variant
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
        gate_totals: dict[str, int] = {}
        for r in variant_rows:
            for g, n in r.gate_activations.items():
                gate_totals[g] = gate_totals.get(g, 0) + n
        metrics_by_variant[variant] = {
            "n_tasks": total,
            "score_avg": round(score_avg, 4),
            "ok_rate": round(ok_rate, 4),
            "tokens_total": tokens_total,
            "cost_total_usd": round(cost_total, 6),
            "latency_avg_s": round(latency_avg, 3),
            "route_distribution": by_level,
            "gate_totals": gate_totals,
        }

    metrics = {
        "dataset": dataset,
        "split": split,
        "n_tasks": len(tasks),
        "variants": list(variants),
        "elapsed_s": round(elapsed, 3),
        "by_variant": metrics_by_variant,
        "dry_run": dry_run,
        "run_dir": str(out_dir_path),
    }
    (out_dir_path / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


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
    p.add_argument("--variants", "--variant", nargs="*", default=None)
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
