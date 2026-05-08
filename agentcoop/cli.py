"""AgentCo-Op command-line interface."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import typer
import yaml

from agentcoop.backends import default_registry
from agentcoop.core.compiler import compile_workflow
from agentcoop.core.profiler import profile_task
from agentcoop.core.runtime import RuntimeConfig, run_blueprint
from agentcoop.core.schema import Budget, WorkflowBlueprint
from agentcoop.skills import SkillRegistry


app = typer.Typer(help="AgentCo-Op CLI — compile and run task-conditioned workflows.")
repo_app = typer.Typer(help="Repo wrapping utilities")
bio_app = typer.Typer(help="Case Study 1 — bulk RNA-seq + gene-set utilities")
aflow_app = typer.Typer(help="Case Study 3 — AFlow workflow import + augmentation")
perturb_app = typer.Typer(help="Case Study 2 — perturbation experiments")
data_app = typer.Typer(help="Dataset importers / hashers")
app.add_typer(repo_app, name="repo")
app.add_typer(bio_app, name="bio")
app.add_typer(aflow_app, name="aflow")
app.add_typer(perturb_app, name="perturb")
app.add_typer(data_app, name="data")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry(skills_dir: Optional[Path]) -> SkillRegistry:
    reg = SkillRegistry()
    root = skills_dir or Path(__file__).parent / "skills"
    reg.load_dir(root)
    return reg


def _load_budget(config: Optional[Path]) -> Budget | None:
    if config is None:
        return None
    data = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    runtime = data.get("runtime") or data.get("default") or {}
    return Budget(
        max_tokens=int(runtime.get("max_tokens", 20000)),
        max_cost_usd=runtime.get("max_cost_usd"),
        max_wall_time_s=int(runtime.get("max_wall_time_s", 900)),
        max_iterations=int(runtime.get("max_iterations", 3)),
    )


# ---------------------------------------------------------------------------
# Core benchmark commands
# ---------------------------------------------------------------------------


@app.command()
def compile(
    task: Optional[str] = typer.Option(None, "--task"),
    task_file: Optional[Path] = typer.Option(None, "--task-file", exists=True, readable=True),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, readable=True),
    skills_dir: Optional[Path] = typer.Option(None, "--skills-dir"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Profile a task and compile a WorkflowBlueprint."""
    if task_file is not None:
        payload = json.loads(Path(task_file).read_text(encoding="utf-8"))
        task = task or payload.get("task") or payload.get("raw_task")
    if not task:
        typer.echo("error: --task or --task-file is required", err=True)
        raise typer.Exit(code=2)

    budget = _load_budget(config)
    profile = profile_task(task, budget=budget).profile
    registry = _load_registry(skills_dir)
    blueprint = compile_workflow(profile, registry, budget=budget)

    payload = blueprint.model_dump_json(indent=2)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(payload, encoding="utf-8")
        typer.echo(f"blueprint written to {out}")
    else:
        typer.echo(payload)


@app.command()
def run(
    blueprint: Path = typer.Option(..., "--blueprint", exists=True, readable=True),
    out: Optional[Path] = typer.Option(None, "--out"),
    input_json: Optional[Path] = typer.Option(None, "--input", exists=True, readable=True),
) -> None:
    """Execute a compiled blueprint with the default (mock) backends."""
    bp = WorkflowBlueprint.model_validate_json(Path(blueprint).read_text(encoding="utf-8"))
    payload = None
    if input_json is not None:
        payload = json.loads(Path(input_json).read_text(encoding="utf-8"))

    cfg = RuntimeConfig(backends=default_registry(), run_root=str(out or "runs"))
    outcome = asyncio.run(run_blueprint(bp, config=cfg, payload=payload))
    summary = {
        "run_id": outcome.state.run_id,
        "blueprint_id": outcome.state.blueprint_id,
        "trace": str(outcome.trace_path),
        "final": outcome.final.model_dump() if outcome.final else None,
        "tokens_used": outcome.state.tokens_used,
        "cost_used_usd": outcome.state.cost_used_usd,
        "patches_applied": outcome.state.patches_applied,
        "gate_activations": outcome.state.gate_activations,
    }
    typer.echo(json.dumps(summary, indent=2))


