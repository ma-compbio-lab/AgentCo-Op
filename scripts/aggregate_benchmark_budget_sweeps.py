#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dynaforge.experiment_runner import (
    aggregate_humaneval_runs,
    aggregate_math_runs,
    aggregate_medqa_runs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate benchmark budget sweep shards and emit summary tables.")
    parser.add_argument("--benchmark", choices=("medqa", "math", "humaneval"), required=True)
    parser.add_argument("--sweeps-root", required=True, help="Root directory containing budget sweep run directories.")
    parser.add_argument("--output-dir", required=True, help="Directory for aggregated outputs.")
    parser.add_argument("--plot", action="store_true", help="Generate Pareto plots if matplotlib is available.")
    return parser.parse_args()


def discover_latest_shards(sweeps_root: Path) -> dict[tuple[str, str, str], list[Path]]:
    latest_by_slug: dict[tuple[str, str, str, str], tuple[str, Path]] = {}
    for task_results_path in sweeps_root.glob("*/*/*/*/*/eval/task_results.json"):
        run_dir = task_results_path.parents[1]
        relative = task_results_path.relative_to(sweeps_root)
        experiment, model, tier, shard_slug, timestamp = relative.parts[:5]
        key = (experiment, model, tier, shard_slug)
        current = latest_by_slug.get(key)
        if current is None or timestamp > current[0]:
            latest_by_slug[key] = (timestamp, run_dir)

    grouped: dict[tuple[str, str, str], list[Path]] = {}
    for (experiment, model, tier, _), (_, run_dir) in sorted(latest_by_slug.items()):
        grouped.setdefault((experiment, model, tier), []).append(run_dir)
    return grouped


def pareto_frontier(rows: list[dict[str, Any]], metric_key: str) -> list[dict[str, Any]]:
    frontier = []
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            better_or_equal_cost = float(other["average_usd"]) <= float(row["average_usd"])
            better_or_equal_metric = float(other[metric_key]) >= float(row[metric_key])
            strictly_better = (
                float(other["average_usd"]) < float(row["average_usd"])
                or float(other[metric_key]) > float(row[metric_key])
            )
            if better_or_equal_cost and better_or_equal_metric and strictly_better:
                dominated = True
                break
        if not dominated:
            frontier.append(row)
    frontier.sort(key=lambda item: (float(item["average_usd"]), -float(item[metric_key])))
    return frontier


def maybe_plot(rows: list[dict[str, Any]], output_dir: Path, metric_key: str) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []

    fig, ax = plt.subplots(figsize=(7, 5))
    for row in rows:
        label = f"{row['model']} / {row['budget_tier']}"
        ax.scatter(float(row["average_usd"]), float(row[metric_key]), label=label)
        ax.annotate(label, (float(row["average_usd"]), float(row[metric_key])), fontsize=8)
    ax.set_xlabel("Average USD per task")
    ax.set_ylabel(metric_key)
    ax.set_title(f"{metric_key} vs Cost")
    ax.grid(True, alpha=0.3)
    path = output_dir / f"pareto_{metric_key}_vs_usd.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path))
    return paths


def main() -> int:
    args = parse_args()
    sweeps_root = Path(args.sweeps_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    aggregate_fn: Callable[..., dict[str, Any]]
    metric_key: str
    if args.benchmark == "medqa":
        aggregate_fn = aggregate_medqa_runs
        metric_key = "accuracy"
    elif args.benchmark == "math":
        aggregate_fn = aggregate_math_runs
        metric_key = "solve_rate"
    else:
        aggregate_fn = aggregate_humaneval_runs
        metric_key = "pass_at_1"

    discovered = discover_latest_shards(sweeps_root)
    if not discovered:
        raise SystemExit(f"No budget sweep shards found under {sweeps_root}")

    rows: list[dict[str, Any]] = []
    aggregate_dirs: list[str] = []
    for (experiment, model, tier), run_dirs in sorted(discovered.items()):
        aggregate_summary = aggregate_fn(
            [str(path) for path in run_dirs],
            base_dir=output_dir / experiment / model / tier,
            run_name="aggregate",
        )
        row = {
            "benchmark": args.benchmark,
            "experiment": experiment,
            "model": model,
            "budget_tier": tier,
            "task_count": aggregate_summary.get("task_count", 0),
            metric_key: aggregate_summary.get(metric_key, aggregate_summary.get("accuracy", 0.0)),
            "average_usd": aggregate_summary.get("average_usd", 0.0),
            "average_input_tokens": aggregate_summary.get("average_input_tokens", 0.0),
            "average_output_tokens": aggregate_summary.get("average_output_tokens", 0.0),
            "average_activated_nodes": aggregate_summary.get("average_activated_nodes", 0.0),
            "average_activated_subgraphs": aggregate_summary.get("average_activated_subgraphs", 0.0),
            "aggregate_run_dir": aggregate_summary.get("run_dir", ""),
        }
        rows.append(row)
        aggregate_dirs.append(str(aggregate_summary.get("run_dir", "")))

    rows.sort(key=lambda item: (item["experiment"], item["model"], item["budget_tier"]))
    frontier = pareto_frontier(rows, metric_key)
    plot_paths = maybe_plot(rows, output_dir, metric_key) if args.plot else []

    json_path = output_dir / f"{args.benchmark}_budget_sweep_summary.json"
    csv_path = output_dir / f"{args.benchmark}_budget_sweep_table.csv"
    md_path = output_dir / f"{args.benchmark}_budget_sweep_summary.md"

    json_path.write_text(
        json.dumps(
            {
                "benchmark": args.benchmark,
                "sweeps_root": str(sweeps_root),
                "aggregate_run_dirs": aggregate_dirs,
                "rows": rows,
                "pareto_frontier": frontier,
                "plots": plot_paths,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# {args.benchmark} budget sweep summary",
        "",
        f"- Sweeps root: `{sweeps_root}`",
        f"- Aggregated configurations: {len(rows)}",
        f"- Plot files: {json.dumps(plot_paths, ensure_ascii=True)}",
        "",
        "## Aggregate table",
    ]
    for row in rows:
        lines.append(
            f"- `{row['experiment']} / {row['model']} / {row['budget_tier']}`: "
            f"{metric_key}={float(row[metric_key]):.4f} average_usd={float(row['average_usd']):.6f} "
            f"average_input_tokens={float(row['average_input_tokens']):.2f}"
        )
    lines.extend(["", "## Pareto frontier"])
    for row in frontier:
        lines.append(
            f"- `{row['experiment']} / {row['model']} / {row['budget_tier']}`: "
            f"{metric_key}={float(row[metric_key]):.4f} average_usd={float(row['average_usd']):.6f}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "summary_json": str(json_path),
                "summary_csv": str(csv_path),
                "summary_md": str(md_path),
                "plots": plot_paths,
                "config_count": len(rows),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
