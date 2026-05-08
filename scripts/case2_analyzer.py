"""Case Study 2 BiologicalPatternAnalyzer + BenchmarkReportReviewer.

Reads: per-model metrics + the three ensemble outputs.
Calls OpenAI to produce the structured benchmark report described in
docs/experiments/case_study.md §3.7 (compare against simple baselines, identify
dataset-specific failure modes, ensemble decision).

Writes: `final_report.md` + `final_report.json`.

Usage:
    python scripts/case2_analyzer.py runs/case2/synth
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentcoop.backends.llm import OpenAIClient  # noqa: E402


SYSTEM_PROMPT = (
    "You are the benchmark integrator for a single-cell perturbation case "
    "study. You receive per-model metrics on a synthetic Norman-like dataset "
    "(perturbation-level split) plus three ensemble results "
    "(validation_winner / rank_fusion / weighted_average).\n\n"
    "Rules (from docs/experiments/case_study.md §3.7):\n"
    " - Do not claim a model is better unless the metric table supports it.\n"
    " - Always compare against simple baselines.\n"
    " - Identify dataset-specific failure modes, systematic-variation risks, "
    "and split-specific behavior.\n\n"
    "Return a JSON object:\n"
    "{\"summary\": str, \"per_model_findings\": [{\"model\": str, "
    "\"strengths\": [str], \"weaknesses\": [str]}], "
    "\"per_dataset_findings\": str, \"ensemble_decision\": "
    "{\"strategy\": str, \"justification\": str}, "
    "\"failure_modes\": [str], \"recommended_next_experiments\": [str], "
    "\"caveats\": [str], \"rationale_summary\": str}."
)


def _load_metrics(case_dir: Path) -> dict:
    return json.loads((case_dir / "metrics.json").read_text())


def _load_ensembles(case_dir: Path) -> dict:
    out = {}
    for name in ("validation", "rank", "weighted"):
        p = case_dir / f"ensemble_{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    return out


def _summarize_metrics(metrics: dict) -> dict:
    averages = metrics.get("averages", {}) or {}
    table = []
    for model, m in averages.items():
        table.append(
            {
                "model": model,
                "n": m.get("n"),
                "pearson_delta": round(m.get("pearson_delta", 0.0), 4),
                "pearson_delta_top20": round(m.get("pearson_delta_top20", 0.0), 4),
                "cosine": round(m.get("cosine", 0.0), 4),
                "rmse": round(m.get("rmse", 0.0), 4),
                "precision_at_k": round(m.get("precision_at_k", 0.0), 4),
            }
        )
    table.sort(key=lambda x: -x["pearson_delta_top20"])
    return {"models": table}


def _summarize_ensembles(ensembles: dict) -> dict:
    out = {}
    for name, payload in ensembles.items():
        if name == "validation":
            out[name] = {"choice": payload.get("validation_winner"), "metrics": payload.get("scores", {})}
        elif name == "rank":
            top = list(payload.items())[:10] if isinstance(payload, dict) else None
            out[name] = {"top_genes": top}
        elif name == "weighted":
            out[name] = {"shape": "weighted average vector", "len": len(payload) if isinstance(payload, list) else None}
    return out


async def analyze(case_dir: Path) -> dict:
    metrics = _load_metrics(case_dir)
    ensembles = _load_ensembles(case_dir)
    payload = {
        "dataset": "synthetic_norman (CS2 dry-run, 50 genes × 12 perturbations, seed=42)",
        "note": "Real Norman / Replogle K562 require pertpy / figshare access + GPUs.",
        "metrics": _summarize_metrics(metrics),
        "ensembles": _summarize_ensembles(ensembles),
        "available_baselines": [
            "perturbed_mean", "matching_mean", "crispr_informed_mean", "ridge",
        ],
    }

    client = OpenAIClient(model="gpt-4o-mini")
    raw = await client.complete(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, indent=2),
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=4096,
    )
    await client.aclose()
    text = raw.get("text", "") or "{}"
    try:
        report = json.loads(text)
    except Exception:
        report = {"summary": "(parse failed)", "raw": text}
    report["_tokens_in"] = raw.get("tokens_in", 0)
    report["_tokens_out"] = raw.get("tokens_out", 0)
    return report


def _render_md(report: dict, metrics_summary: dict, case_dir: Path) -> str:
    lines: list[str] = []
    lines.append("# Case Study 2 — Benchmark Report")
    lines.append("")
    lines.append("**Dataset:** synthetic Norman-like (CS2 dry-run; 50 genes × 12 perturbations, seed 42).")
    lines.append("")
    lines.append("## Per-model metrics (sorted by pearson_delta_top20)")
    lines.append("")
    lines.append("| Model | n | pearson_delta | pearson_delta_top20 | cosine | rmse | precision@k |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in metrics_summary["models"]:
        lines.append(
            f"| {row['model']} | {row['n']} | {row['pearson_delta']} | "
            f"{row['pearson_delta_top20']} | {row['cosine']} | {row['rmse']} | "
            f"{row['precision_at_k']} |"
        )
    lines.append("")
    lines.append("## Summary")
    lines.append(str(report.get("summary", "")))
    lines.append("")
    lines.append("## Per-model findings")
    for f in report.get("per_model_findings", []) or []:
        lines.append(f"### {f.get('model','?')}")
        if f.get("strengths"):
            lines.append("**Strengths:** " + "; ".join(f["strengths"]))
        if f.get("weaknesses"):
            lines.append("**Weaknesses:** " + "; ".join(f["weaknesses"]))
        lines.append("")
    lines.append("## Per-dataset findings")
    lines.append(str(report.get("per_dataset_findings", "")))
    lines.append("")
    lines.append("## Ensemble decision")
    ed = report.get("ensemble_decision") or {}
    lines.append(f"**Strategy:** {ed.get('strategy','?')}\n\n{ed.get('justification','')}")
    lines.append("")
    if report.get("failure_modes"):
        lines.append("## Failure modes")
        for f in report["failure_modes"]:
            lines.append(f"- {f}")
        lines.append("")
    if report.get("recommended_next_experiments"):
        lines.append("## Recommended next experiments")
        for r in report["recommended_next_experiments"]:
            lines.append(f"- {r}")
        lines.append("")
    if report.get("caveats"):
        lines.append("## Caveats")
        for c in report["caveats"]:
            lines.append(f"- {c}")
        lines.append("")
    lines.append("## Token usage")
    lines.append(f"- input tokens: {report.get('_tokens_in', 0)}")
    lines.append(f"- output tokens: {report.get('_tokens_out', 0)}")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append(f"- dataset: `{case_dir / 'dataset.json'}`")
    lines.append(f"- predictions: `{case_dir / 'predictions'}/`")
    lines.append(f"- metrics: `{case_dir / 'metrics.json'}`")
    lines.append(f"- ensembles: `{case_dir}/ensemble_validation.json`, `ensemble_rank.json`, `ensemble_weighted.json`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    case_dir = Path(argv[0]) if argv else Path("runs/case2/synth")
    if not (case_dir / "metrics.json").exists():
        print(f"error: missing {case_dir / 'metrics.json'} — run perturb evaluate first", file=sys.stderr)
        return 2
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    metrics = _load_metrics(case_dir)
    metrics_summary = _summarize_metrics(metrics)
    report = asyncio.run(analyze(case_dir))
    (case_dir / "final_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = _render_md(report, metrics_summary, case_dir)
    (case_dir / "final_report.md").write_text(md, encoding="utf-8")
    print(json.dumps({
        "out_md": str(case_dir / "final_report.md"),
        "out_json": str(case_dir / "final_report.json"),
        "tokens_in": report.get("_tokens_in", 0),
        "tokens_out": report.get("_tokens_out", 0),
        "n_models": len(metrics_summary["models"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
