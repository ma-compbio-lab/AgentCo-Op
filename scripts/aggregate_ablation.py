"""Aggregate the AgentCo-Op ablation runs into the four tables
required by `docs/experiments/ablation.md` §6.

Inputs (paths are conventional and overridable via CLI flags):
- AC-Full: reuses the Sessions-4–6 AC-Gated full-split runs
  (`runs/full_v6/<dataset>/` for 5 datasets; `runs/full/math/` for
  MATH which was carried over from Session 5).
- AC-NoGate / AC-NoSkillsTools / AC-Minimal: produced by
  `agentcoop run-benchmark -v <variant> --out runs/ablations/<dataset>/<variant>`.

Outputs (written to `runs/ablations/`):
- `ablation_results.csv`  — main 6 × 4 score table.
- `component_effects.csv` — marginal-effect + interaction table.
- `cost_latency.csv`      — per-(dataset, variant) cost / latency / gates.
- `gate_rescue.csv`       — per-dataset gate trigger / rescue / harm.
- `summary.json`          — every cell as a structured object.

Pure-Python script: no AgentCo-Op core code is imported beyond
benchmark-config metadata; safe to run in any env with pandas.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


DATASETS = ["hotpotqa", "drop", "humaneval", "mbpp", "gsm8k", "math"]
VARIANTS = ["AC-Full", "AC-NoGate", "AC-NoSkillsTools", "AC-Minimal"]


def _load_metrics(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_variant_block(metrics: dict[str, Any], variant: str) -> dict[str, Any]:
    by = metrics.get("by_variant") or {}
    # The AC-Full row reuses the AC-Gated metrics; both keys map to the same block.
    return by.get(variant) or by.get("AC-Gated") or {}


def _path_full(dataset: str) -> Path:
    if dataset == "math":
        return Path("runs/full/math/metrics.json")
    return Path(f"runs/full_v6/{dataset}/metrics.json")


def _path_ablation(dataset: str, variant: str) -> Path:
    return Path(f"runs/ablations/{dataset}/{variant}/metrics.json")


def _predictions_path(dataset: str, variant: str) -> Path:
    if variant == "AC-Full":
        if dataset == "math":
            return Path("runs/full/math/predictions.jsonl")
        return Path(f"runs/full_v6/{dataset}/predictions.jsonl")
    return Path(f"runs/ablations/{dataset}/{variant}/predictions.jsonl")


def collect_main(out_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ds in DATASETS:
        row = {"dataset": ds}
        for v in VARIANTS:
            mpath = _path_full(ds) if v == "AC-Full" else _path_ablation(ds, v)
            metrics = _load_metrics(mpath)
            if metrics is None:
                row[v] = None
                row[f"{v}__source"] = f"missing: {mpath}"
                continue
            block = _pick_variant_block(metrics, v)
            row[v] = float(block.get("score_avg")) if block.get("score_avg") is not None else None
            row[f"{v}__n"] = block.get("n_tasks")
            row[f"{v}__source"] = str(mpath)
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "ablation_results.csv", index=False)
    return df


def collect_cost_latency(out_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ds in DATASETS:
        for v in VARIANTS:
            mpath = _path_full(ds) if v == "AC-Full" else _path_ablation(ds, v)
            metrics = _load_metrics(mpath)
            if metrics is None:
                continue
            block = _pick_variant_block(metrics, v)
            rows.append({
                "dataset": ds,
                "variant": v,
                "score": block.get("score_avg"),
                "n_tasks": block.get("n_tasks"),
                "tokens_total": block.get("tokens_total"),
                "cost_total_usd": block.get("cost_total_usd"),
                "latency_avg_s": block.get("latency_avg_s"),
                "gate_totals": json.dumps(block.get("gate_totals") or {}),
                "source": str(mpath),
            })
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "cost_latency.csv", index=False)
    return df


def compute_component_effects(main: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Compute the §6.2 marginal-effect table.

    skills_tools_with_gates    = AC-Full           - AC-NoSkillsTools
    skills_tools_without_gates = AC-NoGate         - AC-Minimal
    gate_with_skills_tools     = AC-Full           - AC-NoGate
    gate_without_skills_tools  = AC-NoSkillsTools  - AC-Minimal
    interaction                = AC-Full - AC-NoGate - AC-NoSkillsTools + AC-Minimal
    """
    rows: list[dict[str, Any]] = []
    for _, r in main.iterrows():
        f, ng, nst, mn = r.get("AC-Full"), r.get("AC-NoGate"), r.get("AC-NoSkillsTools"), r.get("AC-Minimal")
        def sub(a, b):
            return None if (a is None or b is None) else round(a - b, 4)
        interaction = (
            None if any(x is None for x in (f, ng, nst, mn))
            else round(f - ng - nst + mn, 4)
        )
        rows.append({
            "dataset": r["dataset"],
            "skills_tools_with_gates":    sub(f,  nst),
            "skills_tools_without_gates": sub(ng, mn),
            "gate_with_skills_tools":     sub(f,  ng),
            "gate_without_skills_tools":  sub(nst, mn),
            "interaction": interaction,
        })
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "component_effects.csv", index=False)
    return df


