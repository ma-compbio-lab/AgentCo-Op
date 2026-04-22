"""MBPP loader (sanitized)."""

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
        path = root / "mbpp" / f"{split}.jsonl"
    else:
        path = root / "mbpp" / f"{split}.jsonl"
    ensure_data_exists(path, "mbpp")
    tasks: list[BenchmarkTask] = []
    for row in iter_jsonl(path):
        if aflow:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference=row["reference"],
                    dataset="mbpp",
                    split=split,
                    metadata=row.get("metadata", {}),
                )
            )
        else:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row.get("prompt", row.get("text", "")),
                    reference={
                        "code": row.get("code", ""),
                        "test_list": row.get("test_list", []),
                        "test_setup_code": row.get("test_setup_code", ""),
                    },
                    dataset="mbpp",
                    split=split,
                    metadata={},
                )
            )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


__all__ = ["load"]
