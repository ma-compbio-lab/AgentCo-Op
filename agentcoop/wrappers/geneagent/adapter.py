"""GeneAgent adapter stub (Case Study 1).

Contract (per case_study.md §2): read `--input /inputs/request.json` with
{command, params:{gene_symbols, context, organism, direction}}; write
`--output /outputs/result.json` per the `geneagent_report_v1` schema.

This stub returns a canned analysis so downstream code can be developed
without cloning GeneAgent. Swap `run()` for the real repo call when
`external/GeneAgent` is pinned.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


REQUIRED_COMMANDS = {"analyze_gene_set"}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def run(request: dict) -> dict:
    command = request.get("command")
    params = request.get("params", {}) or {}
    if command not in REQUIRED_COMMANDS:
        return {
            "ok": False,
            "errors": [
                f"unsupported command '{command}'; expected one of {sorted(REQUIRED_COMMANDS)}"
            ],
            "claims": [],
            "supported_claims": [],
            "unsupported_claims": [],
            "functional_labels": [],
            "logs_summary": "",
        }

    genes = list(params.get("gene_symbols") or [])
    direction = params.get("direction", "up")
    # Synthetic canned analysis — aligns with the airway dex dataset.
    functional_labels = []
    if direction == "up":
        functional_labels = [
            "glucocorticoid response",
            "circadian rhythm",
            "metabolic regulation",
        ]
    elif direction == "down":
        functional_labels = ["inflammatory response", "cytokine signalling"]

    claims = [
        {
            "statement": f"The {direction}-regulated gene list contains markers of {label}.",
            "evidence_refs": ["MSigDB:HALLMARK_stub", "GO:BP_stub"],
        }
        for label in functional_labels
    ]
    # Treat 1/4 of stub claims as "unsupported" so gate tests have a signal.
    supported = claims[:max(1, len(claims) - 1)]
    unsupported = claims[len(supported):]

    return {
        "ok": True,
        "artifact_schema": "geneagent_report_v1",
        "genes_analyzed": genes[:200],
        "functional_labels": functional_labels,
        "claims": claims,
        "supported_claims": supported,
        "unsupported_claims": unsupported,
        "context": params.get("context", ""),
        "logs_summary": "framework stub: real GeneAgent not invoked",
        "errors": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/inputs/request.json")
    parser.add_argument("--output", default="/outputs/result.json")
    args = parser.parse_args(argv)

    start = time.time()
    request = _read(Path(args.input))
    try:
        result = run(request)
    except Exception as exc:
        result = {
            "ok": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "claims": [],
            "functional_labels": [],
            "logs_summary": "",
        }
    result["elapsed_s"] = round(time.time() - start, 4)
    _write(Path(args.output), result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
