"""AFlow-aligned split generation.

Implements the protocol in `experiments.md` §2.1:
- random seed = 42
- validation : test = 1 : 4 (i.e. 20% val / 80% test)
- HotpotQA + DROP: random 1000-sample cap
- MATH: level-5 subset across Counting & Probability / Number Theory /
  Prealgebra / Precalculus (expected total = 617)

Output format (`data/aflow_aligned/<dataset>/<split>.jsonl`):
    {"dataset", "split", "task_id", "prompt", "reference", "metadata"}

Idempotent: re-running with the same seed produces byte-identical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Callable, Iterable

from agentcoop.benchmarks.common import DATA_AFLOW, DATA_RAW, REPO_ROOT, iter_jsonl


SEED = 42
VAL_FRACTION = 0.2
HOTPOT_DROP_CAP = 1000

MATH_LEVEL5_CATEGORIES = {
    "Counting & Probability",
    "Number Theory",
    "Prealgebra",
    "Precalculus",
}


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _aflow_task(
    dataset: str,
    split: str,
    raw: dict[str, Any],
    *,
    prompt: str,
    input_extra: dict[str, Any] | None = None,
    reference_obj: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Emit the benchmarks.md §3 nested JSONL record."""
    input_obj: dict[str, Any] = {"prompt": prompt}
    if input_extra:
        input_obj.update(input_extra)
    return {
        "task_id": raw.get("task_id") or f"{dataset}_{split}_{raw.get('id', '?')}",
        "dataset": dataset,
        "split": split,
        "input": input_obj,
        "reference": reference_obj,
        "metadata": metadata,
    }


def _split_val_test(rows: list[dict[str, Any]], seed: int = SEED, cap: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(seed)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    if cap is not None and len(idx) > cap:
        idx = idx[:cap]
    val_size = int(len(idx) * VAL_FRACTION)
    val_idx = sorted(idx[:val_size])
    test_idx = sorted(idx[val_size:])
    return [rows[i] for i in val_idx], [rows[i] for i in test_idx]


def _import_gsm8k() -> dict[str, int]:
    rows = list(iter_jsonl(DATA_RAW / "gsm8k" / "test.jsonl"))
    val, test = _split_val_test(rows)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "gsm8k" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "gsm8k",
                    split_name,
                    row,
                    prompt=row["question"],
                    input_extra={"question": row["question"]},
                    reference_obj={"answer": row["answer"].split("####")[-1].strip()},
                    metadata={
                        "source": "gsm8k-main",
                        "category": "word_problem_arithmetic",
                        "raw_answer": row["answer"],
                    },
                )
                for row in bucket
            ),
        )
    return counts


def _import_math() -> dict[str, int]:
    # AFlow 617 = level-5 across 4 categories in the MATH **test** split.
    rows: list[dict[str, Any]] = []
    p = DATA_RAW / "math" / "test.jsonl"
    if not p.exists():
        raise FileNotFoundError(p)
    for row in iter_jsonl(p):
        lvl = str(row.get("level", ""))
        if "Level 5" not in lvl and "5" not in lvl:
            continue
        cat = row.get("type", "")
        if cat not in MATH_LEVEL5_CATEGORIES:
            continue
        rows.append({**row, "_source_split": "test.jsonl"})

    if len(rows) != 617:
        # Keep going but record the discrepancy for manual review.
        print(
            f"[aflow-import] MATH level-5 subset: expected 617, got {len(rows)}. "
            "Check dataset version and category-name mapping."
        )

    val, test = _split_val_test(rows)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "math" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "math",
                    split_name,
                    row,
                    prompt=row["problem"],
                    input_extra={"question": row["problem"]},
                    reference_obj={"answer": row["solution"]},
                    metadata={
                        "source": "hendrycks-math-level5",
                        "category": row["type"],
                        "difficulty": row["level"],
                        "source_split": row.get("_source_split"),
                    },
                )
                for row in bucket
            ),
        )
    return counts


