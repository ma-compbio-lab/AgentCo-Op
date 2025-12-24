from __future__ import annotations

from typing import Any, Callable


HookFn = Callable[..., Any]


class HookManager:
    def __init__(self) -> None:
        self._pre_plan: list[HookFn] = []
        self._post_plan: list[HookFn] = []
        self._pre_step: list[HookFn] = []
        self._post_step: list[HookFn] = []
        self._before_judge: list[HookFn] = []
        self._before_output: list[HookFn] = []
        self._on_tool_error: list[HookFn] = []

    def register_pre_plan(self, hook: HookFn) -> None:
        self._pre_plan.append(hook)

    def register_post_plan(self, hook: HookFn) -> None:
        self._post_plan.append(hook)

    def register_pre_step(self, hook: HookFn) -> None:
        self._pre_step.append(hook)

    def register_post_step(self, hook: HookFn) -> None:
        self._post_step.append(hook)

    def register_before_judge(self, hook: HookFn) -> None:
        self._before_judge.append(hook)

    def register_before_output(self, hook: HookFn) -> None:
        self._before_output.append(hook)

    def register_on_tool_error(self, hook: HookFn) -> None:
        self._on_tool_error.append(hook)

    def pre_plan(self, task):
        for hook in self._pre_plan:
            task = hook(task)
        return task

    def post_plan(self, task, plan):
        for hook in self._post_plan:
            plan = hook(task, plan)
        return plan

    def pre_step(self, task, plan, state, step):
        for hook in self._pre_step:
            step = hook(task, plan, state, step)
        return step

    def post_step(self, task, plan, state, output_msg):
        patch = None
        for hook in self._post_step:
            patch = hook(task, plan, state, output_msg) or patch
        return patch

    def before_judge(self, task, plan, state):
        for hook in self._before_judge:
            state = hook(task, plan, state)
        return state

    def before_output(self, task, plan, state, final_answer: str) -> str:
        for hook in self._before_output:
            final_answer = hook(task, plan, state, final_answer)
        return final_answer

    def on_tool_error(self, task, plan, state, error):
        for hook in self._on_tool_error:
            hook(task, plan, state, error)
