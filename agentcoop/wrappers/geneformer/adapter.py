"""Geneformer adapter stub (Case Study 2 optional)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(request: dict) -> dict:
    params = request.get("params", {}) or {}
    command = request.get("command", "embed_cells")
    if command == "embed_cells":
        return {
            "ok": True,
            "model": "Geneformer",
            "command": command,
            "dataset": params.get("dataset", "synthetic_norman"),
            "embedding_shape": [100, 768],
            "artifact_schema": "perturbation_prediction_v1",
            "logs_summary": "framework stub: real Geneformer embeddings not computed",
        }
    return {
        "ok": False,
        "model": "Geneformer",
        "errors": [f"unsupported command '{command}'"],
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