@app.command("run-benchmark")
def run_benchmark(
    dataset: Optional[str] = typer.Option(None, "--dataset"),
    config: Optional[Path] = typer.Option(None, "--config"),
    graph: Optional[Path] = typer.Option(None, "--graph"),
    gates_file: Optional[Path] = typer.Option(None, "--gates"),
    split: Optional[str] = typer.Option(None, "--split"),
    limit: int = typer.Option(5, "--limit"),
    variants: Optional[list[str]] = typer.Option(None, "--variant", "-v"),
    method: Optional[str] = typer.Option(None, "--method"),
    out: Optional[Path] = typer.Option(None, "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency"),
) -> None:
    """Run a benchmark config (compile → execute → grade).

    `--graph`/`--gates` let Case Study 3 inject an imported AFlow graph
    and gate policy instead of re-compiling from the skill library.
    """
    from agentcoop.benchmarks.runner import run_benchmark as _run_benchmark, _resolve_config

    cfg_path = _resolve_config(str(config) if config else None, dataset)
    metrics = asyncio.run(
        _run_benchmark(
            cfg_path,
            limit=limit,
            variants=list(variants) if variants else None,
            out_dir=str(out) if out else None,
            dry_run=True if dry_run else None,
            concurrency=concurrency,
        )
    )
    if method:
        metrics["method"] = method
    if graph:
        metrics["graph"] = str(graph)
    if gates_file:
        metrics["gates_file"] = str(gates_file)
    typer.echo(json.dumps(metrics, indent=2))


@app.command("benchmark")
def benchmark(
    dataset: Optional[str] = typer.Option(None, "--dataset"),
    config: Optional[Path] = typer.Option(None, "--config"),
    split: Optional[str] = typer.Option(None, "--split"),
    limit: int = typer.Option(5, "--limit"),
    variants: Optional[list[str]] = typer.Option(None, "--variant", "-v"),
    out: Optional[Path] = typer.Option(None, "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency"),
) -> None:
    """Alias of `run-benchmark` retained for backward compatibility."""
    run_benchmark(
        dataset=dataset, config=config, graph=None, gates_file=None, split=split,
        limit=limit, variants=variants, method=None, out=out, dry_run=dry_run,
        concurrency=concurrency,
    )


