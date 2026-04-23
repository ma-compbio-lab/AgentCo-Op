"""DROP loader (1000-cap via AFlow splits; nested schema)."""

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
        path = DATA_AFLOW / "drop" / f"{split}.jsonl"
        ensure_data_exists(path, "drop")
        tasks = []
        for row in iter_jsonl(path):
            task = task_from_record(row)
            ref = row.get("reference", {}) or {}
            task.reference = ref.get("answer", {}) or ref
            tasks.append(task)
    else:
        path = DATA_RAW / "drop" / f"{split}.jsonl"
        ensure_data_exists(path, "drop")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="drop",
                split=split,
                prompt=row["question"] + "\n\nPassage:\n" + row["passage"],
                reference=row.get("answers_spans", {}),
                metadata={"section_id": row.get("section_id", "")},
                input={"question": row["question"], "context": row["passage"], "prompt": row["question"] + "\n\nPassage:\n" + row["passage"]},
                reference_obj={"answer": row.get("answers_spans", {})},
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
