"""HotpotQA loader (distractor, 1000-cap via AFlow splits; nested schema)."""

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
        path = DATA_AFLOW / "hotpotqa" / f"{split}.jsonl"
        ensure_data_exists(path, "hotpotqa")
        tasks = []
        for row in iter_jsonl(path):
            task = task_from_record(row)
            ref = row.get("reference", {}) or {}
            task.reference = ref.get("answer", "")
            tasks.append(task)
    else:
        path = DATA_RAW / "hotpotqa" / f"{split}.jsonl"
        ensure_data_exists(path, "hotpotqa")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="hotpotqa",
                split=split,
                prompt=row["question"],
                reference=row["answer"],
                metadata={
                    "context": row.get("context", {}),
                    "supporting_facts": row.get("supporting_facts", {}),
                },
                input={"question": row["question"], "context": row.get("context", {}), "prompt": row["question"]},
                reference_obj={"answer": row["answer"], "supporting_facts": row.get("supporting_facts", {})},
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
