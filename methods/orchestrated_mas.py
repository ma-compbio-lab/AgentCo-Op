from __future__ import annotations

from agents.reasoning_agents import LastMessageAggregator, RuleBasedVerifier
from core.contracts import TaskSpec
from core.judge import Judge
from core.orchestrator import Orchestrator
from engine.executor import ExecutionEngine
from engine.protocols import default_protocols
from methods.base import MethodResult
from runtime import Runtime


def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    orchestrator = Orchestrator(
        registry=runtime.registry,
        budget_router=runtime.budget_router,
        hooks=runtime.hooks,
    )
    plan = orchestrator.plan(task)

    engine = ExecutionEngine(
        agent_pool=runtime.agent_pool,
        protocols=default_protocols(),
        hooks=runtime.hooks,
        observability=runtime.observability,
    )
    state = engine.run(task, plan)

    judge = Judge(
        verifier=RuleBasedVerifier(),
        aggregator=LastMessageAggregator(),
        hooks=runtime.hooks,
    )
    report, answer = judge.evaluate_and_summarize(task, plan, state)
    return MethodResult(answer=answer, judge_report=report, state=state)
