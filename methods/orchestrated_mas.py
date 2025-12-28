from __future__ import annotations

from core.contracts import TaskSpec
from core.judge import Judge
from core.orchestrator import Orchestrator
from engine.executor import ExecutionEngine
from engine.protocols import default_protocols
from methods.base import MethodResult
from runtime import Runtime


async def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    from utils import log_event, log_section

    log_section("METHOD", "Orchestrated")
    orchestrator = Orchestrator(
        registry=runtime.registry,
        budget_router=runtime.budget_router,
        hooks=runtime.hooks,
        planner_agent=runtime.agent_pool["planner"],
        observability=runtime.observability,
        run_hooks=runtime.run_hooks,
    )
    log_event("METHOD", "start", "orchestrated run", data={"task_id": task.task_id})
    plan = await orchestrator.plan(task, runtime.context, session=runtime.session)

    engine = ExecutionEngine(
        agent_pool=runtime.agent_pool,
        protocols=default_protocols(),
        hooks=runtime.hooks,
        observability=runtime.observability,
        run_hooks=runtime.run_hooks,
    )
    state = await engine.run(task, plan, runtime.context, session=runtime.session)

    judge = Judge(
        judge_agent=runtime.agent_pool["judge"],
        aggregator_agent=runtime.agent_pool["aggregator"],
        hooks=runtime.hooks,
        observability=runtime.observability,
        run_hooks=runtime.run_hooks,
    )
    report, answer = await judge.evaluate_and_summarize(task, plan, state, runtime.context, session=runtime.session)
    log_event(
        "METHOD",
        "done",
        "orchestrated complete",
        data={"ok": report.ok, "score": report.score, "output_len": len(answer)},
    )
    return MethodResult(answer=answer, judge_report=report, state=state)