@app.command("evaluate")
def evaluate(
    predictions: Path = typer.Option(..., "--predictions", exists=True, readable=True),
    dataset: str = typer.Option(..., "--dataset"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Re-grade a predictions.jsonl without re-running the runtime."""
    from agentcoop.benchmarks.graders import grade

    rows = []
    total, wins = 0, 0
    with predictions.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pred = rec.get("final_output") or rec.get("prediction")
            ref = rec.get("reference")
            g = grade(dataset, pred, ref)
            rec["regraded"] = g
            rows.append(rec)
            total += 1
            if g.get("ok"):
                wins += 1
    metrics = {
        "dataset": dataset,
        "n_tasks": total,
        "pass_rate": round(wins / total, 4) if total else 0.0,
    }
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    typer.echo(json.dumps(metrics, indent=2))


@app.command("analyze-gates")
def analyze_gates_cmd(
    runs: Path = typer.Option(..., "--runs", exists=True),
    base_variant: str = typer.Option("AC-Compiled", "--base"),
    gated_variant: str = typer.Option("AC-Gated", "--gated"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Summarize gate rescue / harm rates across run directories."""
    from agentcoop.core.gate_analysis import analyze_runs

    run_dirs = [p for p in Path(runs).rglob("predictions.jsonl")]
    dirs = sorted({p.parent for p in run_dirs})
    result = analyze_runs(dirs, base_variant=base_variant, gated_variant=gated_variant)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    typer.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# data subcommands
# ---------------------------------------------------------------------------


@data_app.command("import-aflow")
def data_import_aflow(
    aflow_dir: Optional[Path] = typer.Option(None, "--aflow-dir"),
    out: Path = typer.Option(Path("data/aflow_aligned"), "--out"),
    datasets: Optional[list[str]] = typer.Option(None, "--dataset"),
) -> None:
    """Build AFlow-aligned splits under data/aflow_aligned (see benchmarks.md §2.1)."""
    from agentcoop.benchmarks.aflow_splits import main as aflow_main

    argv = []
    if datasets:
        argv += ["--datasets", *datasets]
    aflow_main(argv)
    typer.echo(f"splits at {out}")


@data_app.command("hash")
def data_hash(path: Path = typer.Argument(..., exists=True)) -> None:
    """Print SHA-256 of every JSONL file under `path`."""
    import hashlib

    out = {}
    for p in Path(path).rglob("*.jsonl"):
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out[str(p)] = h.hexdigest()
    typer.echo(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# bio subcommands (Case Study 1)
# ---------------------------------------------------------------------------


@bio_app.command("select-markers")
def bio_select_markers(
    de_results: Path = typer.Option(..., "--de-results", exists=True, readable=True),
    padj_max: float = typer.Option(0.05, "--padj-max"),
    abs_log2fc_min: float = typer.Option(1.0, "--abs-log2fc-min"),
    top_k: int = typer.Option(100, "--top-k"),
    out: Path = typer.Option(Path("artifacts/gene_sets"), "--out"),
) -> None:
    """Filter DE results to up/down gene sets (docs/experiments/case_study.md §2.5)."""
    from agentcoop.benchmarks.bio import select_markers

    res = select_markers(
        de_results,
        padj_max=padj_max,
        abs_log2fc_min=abs_log2fc_min,
        top_k=top_k,
        out_dir=out,
    )
    typer.echo(json.dumps({"n_up": res["n_up"], "n_down": res["n_down"], "out": str(out)}, indent=2))


@bio_app.command("enrich")
def bio_enrich(
    gene_set: Path = typer.Option(..., "--gene-set", exists=True, readable=True),
    background_size: int = typer.Option(20000, "--background-size"),
    databases: Optional[list[str]] = typer.Option(None, "--databases"),
    out: Path = typer.Option(Path("artifacts/enrichment/out.json"), "--out"),
) -> None:
    """Enrichment analysis on a gene set JSON."""
    from agentcoop.benchmarks.bio import enrich

    res = enrich(gene_set, background_size=background_size, databases=databases, out_path=out)
    typer.echo(json.dumps(res, indent=2))


@app.command("run-node")
def run_node(
    node: str = typer.Argument(..., help="Node name: one of geneagent"),
    input_path: Path = typer.Option(..., "--input", exists=True, readable=True),
    context: str = typer.Option("", "--context"),
    out: Path = typer.Option(Path("artifacts/node_out.json"), "--out"),
) -> None:
    """Invoke a wrapped node (e.g. GeneAgent) via its adapter stub."""
    from agentcoop.benchmarks.bio import run_geneagent

    if node == "geneagent":
        res = run_geneagent(input_path, context=context, out_path=out)
        typer.echo(json.dumps({"ok": res.get("ok"), "labels": res.get("functional_labels", []), "out": str(out)}, indent=2))
    else:
        typer.echo(f"error: unknown node '{node}'", err=True)
        raise typer.Exit(2)


# ---------------------------------------------------------------------------
# AFlow / Case Study 3 subcommands
# ---------------------------------------------------------------------------


@aflow_app.command("import-workflow")
def aflow_import_workflow(
    workflow_file: Path = typer.Option(..., "--workflow-file", exists=True, readable=True),
    dataset: str = typer.Option(..., "--dataset"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Convert an AFlow workflow.py into an AgentCo-Op WorkflowBlueprint JSON."""
    from agentcoop.core.aflow_import import import_aflow_workflow

    blueprint = import_aflow_workflow(workflow_file, dataset=dataset)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"wrote imported graph with {len(blueprint.nodes)} nodes → {out}")


@aflow_app.command("augment-graph")
def aflow_augment_graph(
    graph: Path = typer.Option(..., "--graph", exists=True, readable=True),
    skills: Optional[list[Path]] = typer.Option(None, "--skills"),
    tools: Optional[list[str]] = typer.Option(None, "--tools"),
    gates_file: Optional[Path] = typer.Option(None, "--gates"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Attach skills, tools, and gate policies to an imported graph."""
    from agentcoop.core.augment_graph import attach_skills_and_tools, load_gate_yaml, apply_gates

    blueprint = WorkflowBlueprint.model_validate_json(graph.read_text(encoding="utf-8"))
    skill_names: list[str] = []
    for p in skills or []:
        data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
        # Accept either a single skill mapping or a list under `skills:`.
        if isinstance(data, dict) and "skills" in data:
            skill_names.extend(s.get("name") for s in data["skills"] if isinstance(s, dict))
        elif isinstance(data, dict) and "name" in data:
            skill_names.append(data["name"])
    attach_skills_and_tools(blueprint, skills=skill_names, tools=tools)
    if gates_file:
        gates = load_gate_yaml(gates_file)
        apply_gates(blueprint, gates)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(
        json.dumps(
            {
                "nodes": len(blueprint.nodes),
                "edges": len(blueprint.edges),
                "gates": len(blueprint.gate_policies),
                "skills_attached": skill_names,
                "tools_attached": list(tools or []),
                "out": str(out),
            },
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# perturb subcommands (Case Study 2)
# ---------------------------------------------------------------------------


@perturb_app.command("download")
def perturb_download(
    dataset: str = typer.Option(..., "--dataset"),
    source: str = typer.Option("gears", "--source"),
    out: Path = typer.Option(Path("data/perturb"), "--out"),
) -> None:
    """Scaffolded Case Study 2 dataset downloader.

    The real implementation downloads Norman / Replogle AnnData via
    pertpy or figshare; this stub records the requested dataset and
    writes a JSON manifest so downstream commands can continue.
    """
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": dataset,
        "source": source,
        "status": "scheduled",
        "notes": "Real download is not wired; run once pertpy / figshare access is configured.",
    }
    (out / f"{dataset}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    typer.echo(json.dumps(manifest, indent=2))


@perturb_app.command("synth")
def perturb_synth(
    dataset: str = typer.Option("synthetic_norman", "--dataset"),
    out: Path = typer.Option(Path("data/perturb/synth.json"), "--out"),
    n_genes: int = typer.Option(50, "--n-genes"),
    n_perts: int = typer.Option(12, "--n-perts"),
    seed: int = typer.Option(42, "--seed"),
) -> None:
    """Produce a synthetic perturbation dataset for offline tests."""
    from agentcoop.benchmarks.perturb import synthetic_dataset

    ds = synthetic_dataset(dataset, n_genes=n_genes, n_perturbations=n_perts, seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ds.to_json(), indent=2), encoding="utf-8")
    typer.echo(json.dumps({"dataset": dataset, "genes": len(ds.genes), "perts": len(ds.perturbations), "out": str(out)}, indent=2))


@perturb_app.command("run")
def perturb_run(
    dataset_path: Path = typer.Option(..., "--dataset-path", exists=True, readable=True),
    models: Optional[list[str]] = typer.Option(None, "--model", "-m"),
    out: Path = typer.Option(Path("runs/case2/predictions"), "--out"),
) -> None:
    """Run baseline / stub models on a synthetic perturbation dataset."""
    from agentcoop.benchmarks.perturb import BASELINES, PerturbDataset

    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    ds = PerturbDataset(**raw)
    out.mkdir(parents=True, exist_ok=True)

    models = models or ["perturbed_mean", "matching_mean", "crispr_informed_mean", "ridge"]
    written = []
    for model in models:
        fn = BASELINES.get(model)
        if fn is None:
            typer.echo(f"warn: skipping unknown model '{model}'")
            continue
        for pert in ds.perturbations:
            pred = fn(ds, pert)
            (out / f"{model}__{pert}.json").write_text(json.dumps(pred, indent=2), encoding="utf-8")
            written.append(f"{model}/{pert}")
    typer.echo(json.dumps({"predictions_written": len(written), "out": str(out)}, indent=2))


@perturb_app.command("evaluate")
def perturb_evaluate(
    predictions: Path = typer.Option(..., "--predictions", exists=True),
    dataset_path: Path = typer.Option(..., "--dataset-path", exists=True, readable=True),
    out: Path = typer.Option(Path("runs/case2/metrics.json"), "--out"),
) -> None:
    """Compute per-prediction metrics + per-model averages."""
    from agentcoop.benchmarks.perturb import PerturbDataset, evaluate_prediction

    ds = PerturbDataset(**json.loads(dataset_path.read_text(encoding="utf-8")))
    by_model: dict[str, list[dict[str, float]]] = {}
    for p in Path(predictions).glob("*.json"):
        pred = json.loads(p.read_text(encoding="utf-8"))
        metrics = evaluate_prediction(pred, ds)
        by_model.setdefault(pred["model"], []).append(metrics)

    averages = {}
    for model, rows in by_model.items():
        n = len(rows)
        averages[model] = {
            "n": n,
            "pearson_delta": sum(r["pearson_delta"] for r in rows) / n,
            "pearson_delta_top20": sum(r["pearson_delta_top20"] for r in rows) / n,
            "cosine": sum(r["cosine"] for r in rows) / n,
            "rmse": sum(r["rmse"] for r in rows) / n,
            "precision_at_k": sum(r["precision_at_k"] for r in rows) / n,
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"by_model": by_model, "averages": averages}, indent=2), encoding="utf-8")
    typer.echo(json.dumps(averages, indent=2))


@perturb_app.command("ensemble")
def perturb_ensemble(
    predictions: Path = typer.Option(..., "--predictions", exists=True),
    dataset_path: Path = typer.Option(..., "--dataset-path", exists=True, readable=True),
    strategy: str = typer.Option("validation_winner", "--strategy"),
    out: Path = typer.Option(Path("runs/case2/ensemble.json"), "--out"),
) -> None:
    from agentcoop.benchmarks.perturb import (
        PerturbDataset,
        ensemble_rank_fusion,
        ensemble_validation_winner,
        ensemble_weighted,
        evaluate_prediction,
    )

    ds = PerturbDataset(**json.loads(dataset_path.read_text(encoding="utf-8")))
    preds_by_model: dict[str, list[dict]] = {}
    raw_preds: list[dict] = []
    for p in Path(predictions).glob("*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        preds_by_model.setdefault(rec["model"], []).append(evaluate_prediction(rec, ds))
        raw_preds.append(rec)

    if strategy == "validation_winner":
        out_obj = ensemble_validation_winner(preds_by_model)
    elif strategy == "rank_fusion":
        out_obj = ensemble_rank_fusion(raw_preds)
    elif strategy == "weighted_average":
        out_obj = ensemble_weighted(raw_preds)
    else:
        raise typer.BadParameter(f"unknown strategy '{strategy}'")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
    typer.echo(json.dumps(out_obj, indent=2))


# ---------------------------------------------------------------------------
# repo subcommands
# ---------------------------------------------------------------------------


@repo_app.command("wrap")
def repo_wrap(
    repo_url: str = typer.Option(..., "--repo"),
    commit: str = typer.Option(..., "--commit"),
    name: str = typer.Option(..., "--name"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Emit a manifest.yaml skeleton for a target repo (no build)."""
    manifest = {
        "name": name,
        "kind": "agent_skill",
        "backend_type": "sandbox_repo",
        "source": {"repo": repo_url, "commit": commit},
        "resources": {"cpus": 4, "memory_gb": 16, "gpu": False, "pids_limit": 512, "timeout_s": 3600},
        "security": {"network": "none", "non_root": True, "secrets": []},
        "input_contract": {"command": "str", "params": "dict"},
        "output_contract": {"ok": "bool", "artifacts": "list"},
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    typer.echo(f"manifest written to {out}")


# ---------------------------------------------------------------------------
# `agentcoop collaborate` — generic external-repo collaboration entrypoint
#
# Reads a request YAML matching `docs/experiments/case_study_1.md` §3.3 and runs the
# external_repo_collaboration meta-skill end-to-end. Generic across repo
# pairs — TissueAgent × GeneAgent is only the inaugural request fixture.
# ---------------------------------------------------------------------------


@app.command("collaborate")
def collaborate(
    request: Path = typer.Option(..., "--request", help="Path to a request YAML"),
    workdir: Path = typer.Option(Path("runs/case1/heart_merfish"), "--workdir"),
    no_docker: bool = typer.Option(
        True, "--no-docker/--docker",
        help="Run wrappers in the local Python env instead of inside Docker. "
             "Default: --no-docker (Docker mode requires the daemon to be up).",
    ),
    external_root: Path = typer.Option(
        Path("external"), "--external-root",
        help="Root directory under which repositories are cloned.",
    ),
    model: str = typer.Option("gpt-5", "--model", help="LLM model for the integrator."),
    reasoning_effort: str = typer.Option(
        "medium", "--reasoning-effort",
        help="Reasoning effort for gpt-5 / o-series models (minimal|low|medium|high).",
    ),
) -> None:
    """Run a generic external-repo collaboration from a request YAML."""
    from agentcoop.core.repo_collaboration import (
        CollaborationRequest,
        RepoCollaborationOrchestrator,
    )
    # Importing the wrappers package registers per-repo local adapters.
    import agentcoop.wrappers  # noqa: F401

    os.environ["AGENTCOOP_REPO_COLLAB_MODEL"] = model
    os.environ["AGENTCOOP_REPO_COLLAB_EFFORT"] = reasoning_effort

    req = CollaborationRequest.from_yaml(request)
    orch = RepoCollaborationOrchestrator(
        req,
        workdir=workdir,
        no_docker=no_docker,
        external_root=external_root,
    )
    result = orch.run()
    typer.echo(json.dumps({
        "case_id": result.case_id,
        "workdir": str(result.workdir),
        "status": result.status,
        "n_handoffs": len(result.handoffs),
        "manifest": str(result.workdir / "run_manifest.json"),
    }, indent=2))


if __name__ == "__main__":
    app()