def _import_hotpotqa() -> dict[str, int]:
    rows = list(iter_jsonl(DATA_RAW / "hotpotqa" / "validation.jsonl"))
    val, test = _split_val_test(rows, cap=HOTPOT_DROP_CAP)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "hotpotqa" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "hotpotqa",
                    split_name,
                    row,
                    prompt=row["question"],
                    input_extra={
                        "question": row["question"],
                        "context": row.get("context", {}),
                    },
                    reference_obj={
                        "answer": row["answer"],
                        "supporting_facts": row.get("supporting_facts", {}),
                    },
                    metadata={
                        "source": "hotpot_qa/distractor",
                        "category": row.get("type", ""),
                        "difficulty": row.get("level", ""),
                    },
                )
                for row in bucket
            ),
        )
    return counts


def _import_drop() -> dict[str, int]:
    rows = list(iter_jsonl(DATA_RAW / "drop" / "validation.jsonl"))
    val, test = _split_val_test(rows, cap=HOTPOT_DROP_CAP)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "drop" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "drop",
                    split_name,
                    row,
                    prompt=row["question"] + "\n\nPassage:\n" + row["passage"],
                    input_extra={
                        "question": row["question"],
                        "context": row["passage"],
                    },
                    reference_obj={
                        "answer": row.get("answers_spans", {}),
                    },
                    metadata={
                        "source": "drop",
                        "section_id": row.get("section_id", ""),
                    },
                )
                for row in bucket
            ),
        )
    return counts


def _import_humaneval() -> dict[str, int]:
    rows = list(iter_jsonl(DATA_RAW / "humaneval" / "HumanEval.jsonl"))
    # HumanEval has no train/test split in the canonical release — use
    # everything as "test" and carve a small validation slice with seed 42.
    val, test = _split_val_test(rows)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "humaneval" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "humaneval",
                    split_name,
                    row,
                    prompt=row["prompt"],
                    input_extra={"prompt": row["prompt"]},
                    reference_obj={
                        "answer": row.get("canonical_solution", ""),
                        "tests": row.get("test", ""),
                        "entry_point": row.get("entry_point", ""),
                    },
                    metadata={
                        "source": "openai/human-eval",
                        "canonical_task_id": row.get("task_id", ""),
                    },
                )
                for row in bucket
            ),
        )
    return counts


def _import_mbpp() -> dict[str, int]:
    rows: list[dict[str, Any]] = []
    for source in ("train.jsonl", "validation.jsonl", "test.jsonl", "prompt.jsonl"):
        p = DATA_RAW / "mbpp" / source
        if p.exists():
            for r in iter_jsonl(p):
                rows.append({**r, "_source_split": source})
    val, test = _split_val_test(rows)
    counts = {}
    for split_name, bucket in (("validation", val), ("test", test)):
        path = DATA_AFLOW / "mbpp" / f"{split_name}.jsonl"
        counts[split_name] = _write_jsonl(
            path,
            (
                _aflow_task(
                    "mbpp",
                    split_name,
                    row,
                    prompt=row.get("prompt", row.get("text", "")),
                    input_extra={"prompt": row.get("prompt", row.get("text", ""))},
                    reference_obj={
                        "answer": row.get("code", ""),
                        "tests": row.get("test_list", []),
                        "test_setup_code": row.get("test_setup_code", ""),
                    },
                    metadata={
                        "source": "mbpp/sanitized",
                        "source_split": row.get("_source_split"),
                    },
                )
                for row in bucket
            ),
        )
    return counts


IMPORTERS: dict[str, Callable[[], dict[str, int]]] = {
    "gsm8k": _import_gsm8k,
    "math": _import_math,
    "hotpotqa": _import_hotpotqa,
    "drop": _import_drop,
    "humaneval": _import_humaneval,
    "mbpp": _import_mbpp,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--datasets",
        nargs="*",
        default=list(IMPORTERS),
        choices=list(IMPORTERS),
    )
    args = parser.parse_args(argv)

    for name in args.datasets:
        print(f"[aflow-import] {name} ...", flush=True)
        try:
            counts = IMPORTERS[name]()
        except FileNotFoundError as exc:
            print(f"  skipped: {exc}")
            continue
        for split, n in counts.items():
            print(f"  {name}.{split}: {n} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
