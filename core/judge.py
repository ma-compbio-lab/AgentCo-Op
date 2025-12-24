from __future__ import annotations

from typing import Protocol

from core.contracts import JudgeReport, TaskSpec, ExecutionPlan
from core.hooks import HookManager


class Verifier(Protocol):
    def verify(self, task: TaskSpec, plan: ExecutionPlan, state: dict) -> JudgeReport:
        ...


class Aggregator(Protocol):
    def fuse(self, task: TaskSpec, plan: ExecutionPlan, state: dict) -> str:
        ...


class Judge:
    def __init__(self, verifier: Verifier, aggregator: Aggregator, hooks: HookManager) -> None:
        self.verifier = verifier
        self.aggregator = aggregator
        self.hooks = hooks

    def evaluate_and_summarize(
        self, task: TaskSpec, plan: ExecutionPlan, state: dict
    ) -> tuple[JudgeReport, str]:
        state = self.hooks.before_judge(task, plan, state)
        report = self.verifier.verify(task, plan, state)
        if not report.ok:
            return report, ""
        final_answer = self.aggregator.fuse(task, plan, state)
        final_answer = self.hooks.before_output(task, plan, state, final_answer)
        return report, final_answer
