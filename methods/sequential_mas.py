from __future__ import annotations

from agents.reasoning_agents import LastMessageAggregator, RuleBasedVerifier
from core.contracts import TaskSpec
from methods.base import MethodResult
from runtime import Runtime


def run(task: TaskSpec, runtime: Runtime) -> MethodResult:
    planner = runtime.agent_pool["planner"]
    worker = runtime.agent_pool["worker"]

    plan_msg = planner.run(task, inbox=[], instructions=f"Create a short plan for: {task.goal}")
    worker_msg = worker.run(task, inbox=[plan_msg], instructions=task.goal)

    state = {"messages": [plan_msg, worker_msg]}
    verifier = RuleBasedVerifier()
    report = verifier.verify(task, plan=None, state=state)
    aggregator = LastMessageAggregator()
    answer = aggregator.fuse(task, plan=None, state=state)
    return MethodResult(answer=answer, judge_report=report, state=state)
