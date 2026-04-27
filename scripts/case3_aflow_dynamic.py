"""Case Study 3 — AFlow dynamic topology refinement.

Runs three variants on a fixed MBPP subset and compares:
  1. `AFlow-imported`           — minimal AFlow MBPP graph as imported.
  2. `AFlow-imported+SkillsTools` — same graph augmented with skills + tools.
  3. `AFlow-imported+Skills+Gates` — same plus runtime gates from
     `configs/gates/code_runtime_gates.yaml`.
  4. `AC-Gated` baseline copied from the canonical workflow blueprint.

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


def _make_variants(repo_root: Path, dataset: str) -> dict[str, WorkflowBlueprint]:
    aflow_workflow = (
        repo_root / "external" / "AFlow" / "workspace" / dataset.upper() /
        "workflows" / "round_1" / "graph.py"
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


async def amain(dataset: str, limit: int, out_root: Path) -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    tasks = load_dataset(dataset, split="test", limit=limit, aflow=True)
    variants = _make_variants(repo_root, dataset)
    variants = _add_canonical(repo_root, dataset, variants)

    results = []
    for name, bp in variants.items():
        print(f"[case3] running variant: {name} (n={len(tasks)})")
        results.append(await _execute(name, bp, tasks, dataset, out_root))
        print(f"  -> {results[-1]}")

    final = {"dataset": dataset, "n_per_variant": len(tasks), "variants": results}
    (out_root / "summary.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    return final


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="mbpp")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", default="runs/case3/mbpp")
    args = p.parse_args(argv)
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(amain(args.dataset, args.limit, out_root))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
