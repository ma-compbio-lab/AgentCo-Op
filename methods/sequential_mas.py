from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.runtime import run_agent
from core.spec_enricher import ensure_spec
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_plan_prompt, build_task_prompt, format_memory_block


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import log_event, log_section

    log_section("METHOD", "Sequential")
    task = await ensure_spec(task, runtime.context, runtime.agent_pool, session=runtime.session)
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
    planning_memory = getattr(runtime.context, "planning_memory", None)
    plan_block = None
    if planning_memory and getattr(planning_memory, "enabled", False):
        plan_block = planning_memory.render_prompt_block(task)
    if plan_block:
        memory_block = "\n\n".join(block for block in [plan_block, memory_block] if block)
    mcp_manager = getattr(runtime.context, "mcp_manager", None)
    mcp_summary = None
    planner_mcp_prompt = None
    worker_mcp_prompt = None
    if mcp_manager and mcp_manager.is_enabled():
        mcp_summary = await mcp_manager.describe_servers()
        planner_mcp_prompt = await mcp_manager.get_prompt("planner")
        worker_mcp_prompt = await mcp_manager.get_prompt("worker")
    plan_prompt = build_plan_prompt(
        task,
        candidates,
        memory_block=memory_block,
        mcp_summary=mcp_summary,
        extra_instructions=planner_mcp_prompt,
    )
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
    if plan_block:
        worker_memory_block = "\n\n".join(block for block in [plan_block, worker_memory_block] if block)
    worker_prompt = build_task_prompt(
        "worker",
        task,
        task.goal,
        [plan_msg],
        memory_block=worker_memory_block,
        extra_instructions=worker_mcp_prompt,
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
