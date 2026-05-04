"""Case Study 3 — AFlow dynamic topology refinement.

Runs five variants on a fixed MBPP subset and compares:
  1. `AFlow-imported`               — minimal AFlow MBPP graph as imported.
  2. `AFlow+Skills+Tools`           — same graph + skill cards + tool refs.
  3. `AFlow+Skills+Gates`           — same + runtime gates from
     `configs/gates/code_runtime_gates.yaml`.
  4. `AC-Gated`                     — canonical compiled workflow.
  5. `AFlow+MedPrompt-Voting`       — AFlow+Skills+Tools sampled K=3 times
     at temperature 0.7, candidate picked by public-test pass count
     (tie-break: shortest passing code). Demonstrates the parallel
     specialist / self-consistency pattern from experiments.md §3 as a
     case-study variant — does NOT change the canonical AC-Gated workflow.

For each variant, executes N tasks through `run_blueprint` with the live
OpenAI client and grades with the standard pytest-style sandbox.

Usage:
    python scripts/case3_aflow_dynamic.py [--limit 50]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentcoop.backends import default_registry  # noqa: E402
from agentcoop.backends.llm import OpenAIClient  # noqa: E402
from agentcoop.benchmarks import load as load_dataset  # noqa: E402
from agentcoop.benchmarks.graders import grade  # noqa: E402
from agentcoop.core.augment_graph import (  # noqa: E402
    apply_gates,
    attach_skills_and_tools,
    load_gate_yaml,
)
from agentcoop.core.aflow_import import import_aflow_workflow  # noqa: E402
from agentcoop.core.runtime import RuntimeConfig, run_blueprint  # noqa: E402
from agentcoop.core.schema import EdgeSpec, NodeSpec, WorkflowBlueprint  # noqa: E402


def _ensure_formatter_sink(bp: WorkflowBlueprint) -> WorkflowBlueprint:
    """AFlow imported MBPP graph has only a programmer node — append a
    formatter so `_pick_final_result` lands on a JSON-extracted answer."""
    if any(n.role == "formatter" for n in bp.nodes):
        return bp
    if not bp.nodes:
        return bp
    last = bp.nodes[-1]
    formatter = NodeSpec(
        node_id="aflow_formatter",
        role="formatter",
        backend="llm",
        memory_scope="shared_read",
    )
    bp.nodes.append(formatter)
    bp.edges.append(EdgeSpec(source=last.node_id, target=formatter.node_id))
    bp.provenance.append("aflow:appended_formatter_sink")
    return bp


def _make_variants(repo_root: Path, dataset: str, round_num: int = 1) -> dict[str, WorkflowBlueprint]:
    aflow_workflow = (
        repo_root / "external" / "AFlow" / "workspace" / dataset.upper() /
        "workflows" / f"round_{round_num}" / "graph.py"
    )
    imported = import_aflow_workflow(aflow_workflow, dataset=dataset)
    imported = _ensure_formatter_sink(imported)

    skills_tools = copy.deepcopy(imported)
    attach_skills_and_tools(
        skills_tools,
        skills=["code_debugging", "python_testing"],
        tools=["sandbox_python", "generated_tests", "static_analyzer"],
    )

    skills_tools_gates = copy.deepcopy(skills_tools)
    gate_yaml = repo_root / "configs" / "gates" / "code_runtime_gates.yaml"
    apply_gates(skills_tools_gates, load_gate_yaml(gate_yaml))

    return {
        "AFlow-imported": imported,
        "AFlow+Skills+Tools": skills_tools,
        "AFlow+Skills+Gates": skills_tools_gates,
    }


def _add_canonical(repo_root: Path, dataset: str, variants: dict) -> dict:
    canonical_path = repo_root / "workflows" / f"{dataset}.json"
    if canonical_path.exists():
        bp = WorkflowBlueprint.model_validate_json(canonical_path.read_text())
        variants["AC-Gated"] = bp
    return variants


async def _execute(variant_name: str, bp: WorkflowBlueprint, tasks, dataset: str, out_root: Path) -> dict:
    rows = []
    out_dir = out_root / variant_name
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = RuntimeConfig(
        backends=default_registry(llm_client=OpenAIClient(model="gpt-4o-mini")),
        run_root=str(out_dir / "traces"),
    )
    sem = asyncio.Semaphore(6)

    async def _one(t):
        async with sem:
            try:
                bp_copy = bp.model_copy(deep=True)
                # Inject per-call max_tokens so the OpenAI calls have headroom.
                for n in bp_copy.nodes:
                    if n.backend == "llm":
                        n.params.setdefault("max_tokens", 4096)
                        n.params.setdefault("temperature", 0.0)
                outcome = await asyncio.wait_for(
                    run_blueprint(
                        bp_copy,
                        config=cfg,
                        payload={
                            "task": t.prompt,
                            "task_id": t.task_id,
                            "input": t.input,
                            "dataset": t.dataset,
                        },
                    ),
                    timeout=240.0,
                )
                pred_obj = outcome.final.output if outcome.final else {}
                g = grade(dataset, pred_obj, t.reference, t)
                return {
                    "task_id": t.task_id,
                    "ok": bool(g.get("ok")),
                    "score": float(g.get("score", 0.0)),
                    "tokens_in": outcome.state.tokens_used,
                    "cost_usd": outcome.state.cost_used_usd,
                    "issues": list(g.get("issues", []))[:1],
                    "gate_activations": dict(outcome.state.gate_activations),
                }
            except Exception as exc:
                return {
                    "task_id": t.task_id,
                    "ok": False,
                    "score": 0.0,
                    "issues": [f"{type(exc).__name__}: {exc}"],
                    "tokens_in": 0,
                    "cost_usd": 0.0,
                    "gate_activations": {},
                }

    t0 = time.time()
    rows = await asyncio.gather(*[_one(t) for t in tasks])
    elapsed = time.time() - t0
    n = len(rows)
    score_avg = sum(r["score"] for r in rows) / n
    ok_rate = sum(1 for r in rows if r["ok"]) / n
    tok_total = sum(r["tokens_in"] for r in rows)
    cost_total = sum(r["cost_usd"] for r in rows)
    gate_totals: dict[str, int] = {}
    for r in rows:
        for g, c in (r.get("gate_activations") or {}).items():
            gate_totals[g] = gate_totals.get(g, 0) + c

    summary = {
        "variant": variant_name,
        "n": n,
        "score_avg": round(score_avg, 4),
        "ok_rate": round(ok_rate, 4),
        "tokens_total": tok_total,
        "cost_total_usd": round(cost_total, 4),
        "elapsed_s": round(elapsed, 1),
        "gate_totals": gate_totals,
    }
    (out_dir / "predictions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "blueprint.json").write_text(bp.model_dump_json(indent=2), encoding="utf-8")
    return summary


async def _execute_medprompt(
    variant_name: str,
    bp: WorkflowBlueprint,
    tasks,
    dataset: str,
    out_root: Path,
    k_samples: int = 3,
    sample_temperature: float = 0.7,
) -> dict:
    """K-sample self-consistency variant: run `bp` K times per task, pick
    the candidate that passes the most public tests (tie-break: shorter
    code first, then sample order).

    The public tests for MBPP are in `task.input["public_tests"]`. For
    HumanEval we'd need to extract from docstring `>>>` examples — keep
    the case study focused on MBPP for now.
    """
    rows = []
    out_dir = out_root / variant_name
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = RuntimeConfig(
        backends=default_registry(llm_client=OpenAIClient(model="gpt-4o-mini")),
        run_root=str(out_dir / "traces"),
    )
    sem = asyncio.Semaphore(6)

    async def _run_once(t, sample_idx: int):
        bp_copy = bp.model_copy(deep=True)
        for n in bp_copy.nodes:
            if n.backend == "llm":
                n.params.setdefault("max_tokens", 4096)
                # Sample 0 stays at T=0 (the original deterministic call);
                # samples 1..K-1 use the higher temperature for diversity.
                n.params.setdefault(
                    "temperature",
                    0.0 if sample_idx == 0 else sample_temperature,
                )
        outcome = await asyncio.wait_for(
            run_blueprint(
                bp_copy,
                config=cfg,
                payload={
                    "task": t.prompt,
                    "task_id": t.task_id,
                    "input": t.input,
                    "dataset": t.dataset,
                },
            ),
            timeout=240.0,
        )
        pred_obj = outcome.final.output if outcome.final else {}
        code = (
            (pred_obj or {}).get("code")
            or (pred_obj or {}).get("final_answer")
            or ""
        )
        return {
            "code": code,
            "tokens": outcome.state.tokens_used,
            "cost": outcome.state.cost_used_usd,
            "pred_obj": pred_obj,
        }

    def _public_test_pass_count(code: str, tests: list[str]) -> int:
        if not code or not tests:
            return 0
        import subprocess
        import sys as _sys
        passes = 0
        for tline in tests:
            try:
                runnable = code + "\n\n" + tline
                r = subprocess.run(
                    [_sys.executable, "-I", "-c", runnable],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if r.returncode == 0:
                    passes += 1
            except Exception:
                pass
        return passes

    async def _one(t):
        async with sem:
            try:
                samples = await asyncio.gather(*[_run_once(t, i) for i in range(k_samples)])
                tests = list((t.input or {}).get("public_tests") or [])
                # Score each candidate by public-test pass count.
                scored = []
                for idx, s in enumerate(samples):
                    score = _public_test_pass_count(s["code"], tests)
                    scored.append((score, -len(s["code"] or ""), idx, s))
                scored.sort(reverse=True)
                winner = scored[0][3]
                g = grade(dataset, winner["pred_obj"], t.reference, t)
                return {
                    "task_id": t.task_id,
                    "ok": bool(g.get("ok")),
                    "score": float(g.get("score", 0.0)),
                    "tokens_in": sum(s["tokens"] for s in samples),
                    "cost_usd": sum(s["cost"] for s in samples),
                    "issues": list(g.get("issues", []))[:1],
                    "gate_activations": {},
                    "n_samples": k_samples,
                    "winner_test_passes": scored[0][0],
                }
            except Exception as exc:
                return {
                    "task_id": t.task_id,
                    "ok": False,
                    "score": 0.0,
                    "issues": [f"{type(exc).__name__}: {exc}"],
                    "tokens_in": 0,
                    "cost_usd": 0.0,
                    "gate_activations": {},
                }

    t0 = time.time()
    rows = await asyncio.gather(*[_one(t) for t in tasks])
    elapsed = time.time() - t0
    n = len(rows)
    summary = {
        "variant": variant_name,
        "n": n,
        "score_avg": round(sum(r["score"] for r in rows) / n, 4),
        "ok_rate": round(sum(1 for r in rows if r["ok"]) / n, 4),
        "tokens_total": sum(r["tokens_in"] for r in rows),
        "cost_total_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "elapsed_s": round(elapsed, 1),
        "gate_totals": {},
        "k_samples": k_samples,
        "sample_temperature": sample_temperature,
    }
    (out_dir / "predictions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "blueprint.json").write_text(bp.model_dump_json(indent=2), encoding="utf-8")
    return summary


async def amain(
    dataset: str,
    limit: int,
    out_root: Path,
    *,
    aflow_round: int = 1,
    keep_variants: list[str] | None = None,
) -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    tasks = load_dataset(dataset, split="test", limit=limit, aflow=True)
    variants = _make_variants(repo_root, dataset, round_num=aflow_round)
    variants = _add_canonical(repo_root, dataset, variants)
    if keep_variants:
        wanted = set(keep_variants)
        variants = {k: v for k, v in variants.items() if k in wanted}

    results = []
    for name, bp in variants.items():
        print(f"[case3] running variant: {name} (n={len(tasks)}, aflow_round={aflow_round})")
        results.append(await _execute(name, bp, tasks, dataset, out_root))
        print(f"  -> {results[-1]}")

    # Self-consistency variant — uses the AFlow+Skills+Tools graph as base.
    if (keep_variants is None or "AFlow+MedPrompt-Voting" in keep_variants):
        medprompt_base = variants.get("AFlow+Skills+Tools")
        if medprompt_base is not None and dataset == "mbpp":
            name = "AFlow+MedPrompt-Voting"
            print(f"[case3] running variant: {name} (n={len(tasks)}, k=3, T=0.7)")
            results.append(
                await _execute_medprompt(name, medprompt_base, tasks, dataset, out_root)
            )
            print(f"  -> {results[-1]}")

    final = {
        "dataset": dataset,
        "n_per_variant": len(tasks),
        "aflow_round": aflow_round,
        "variants": results,
    }
    (out_root / "summary.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    return final


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="mbpp")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", default="runs/case3/mbpp")
    p.add_argument("--aflow-round", type=int, default=1,
                   help="AFlow workspace/<DS>/workflows/round_<N>/graph.py to import")
    p.add_argument("--variants", default="",
                   help="Comma-separated subset of variant names to run "
                        "(default: all). Names: AFlow-imported, AFlow+Skills+Tools, "
                        "AFlow+Skills+Gates, AC-Gated, AFlow+MedPrompt-Voting")
    args = p.parse_args(argv)
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    keep = [v.strip() for v in args.variants.split(",") if v.strip()] or None
    summary = asyncio.run(amain(
        args.dataset, args.limit, out_root,
        aflow_round=args.aflow_round, keep_variants=keep,
    ))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
