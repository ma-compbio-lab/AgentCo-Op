"""Download the six AFlow-aligned benchmark datasets into data/raw/.

Uses HuggingFace `datasets` for everything except HumanEval, which is
read from the locally cloned `external/human-eval`. Files are written as
JSONL for downstream `BenchmarkEvaluator`s; a SHA-256 of each output is
stored in `data/data_hashes.json`.

Idempotent: re-running with already-downloaded files skips work.

Usage:
    python scripts/download_datasets.py
    python scripts/download_datasets.py --datasets gsm8k math
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
HASHES_PATH = REPO_ROOT / "data" / "data_hashes.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_existing_hashes() -> dict[str, Any]:
    if HASHES_PATH.exists():
        return json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    return {}


def _save_hashes(hashes: dict[str, Any]) -> None:
    HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    HASHES_PATH.write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterator[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _already_done(path: Path, hashes: dict[str, Any], name: str) -> bool:
    if not path.exists():
        return False
    rel = str(path.relative_to(REPO_ROOT))
    if hashes.get(name, {}).get("path") == rel:
        return True
    return False


# ---------------------------------------------------------------------------
# Per-dataset downloaders. Each returns a list of (name, Path, count).
# ---------------------------------------------------------------------------


def _download_gsm8k(force: bool) -> list[tuple[str, Path, int]]:
    from datasets import load_dataset

    results: list[tuple[str, Path, int]] = []
    ds = load_dataset("gsm8k", "main")
    for split_name, split in ds.items():
        out = RAW_DIR / "gsm8k" / f"{split_name}.jsonl"
        if out.exists() and not force:
            results.append(("gsm8k." + split_name, out, sum(1 for _ in out.open())))
            continue
        n = _write_jsonl(
            out,
            (
                {
                    "task_id": f"gsm8k_{split_name}_{i}",
                    "question": ex["question"],
                    "answer": ex["answer"],
                }
                for i, ex in enumerate(split)
            ),
        )
        results.append(("gsm8k." + split_name, out, n))
    return results


def _download_math(force: bool) -> list[tuple[str, Path, int]]:
    """MATH via EleutherAI/hendrycks_math (per-subject configs).

    We concatenate all 7 subjects so downstream AFlow-aligned filtering can
    subset to level-5 across Counting & Probability / Number Theory /
    Prealgebra / Precalculus (expected total = 617).
    """
    from datasets import load_dataset

    subjects = {
        "algebra": "Algebra",
        "counting_and_probability": "Counting & Probability",
        "geometry": "Geometry",
        "intermediate_algebra": "Intermediate Algebra",
        "number_theory": "Number Theory",
        "prealgebra": "Prealgebra",
        "precalculus": "Precalculus",
    }

    results: list[tuple[str, Path, int]] = []
    for split_name in ("train", "test"):
        out = RAW_DIR / "math" / f"{split_name}.jsonl"
        if out.exists() and not force:
            results.append(("math." + split_name, out, sum(1 for _ in out.open())))
            continue
        rows: list[dict[str, Any]] = []
        for cfg_key, cfg_label in subjects.items():
            try:
                ds = load_dataset("EleutherAI/hendrycks_math", cfg_key, split=split_name)
            except Exception as exc:
                print(f"  [math] skipping {cfg_key}/{split_name}: {exc}")
                continue
            for i, ex in enumerate(ds):
                rows.append(
                    {
                        "task_id": f"math_{split_name}_{cfg_key}_{i}",
                        "problem": ex.get("problem", ""),
                        "level": ex.get("level", ""),
                        "type": ex.get("type", cfg_label),
                        "solution": ex.get("solution", ""),
                    }
                )
        n = _write_jsonl(out, iter(rows))
        results.append(("math." + split_name, out, n))
    return results


def _download_mbpp(force: bool) -> list[tuple[str, Path, int]]:
    from datasets import load_dataset

    results: list[tuple[str, Path, int]] = []
    ds = load_dataset("mbpp", "sanitized")
    for split_name, split in ds.items():
        out = RAW_DIR / "mbpp" / f"{split_name}.jsonl"
        if out.exists() and not force:
            results.append(("mbpp." + split_name, out, sum(1 for _ in out.open())))
            continue
        n = _write_jsonl(
            out,
            (
                {
                    "task_id": f"mbpp_{split_name}_{ex.get('task_id', i)}",
                    "prompt": ex.get("prompt", ex.get("text", "")),
                    "code": ex.get("code", ""),
                    "test_list": ex.get("test_list", []),
                    "test_setup_code": ex.get("test_setup_code", ""),
                }
                for i, ex in enumerate(split)
            ),
        )
        results.append(("mbpp." + split_name, out, n))
    return results


def _download_hotpotqa(force: bool) -> list[tuple[str, Path, int]]:
    from datasets import load_dataset

    results: list[tuple[str, Path, int]] = []
    ds = load_dataset("hotpot_qa", "distractor", trust_remote_code=True)
    for split_name, split in ds.items():
        out = RAW_DIR / "hotpotqa" / f"{split_name}.jsonl"
        if out.exists() and not force:
            results.append(("hotpotqa." + split_name, out, sum(1 for _ in out.open())))
            continue

        def rows() -> Iterator[dict[str, Any]]:
            for i, ex in enumerate(split):
                yield {
                    "task_id": f"hotpotqa_{split_name}_{ex.get('id', i)}",
                    "question": ex.get("question", ""),
                    "answer": ex.get("answer", ""),
                    "context": ex.get("context", {}),
                    "supporting_facts": ex.get("supporting_facts", {}),
                    "level": ex.get("level", ""),
                    "type": ex.get("type", ""),
                }

        n = _write_jsonl(out, rows())
        results.append(("hotpotqa." + split_name, out, n))
    return results


def _download_drop(force: bool) -> list[tuple[str, Path, int]]:
    from datasets import load_dataset

    results: list[tuple[str, Path, int]] = []
    try:
        ds = load_dataset("drop")
    except Exception:
        ds = load_dataset("ucinlp/drop")
    for split_name, split in ds.items():
        out = RAW_DIR / "drop" / f"{split_name}.jsonl"
        if out.exists() and not force:
            results.append(("drop." + split_name, out, sum(1 for _ in out.open())))
            continue

        def rows() -> Iterator[dict[str, Any]]:
            for i, ex in enumerate(split):
                yield {
                    "task_id": f"drop_{split_name}_{ex.get('query_id', i)}",
                    "passage": ex.get("passage", ""),
                    "question": ex.get("question", ""),
                    "answers_spans": ex.get("answers_spans", {}),
                    "section_id": ex.get("section_id", ""),
                }

        n = _write_jsonl(out, rows())
        results.append(("drop." + split_name, out, n))
    return results


def _download_humaneval(force: bool) -> list[tuple[str, Path, int]]:
    """Pull HumanEval from the locally cloned `external/human-eval`."""
    source = REPO_ROOT / "external" / "human-eval" / "data" / "HumanEval.jsonl.gz"
    if not source.exists():
        print(f"ERROR: {source} missing; clone external/human-eval first.", file=sys.stderr)
        return []
    out = RAW_DIR / "humaneval" / "HumanEval.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not force:
        return [("humaneval.test", out, sum(1 for _ in out.open()))]
    count = 0
    with gzip.open(source, "rt", encoding="utf-8") as src, out.open("w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            dst.write(line + "\n")
            count += 1
    return [("humaneval.test", out, count)]


DOWNLOADERS: dict[str, Callable[[bool], list[tuple[str, Path, int]]]] = {
    "gsm8k": _download_gsm8k,
    "math": _download_math,
    "mbpp": _download_mbpp,
    "hotpotqa": _download_hotpotqa,
    "drop": _download_drop,
    "humaneval": _download_humaneval,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--datasets",
        nargs="*",
        default=list(DOWNLOADERS),
        choices=list(DOWNLOADERS),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    hashes = _load_existing_hashes()
    for name in args.datasets:
        print(f"[download] {name} ...", flush=True)
        try:
            entries = DOWNLOADERS[name](args.force)
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"[download] {name} FAILED: {exc}", file=sys.stderr)
            continue
        for key, path, count in entries:
            digest = _sha256(path)
            hashes[key] = {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": digest,
                "count": count,
            }
            print(f"  {key}: {count} rows, {path.relative_to(REPO_ROOT)}, sha256={digest[:12]}")

    _save_hashes(hashes)
    print(f"[download] wrote hashes → {HASHES_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
