"""Shared types for benchmark loaders + evaluators."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_AFLOW = REPO_ROOT / "data" / "aflow_aligned"


@dataclass
class BenchmarkTask:
    task_id: str
    prompt: str
    reference: Any
    dataset: str
    split: str
    metadata: dict[str, Any] = field(default_factory=dict)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    import json

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def ensure_data_exists(path: Path, dataset: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"{dataset} data not found at {path}. "
            f"Run: python scripts/download_datasets.py --datasets {dataset}"
        )


__all__ = ["BenchmarkTask", "iter_jsonl", "ensure_data_exists", "DATA_RAW", "DATA_AFLOW", "REPO_ROOT"]
