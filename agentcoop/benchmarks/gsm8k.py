"""GSM8K loader."""

from __future__ import annotations

from typing import Iterator

from agentcoop.benchmarks.common import (
    BenchmarkTask,
    DATA_AFLOW,
    DATA_RAW,
    ensure_data_exists,
    iter_jsonl,
)


def load(split: str = "test", limit: int | None = None, aflow: bool = True) -> list[BenchmarkTask]:
    root = DATA_AFLOW if aflow else DATA_RAW
    path = root / "gsm8k" / f"{split}.jsonl"
    ensure_data_exists(path, "gsm8k")
    tasks: list[BenchmarkTask] = []
    for i, row in enumerate(iter_jsonl(path)):
        if aflow:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference=row["reference"],
                    dataset="gsm8k",
                    split=split,
                    metadata=row.get("metadata", {}),
                )
            )
        else:
            ref = row["answer"].split("####")[-1].strip()
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["question"],
                    reference=ref,
                    dataset="gsm8k",
                    split=split,
                    metadata={"raw_answer": row["answer"]},
                )
            )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


__all__ = ["load"]
