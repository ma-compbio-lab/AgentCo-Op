"""HotpotQA loader (distractor setting, 1000-sample cap via AFlow splits)."""

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
    path = root / "hotpotqa" / f"{split}.jsonl"
    ensure_data_exists(path, "hotpotqa")
    tasks: list[BenchmarkTask] = []
    for row in iter_jsonl(path):
        if aflow:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["prompt"],
                    reference=row["reference"],
                    dataset="hotpotqa",
                    split=split,
                    metadata=row.get("metadata", {}),
                )
            )
        else:
            tasks.append(
                BenchmarkTask(
                    task_id=row["task_id"],
                    prompt=row["question"],
                    reference=row["answer"],
                    dataset="hotpotqa",
                    split=split,
                    metadata={
                        "context": row.get("context", {}),
                        "supporting_facts": row.get("supporting_facts", {}),
                    },
                )
            )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


__all__ = ["load"]
