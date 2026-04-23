"""GEARS adapter stub (Case Study 2).

Reads `/inputs/request.json`, writes `/outputs/result.json` conforming to
`perturbation_prediction_v1`. Self-contained so the adapter works both
inside a Docker image (no agentcoop install) and when invoked from the
host for smoke tests.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path


SUPPORTED = {"predict_perturbation"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _synth_prediction(params: dict, model: str = "GEARS") -> dict:
    rng = random.Random(int(params.get("seed", 1)))
    dataset = params.get("dataset", "synthetic_norman")
    pert = params.get("perturbation", "P00")
    n_genes = int(params.get("n_genes", 50))
    genes = [f"G{i:03d}" for i in range(n_genes)]
    delta = [rng.gauss(0.0, 0.5) for _ in range(n_genes)]
    return {
        "ok": True,
        "model": model,
        "dataset": dataset,
        "split": params.get("split", "test"),
        "perturbation": pert,
        "genes": genes,
        "predicted_delta": delta,
        "prediction_type": "mean_expression_delta",
        "artifact_schema": "perturbation_prediction_v1",
        "logs_summary": f"framework stub: real {model} not invoked",
        "errors": [],
    }


def run(request: dict) -> dict:
    command = request.get("command")
    if command not in SUPPORTED:
        return {
            "ok": False,
            "model": "GEARS",
            "errors": [f"unsupported command '{command}'; expected {sorted(SUPPORTED)}"],
        }
    try:
        return _synth_prediction(request.get("params", {}) or {}, model="GEARS")
    except Exception as exc:
        return {"ok": False, "model": "GEARS", "errors": [f"{type(exc).__name__}: {exc}"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/inputs/request.json")
    parser.add_argument("--output", default="/outputs/result.json")
    args = parser.parse_args(argv)
    start = time.time()
    result = run(_read(Path(args.input)))
    result["elapsed_s"] = round(time.time() - start, 4)
    _write(Path(args.output), result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
