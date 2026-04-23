"""Shared types for benchmark loaders + evaluators.

The JSONL schema follows `benchmarks.md` §3:

    {
      "task_id": "...",
      "dataset": "...",
      "split": "...",
      "input":  {"question": "...", "context": ..., "prompt": "..."},
      "reference": {"answer": "...", "tests": ...},
      "metadata": {"source": "...", "category": "...", "difficulty": "..."}
    }

The `BenchmarkTask` dataclass keeps a flat `.prompt` and `.reference` for
convenience but also retains the nested `input` / `reference_obj` /
`metadata` dicts so downstream code can round-trip the full record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_AFLOW = REPO_ROOT / "data" / "aflow_aligned"
RUNS_ROOT = REPO_ROOT / "runs"


@dataclass
class BenchmarkTask:
    task_id: str
    dataset: str
    split: str
    prompt: str
    reference: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    # Nested fields for full round-trip / downstream analysis.
    input: dict[str, Any] = field(default_factory=dict)
    reference_obj: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        """Emit the benchmarks.md §3 JSONL record shape."""
        return {
            "task_id": self.task_id,
            "dataset": self.dataset,
            "split": self.split,
            "input": self.input or {"prompt": self.prompt},
            "reference": self.reference_obj or {"answer": self.reference},
            "metadata": self.metadata,
        }


def task_from_record(record: dict[str, Any]) -> BenchmarkTask:
    """Construct a BenchmarkTask from a benchmarks.md §3 nested record."""
    input_obj = record.get("input", {}) or {}
    reference_obj = record.get("reference", {}) or {}
    # `prompt` is flat-field convenience; fall back to rendering context.
    prompt = input_obj.get("prompt")
    if not prompt:
        q = input_obj.get("question", "")
        ctx = input_obj.get("context")
        prompt = q if not ctx else f"{q}\n\nContext:\n{ctx}"
    reference = reference_obj.get("answer")
    if reference is None and "tests" in reference_obj:
        reference = reference_obj  # keep the whole dict for code/perturb tasks
    return BenchmarkTask(
        task_id=record["task_id"],
        dataset=record["dataset"],
        split=record["split"],
        prompt=prompt,
        reference=reference,
        metadata=record.get("metadata", {}) or {},
        input=input_obj,
        reference_obj=reference_obj,
    )


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, records: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


def ensure_data_exists(path: Path, dataset: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{dataset} data not found at {path}. "
            f"Run: python scripts/download_datasets.py --datasets {dataset}"
        )


__all__ = [
    "BenchmarkTask",
    "task_from_record",
    "iter_jsonl",
    "write_jsonl",
    "ensure_data_exists",
    "DATA_RAW",
    "DATA_AFLOW",
    "RUNS_ROOT",
    "REPO_ROOT",
]
