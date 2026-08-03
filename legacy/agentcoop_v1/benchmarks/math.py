"""MATH loader (level-5 × 4-category AFlow subset, nested schema)."""

from __future__ import annotations

from agentcoop.benchmarks.common import (
    BenchmarkTask,
    DATA_AFLOW,
    DATA_RAW,
    ensure_data_exists,
    iter_jsonl,
    task_from_record,
)


def load(split: str = "test", limit: int | None = None, aflow: bool = True) -> list[BenchmarkTask]:
    if aflow:
        path = DATA_AFLOW / "math" / f"{split}.jsonl"
        ensure_data_exists(path, "math")
        tasks = [task_from_record(row) for row in iter_jsonl(path)]
    else:
        path = DATA_RAW / "math" / f"{split}.jsonl"
        ensure_data_exists(path, "math")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="math",
                split=split,
                prompt=row["problem"],
                reference=row["solution"],
                metadata={"type": row.get("type", ""), "level": row.get("level", "")},
                input={"prompt": row["problem"], "question": row["problem"]},
                reference_obj={"answer": row["solution"]},
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
