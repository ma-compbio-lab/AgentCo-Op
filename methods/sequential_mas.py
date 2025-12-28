from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.runtime import run_agent
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_plan_prompt, build_task_prompt


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    planner = runtime.agent_pool["planner"]
    worker = runtime.agent_pool["worker"]

    candidates = runtime.context.registry.filter(required_caps=["reason"], input_types=task.input_modalities)
    candidates = [c for c in candidates if c.agent_id not in {"planner", "judge", "aggregator"}]
    plan_prompt = build_plan_prompt(task, candidates)
    plan_result = await run_agent(
        planner,
        plan_prompt,
        runtime.context,
        session=runtime.session,
        workflow_name="sequential_plan",
        max_turns=4,
        hooks=runtime.run_hooks,
    )
    runtime.observability.event_from_result("sequential_plan", plan_result, {"method": "sequential"})
    plan_text = getattr(plan_result, "final_output", "")
    if not isinstance(plan_text, str):
        plan_text = str(plan_text)
    plan_msg = Message(sender="planner", receiver="engine", content_type="text", content=str(plan_text))

    worker_prompt = build_task_prompt("worker", task, task.goal, [plan_msg])
    worker_result = await run_agent(
        worker,
        worker_prompt,
        runtime.context,
        session=runtime.session,
        workflow_name="sequential_work",
        max_turns=4,
        hooks=runtime.run_hooks,
    )
    runtime.observability.event_from_result("sequential_work", worker_result, {"method": "sequential"})
    worker_text = getattr(worker_result, "final_output", "")
    if not isinstance(worker_text, str):
        worker_text = str(worker_text)
    worker_msg = Message(sender="worker", receiver="engine", content_type="text", content=worker_text)

    state = {"messages": [plan_msg, worker_msg]}
    return MethodResult(answer=worker_text, state=state)
