from __future__ import annotations

from core.contracts import TaskSpec
from methods.base import MethodResult
from runtime import Runtime


def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    worker = runtime.agent_pool["worker"]
    msg = worker.run(task, inbox=[], instructions=task.goal)
    return MethodResult(answer=msg.content, state={"messages": [msg]})
