"""HumanEval loader (nested schema)."""

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
        path = DATA_AFLOW / "humaneval" / f"{split}.jsonl"
        ensure_data_exists(path, "humaneval")
        tasks = []
        for row in iter_jsonl(path):
            task = task_from_record(row)
            # Flatten reference for graders expecting a dict with `test`/`entry_point`.
            ref = row.get("reference", {}) or {}
            task.reference = {
                "test": ref.get("tests", ""),
                "entry_point": ref.get("entry_point", ""),
                "canonical_solution": ref.get("answer", ""),
            }
            tasks.append(task)
    else:
        path = DATA_RAW / "humaneval" / "HumanEval.jsonl"
        ensure_data_exists(path, "humaneval")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="humaneval",
                split=split,
                prompt=row["prompt"],
                reference={
                    "test": row.get("test", ""),
                    "entry_point": row.get("entry_point", ""),
                    "canonical_solution": row.get("canonical_solution", ""),
                },
                metadata={"task_id": row.get("task_id", "")},
                input={"prompt": row["prompt"]},
                reference_obj={
                    "answer": row.get("canonical_solution", ""),
                    "tests": row.get("test", ""),
                    "entry_point": row.get("entry_point", ""),
                },
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
