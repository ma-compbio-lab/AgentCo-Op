from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.runtime import run_agent
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_task_prompt


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    worker = runtime.agent_pool["worker"]
    prompt = build_task_prompt("worker", task, task.goal, [])
    result = await run_agent(
        worker,
        prompt,
        runtime.context,
        session=runtime.session,
        workflow_name="baseline",
        max_turns=4,
        hooks=runtime.run_hooks,
    )
    runtime.observability.event_from_result("baseline_result", result, {"method": "baseline"})
    output_text = getattr(result, "final_output", "")
    if not isinstance(output_text, str):
        output_text = str(output_text)
    msg = Message(sender="worker", receiver="engine", content_type="text", content=output_text)
    return MethodResult(answer=output_text, state={"messages": [msg]})
