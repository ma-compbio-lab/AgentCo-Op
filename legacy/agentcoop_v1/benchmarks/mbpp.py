"""MBPP loader (sanitized, nested schema)."""

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
        path = DATA_AFLOW / "mbpp" / f"{split}.jsonl"
        ensure_data_exists(path, "mbpp")
        tasks = []
        for row in iter_jsonl(path):
            task = task_from_record(row)
            ref = row.get("reference", {}) or {}
            tests = ref.get("tests", []) or []
            # Per benchmarks.md §4.4 the test_list is public — include it in
            # the prompt so the programmer sees the canonical function name.
            base_prompt = (task.input or {}).get("prompt") or task.prompt
            if tests:
                augmented = (
                    f"{base_prompt}\n\nYour code must pass these tests:\n"
                    + "\n".join(tests)
                )
                task.prompt = augmented
                task.input = {**(task.input or {}), "prompt": augmented, "public_tests": tests}
            task.reference = {
                "code": ref.get("answer", ""),
                "test_list": tests,
                "test_setup_code": ref.get("test_setup_code", ""),
            }
            tasks.append(task)
    else:
        path = DATA_RAW / "mbpp" / f"{split}.jsonl"
        ensure_data_exists(path, "mbpp")
        tasks = [
            BenchmarkTask(
                task_id=row["task_id"],
                dataset="mbpp",
                split=split,
                prompt=row.get("prompt", row.get("text", "")),
                reference={
                    "code": row.get("code", ""),
                    "test_list": row.get("test_list", []),
                    "test_setup_code": row.get("test_setup_code", ""),
                },
                metadata={},
                input={"prompt": row.get("prompt", row.get("text", ""))},
                reference_obj={
                    "answer": row.get("code", ""),
                    "tests": row.get("test_list", []),
                },
            )
            for row in iter_jsonl(path)
        ]
    return tasks if limit is None else tasks[:limit]


__all__ = ["load"]