def compute_gate_rescue(out_dir: Path) -> pd.DataFrame:
    """Per-dataset rescue / harm analysis comparing AC-Full vs AC-NoGate
    on the same task IDs.

    - rescue: AC-NoGate WRONG, AC-Full CORRECT.
    - harm:   AC-NoGate CORRECT, AC-Full WRONG.
    Trigger count is read from AC-Full's gate_totals (sum of all gate
    activations over the dataset).
    """
    rows: list[dict[str, Any]] = []
    for ds in DATASETS:
        full_path = _predictions_path(ds, "AC-Full")
        ng_path = _predictions_path(ds, "AC-NoGate")
        if not (full_path.is_file() and ng_path.is_file()):
            rows.append({
                "dataset": ds,
                "gate_trigger_count": None,
                "rescue_count": None,
                "harm_count": None,
                "rescue_rate": None,
                "harm_rate": None,
                "avg_cost_added_usd": None,
                "note": f"missing predictions: {full_path} or {ng_path}",
            })
            continue
        full_preds = {p["task_id"]: p for p in (json.loads(l) for l in full_path.open())}
        ng_preds = {p["task_id"]: p for p in (json.loads(l) for l in ng_path.open())}
        common = set(full_preds) & set(ng_preds)

        def correct(p: dict[str, Any]) -> bool:
            return float(p.get("score") or 0) >= 0.5

        rescue = sum(1 for tid in common if not correct(ng_preds[tid]) and correct(full_preds[tid]))
        harm = sum(1 for tid in common if correct(ng_preds[tid]) and not correct(full_preds[tid]))

        # Trigger count: sum of all gate activations recorded by AC-Full.
        full_metrics = _load_metrics(_path_full(ds)) or {}
        block = _pick_variant_block(full_metrics, "AC-Full")
        gate_totals = block.get("gate_totals") or {}
        triggers = int(sum(gate_totals.values())) if gate_totals else 0
        if triggers == 0:
            # Fall back to per-prediction gate_activations if the aggregate is missing.
            triggers = sum(
                sum((p.get("gate_activations") or {}).values())
                for p in full_preds.values()
            )

        # Average added cost = (AC-Full cost - AC-NoGate cost) / n_tasks.
        ng_metrics = _load_metrics(_path_ablation(ds, "AC-NoGate")) or {}
        ng_block = _pick_variant_block(ng_metrics, "AC-NoGate")
        full_cost = block.get("cost_total_usd") or 0.0
        ng_cost = ng_block.get("cost_total_usd") or 0.0
        n = block.get("n_tasks") or len(common) or 1
        avg_cost_added = round((full_cost - ng_cost) / max(1, n), 6)

        rows.append({
            "dataset": ds,
            "gate_trigger_count": triggers,
            "rescue_count": rescue,
            "harm_count": harm,
            "rescue_rate": round(rescue / triggers, 4) if triggers else None,
            "harm_rate":   round(harm   / triggers, 4) if triggers else None,
            "avg_cost_added_usd": avg_cost_added,
            "n_common_tasks": len(common),
        })
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "gate_rescue.csv", index=False)
    return df


def write_summary_json(
    main: pd.DataFrame,
    cost_latency: pd.DataFrame,
    component: pd.DataFrame,
    rescue: pd.DataFrame,
    out_dir: Path,
) -> None:
    summary = {
        "ablation_main_table": main.to_dict(orient="records"),
        "cost_latency_table": cost_latency.to_dict(orient="records"),
        "component_effects": component.to_dict(orient="records"),
        "gate_rescue": rescue.to_dict(orient="records"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="runs/ablations")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    main_df = collect_main(out_dir)
    cost_df = collect_cost_latency(out_dir)
    comp_df = compute_component_effects(main_df, out_dir)
    rescue_df = compute_gate_rescue(out_dir)
    write_summary_json(main_df, cost_df, comp_df, rescue_df, out_dir)

    print("=== ablation_results ===")
    print(main_df.to_string(index=False))
    print()
    print("=== component_effects ===")
    print(comp_df.to_string(index=False))
    print()
    print("=== gate_rescue ===")
    print(rescue_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
