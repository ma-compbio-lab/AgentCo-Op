from __future__ import annotations

from core.contracts import Message, TaskSpec
from core.runtime import run_agent
from core.spec_enricher import ensure_spec
from methods.base import MethodResult
from runtime import Runtime
from prompts import build_task_prompt


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import log_event, log_section

    log_section("METHOD", "Baseline")
    task = await ensure_spec(task, runtime.context, runtime.agent_pool, session=runtime.session)
    worker = runtime.agent_pool["worker"]
    mcp_manager = getattr(runtime.context, "mcp_manager", None)
    worker_mcp_prompt = None
    if mcp_manager and mcp_manager.is_enabled():
        worker_mcp_prompt = await mcp_manager.get_prompt("worker")
    planning_memory = getattr(runtime.context, "planning_memory", None)
    plan_block = None
    if planning_memory and getattr(planning_memory, "enabled", False):
        plan_block = planning_memory.render_prompt_block(task)
    prompt = build_task_prompt(
        "worker",
        task,
        task.goal,
        [],
        memory_block=plan_block,
        extra_instructions=worker_mcp_prompt,
    )
    log_event("METHOD", "start", "baseline run", data={"task_id": task.task_id})
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
    log_event("METHOD", "done", "baseline complete", data={"output_len": len(output_text)})
    return MethodResult(answer=output_text, state={"messages": [msg]})
