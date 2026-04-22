"""MATH loader (level-5 subset via AFlow splits)."""

from __future__ import annotations

from agentcoop.benchmarks.common import (
    BenchmarkTask,
    DATA_AFLOW,
    DATA_RAW,
    ensure_data_exists,
    iter_jsonl,
)


def load(split: str = "test", limit: int | None = None, aflow: bool = True) -> list[BenchmarkTask]:
    root = DATA_AFLOW if aflow else DATA_RAW
    path = root / "math" / f"{split}.jsonl"
    ensure_data_exists(path, "math")
    tasks: list[BenchmarkTask] = []
    for row in iter_jsonl(path):
        if aflow:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference=row["reference"],
                    dataset="math",
                    split=split,
                    metadata=row.get("metadata", {}),
                )
            )
        else:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["problem"],
                    reference=row["solution"],
                    dataset="math",
                    split=split,
                    metadata={"type": row.get("type", ""), "level": row.get("level", "")},
                )
            )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


__all__ = ["load"]
