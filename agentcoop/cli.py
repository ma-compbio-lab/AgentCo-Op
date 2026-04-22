"""AgentCo-Op command-line interface."""

from __future__ import annotations

import asyncio
import json
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
app.add_typer(repo_app, name="repo")


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


@app.command()
def compile(
    task: Optional[str] = typer.Option(None, "--task", help="Raw task string"),
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

    cfg = RuntimeConfig(
        backends=default_registry(),
        run_root=str(out or "runs"),
    )
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


@app.command()
def benchmark(
    dataset: Optional[str] = typer.Option(None, "--dataset"),
    config: Optional[Path] = typer.Option(None, "--config"),
    split: Optional[str] = typer.Option(None, "--split"),
    limit: int = typer.Option(5, "--limit"),
    variants: Optional[list[str]] = typer.Option(None, "--variant", "-v"),
    out: Optional[Path] = typer.Option(None, "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run a benchmark config (compile → execute → grade). Dry-run uses MockLLM."""
    from agentcoop.benchmarks.runner import run_benchmark, _resolve_config
    import asyncio

    cfg_path = _resolve_config(str(config) if config else None, dataset)
    metrics = asyncio.run(
        run_benchmark(
            cfg_path,
            limit=limit,
            variants=list(variants) if variants else None,
            out_dir=str(out) if out else None,
            dry_run=True if dry_run else None,
        )
    )
    typer.echo(json.dumps(metrics, indent=2))


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
        "requirements": {
            "docker_image": f"agentcoop/{name.lower()}",
            "cpus": 4,
            "memory_gb": 16,
            "network": "none",
            "secrets": [],
        },
        "input_contract": {"command": "str", "params": "dict"},
        "output_contract": {"ok": "bool", "answer": "str", "artifacts": "list"},
        "risk": {"code_execution": True, "data_exfiltration": "medium"},
        "cost_class": "medium",
        "risk_level": "medium",
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    typer.echo(f"manifest written to {out}")


@repo_app.command("smoke")
def repo_smoke(
    manifest: Path = typer.Option(..., "--manifest", exists=True, readable=True),
    input_json: Optional[Path] = typer.Option(None, "--input"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Dry-run the sandbox-repo backend against a manifest."""
    manifest_data = yaml.safe_load(Path(manifest).read_text(encoding="utf-8"))
    req = manifest_data.get("requirements", {}) or {}
    payload = {
        "manifest": {
            "image": req.get("docker_image", manifest_data.get("docker_image", "agentcoop/repo")),
            "commit_sha": manifest_data.get("source", {}).get("commit", "HEAD"),
            "digest": manifest_data.get("digest", "sha256:placeholder"),
            "network": req.get("network", "none"),
            "cpus": req.get("cpus", 4),
            "memory_gb": req.get("memory_gb", 16),
            "pids_limit": req.get("pids_limit", 512),
            "non_root": True,
            "secrets": req.get("secrets", []),
        },
        "inputs_dir": "./inputs",
        "outputs_dir": "./outputs",
    }
    if input_json is not None:
        payload["request_path"] = str(input_json)
    from agentcoop.backends.repo_sandbox import build_run_command

    cmd = build_run_command(
        manifest=payload["manifest"],
        inputs_dir=payload["inputs_dir"],
        outputs_dir=payload["outputs_dir"],
        request_path=payload.get("request_path", "/inputs/request.json"),
        result_path="/outputs/result.json",
    )
    summary = {"status": "dry_run", "command": cmd}
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
