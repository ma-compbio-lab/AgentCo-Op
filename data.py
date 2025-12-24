from __future__ import annotations

import json

from core.contracts import TaskSpec


def load_tasks(path: str) -> list[TaskSpec]:
    tasks: list[TaskSpec] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            tasks.append(TaskSpec(**payload))
    return tasks

