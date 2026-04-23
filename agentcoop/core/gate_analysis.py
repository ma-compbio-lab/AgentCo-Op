"""Aggregate gate trigger / rescue / harm metrics from benchmark run dirs.

Case Study 3 requires a quantitative picture of when dynamic refinement
helps and when it hurts. Given a collection of run directories containing
`predictions.jsonl`, this module summarizes per-gate trigger counts and
rescue/harm rates computed against a baseline (no-gate) variant.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def _load_predictions(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "predictions.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def analyze_runs(
    run_dirs: Iterable[Path],
    *,
    base_variant: str = "AC-Compiled",
    gated_variant: str = "AC-Gated",
) -> dict[str, Any]:
    """Compare `gated_variant` vs `base_variant` across shared task_ids."""
    base: dict[str, dict[str, Any]] = {}
    gated: dict[str, dict[str, Any]] = {}
    gate_totals: dict[str, int] = defaultdict(int)

    for run in run_dirs:
        for row in _load_predictions(Path(run)):
            if row.get("variant") == base_variant:
                base[row["task_id"]] = row
            elif row.get("variant") == gated_variant:
                gated[row["task_id"]] = row
                for name, n in (row.get("gate_activations") or {}).items():
                    gate_totals[name] += n

    shared = set(base) & set(gated)
    rescues = 0
    harms = 0
    no_change = 0
    for tid in shared:
        b = base[tid]
        g = gated[tid]
        if not b.get("ok") and g.get("ok"):
            rescues += 1
        elif b.get("ok") and not g.get("ok"):
            harms += 1
        else:
            no_change += 1

    return {
        "base_variant": base_variant,
        "gated_variant": gated_variant,
        "n_shared_tasks": len(shared),
        "rescue_count": rescues,
        "harm_count": harms,
        "no_change": no_change,
        "rescue_rate": rescues / len(shared) if shared else 0.0,
        "harm_rate": harms / len(shared) if shared else 0.0,
        "gate_trigger_totals": dict(gate_totals),
    }


__all__ = ["analyze_runs"]
