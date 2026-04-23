"""GSM8K loader (AFlow-aligned nested schema)."""

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
        path = DATA_AFLOW / "gsm8k" / f"{split}.jsonl"
        ensure_data_exists(path, "gsm8k")
        tasks = [task_from_record(row) for row in iter_jsonl(path)]
    else:
        path = DATA_RAW / "gsm8k" / f"{split}.jsonl"
        ensure_data_exists(path, "gsm8k")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="gsm8k",
                split=split,
                prompt=row["question"],
                reference=row["answer"].split("####")[-1].strip(),
                metadata={"raw_answer": row["answer"]},
                input={"prompt": row["question"], "question": row["question"]},
                reference_obj={"answer": row["answer"].split("####")[-1].strip()},
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
