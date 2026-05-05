#!/usr/bin/env python
"""Evaluate an AFlow-trained MBPP workflow graph on the full MBPP test set.

Usage:
    python scripts/aflow_eval_mbpp.py \
        --round 7 \
        --test-file external/AFlow/data/datasets/mbpp_test.jsonl \
        --out-dir runs/case3/mbpp_full/AFlow-trained

Why this script: AFlow's `run.py` has a `Test` mode (commented out by
default) that sweeps multiple rounds. We just need the single-round
test pass on the full split — saves ~5× the API spend.

Reusable for any AFlow workspace/<DATASET>/workflows/round_<N>/graph.py.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--round", type=int, default=7,
                   help="Round number to evaluate (loads workspace/<DATASET>/workflows/round_<N>/graph.py)")
    p.add_argument("--dataset", default="MBPP", help="AFlow dataset name (default: MBPP)")
    p.add_argument("--test-file", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--exec-model", default="gpt-4o-mini")
    p.add_argument("--aflow-root", type=Path,
                   default=Path("external/AFlow"))
    args = p.parse_args()

    repo_root = Path.cwd()
    aflow_root = (repo_root / args.aflow_root).resolve()
    # Resolve out_dir to absolute BEFORE chdir, otherwise the file write
    # at the end lands inside `external/AFlow/<out_dir>` instead of the
    # caller's intended path.
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(aflow_root))
    os.chdir(aflow_root)  # AFlow's modules use `data/datasets/...` paths

    from scripts.async_llm import LLMsConfig
    from benchmarks.mbpp import MBPPBenchmark

    cfg = LLMsConfig.default()
    exec_cfg = cfg.get(args.exec_model)
    if exec_cfg is None:
        raise SystemExit(f"exec model {args.exec_model!r} not in config2.yaml")

    # Dynamic import: workspace.<DATASET>.workflows.round_<N>.graph.Workflow
    mod_name = f"workspace.{args.dataset}.workflows.round_{args.round}.graph"
    print(f"[load] {mod_name}", flush=True)
    mod = importlib.import_module(mod_name)
    Workflow = mod.Workflow
    workflow = Workflow(name=f"{args.dataset}_round_{args.round}_test",
                        llm_config=exec_cfg, dataset=args.dataset)

    # Resolve test file relative to repo root (we cd'd into aflow_root)
    test_path = args.test_file
    if not test_path.is_absolute():
        test_path = (repo_root / test_path).resolve()

    bench = MBPPBenchmark(
        name=args.dataset,
        file_path=str(test_path),
        log_path=str(args.out_dir),
    )

    t0 = time.time()
    print(f"[eval] running round {args.round} on {test_path} ...", flush=True)
    result = asyncio.run(bench.run_baseline(workflow))
    elapsed = time.time() - t0

    # `run_baseline` returns (avg_score, avg_cost, total_cost) and also
    # writes per-task CSV to log_path.
    avg_score, avg_cost, total_cost = result
    n_tasks = sum(1 for _ in test_path.open())
    summary = {
        "phase": "test",
        "variant": "AFlow-trained",
        "round_used": args.round,
        "exec_model": args.exec_model,
        "n_tasks": n_tasks,
        "score_avg": float(avg_score),
        "ok_rate": float(avg_score),
        "cost_total_usd": round(float(total_cost), 4),
        "avg_cost_per_task_usd": float(avg_cost),
        "elapsed_s": round(elapsed, 1),
    }
    out = args.out_dir / "metrics.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
