"""HumanEval loader."""

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
    if aflow:
        path = root / "humaneval" / f"{split}.jsonl"
    else:
        path = root / "humaneval" / "HumanEval.jsonl"
    ensure_data_exists(path, "humaneval")
    tasks: list[BenchmarkTask] = []
    for row in iter_jsonl(path):
        if aflow:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference=row["reference"],
                    dataset="humaneval",
                    split=split,
                    metadata=row.get("metadata", {}),
                )
            )
        else:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference={
                        "test": row.get("test", ""),
                        "entry_point": row.get("entry_point", ""),
                        "canonical_solution": row.get("canonical_solution", ""),
                    },
                    dataset="humaneval",
                    split=split,
                    metadata={"task_id": row.get("task_id", "")},
                )
            )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


__all__ = ["load"]
