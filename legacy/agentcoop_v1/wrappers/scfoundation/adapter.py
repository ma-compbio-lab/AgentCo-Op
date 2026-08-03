"""scFoundation adapter stub (Case Study 2). Self-contained."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(request: dict) -> dict:
    params = request.get("params", {}) or {}
    rng = random.Random(int(params.get("seed", 3)))
    n_genes = int(params.get("n_genes", 50))
    genes = [f"G{i:03d}" for i in range(n_genes)]
    return {
        "ok": True,
        "model": "scFoundation",
        "dataset": params.get("dataset", "synthetic_norman"),
        "split": params.get("split", "test"),
        "perturbation": params.get("perturbation", "P00"),
        "genes": genes,
        "predicted_delta": [rng.gauss(0.0, 0.45) for _ in range(n_genes)],
        "prediction_type": "mean_expression_delta",
        "artifact_schema": "perturbation_prediction_v1",
        "logs_summary": "framework stub: real scFoundation not invoked",
    }


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
