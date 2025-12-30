from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.runtime import run_agent
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_plan_prompt, build_task_prompt, format_memory_block


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import log_event, log_section

    log_section("METHOD", "Sequential")
    planner = runtime.agent_pool["planner"]
    worker = runtime.agent_pool["worker"]

    candidates = runtime.context.registry.filter(required_caps=["reason"], input_types=task.input_modalities)
    candidates = [c for c in candidates if c.agent_id not in {"planner", "judge", "aggregator"}]
    memory_block = None
    memory = getattr(runtime.context, "memory", None)
    if memory and memory.enabled:
        recall = memory.recall_global(
            query=f"{task.goal} planning failures or best protocols",
            k=memory.top_k,
        )
        memory_block = format_memory_block(recall, title="Historical Attempts")
    plan_prompt = build_plan_prompt(task, candidates, memory_block=memory_block)
    log_event("METHOD", "start", "sequential run", data={"task_id": task.task_id})
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

    worker_memory_block = None
    if memory and memory.enabled:
        recall = memory.recall_agent("worker", query=f"{task.goal} {task.goal}", k=memory.top_k)
        worker_memory_block = format_memory_block(recall, title="Retrieved Memory")
    worker_prompt = build_task_prompt(
        "worker",
        task,
        task.goal,
        [plan_msg],
        memory_block=worker_memory_block,
    )
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
    log_event("METHOD", "done", "sequential complete", data={"output_len": len(worker_text)})
    return MethodResult(answer=worker_text, state=state)
