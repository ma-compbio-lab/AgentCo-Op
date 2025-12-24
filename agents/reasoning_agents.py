from __future__ import annotations

from typing import Any

from agents.base import Agent
from core.contracts import AgentSpec, JudgeReport, Message, TaskSpec
from prompts import build_system_prompt, build_task_prompt
from utils import now_iso


class LLMAgent(Agent):
    def __init__(self, spec: AgentSpec, model_backend, role: str) -> None:
        super().__init__(spec, model_backend)
        self.role = role

    def run(self, task: TaskSpec, inbox: list[Message], **kwargs) -> Message:
        instructions = kwargs.get("instructions", task.goal)
        system_prompt = build_system_prompt(self.role)
        user_prompt = build_task_prompt(self.role, task, instructions, inbox)
        output = self.model.generate(user_prompt, system=system_prompt)
        return Message(
            sender=self.spec.agent_id,
            receiver="engine",
            content_type="text",
            content=output.text,
            meta={"ts": now_iso(), "model": output.model_name},
        )


class RuleBasedVerifier:
    def verify(self, task: TaskSpec, plan, state: dict) -> JudgeReport:
        if not state.get("messages"):
            return JudgeReport(ok=False, score=0.0, issues=["no_messages"])
        content = state["messages"][-1].content or ""
        missing = []
        for criterion in task.success_criteria:
            if criterion.lower() not in content.lower():
                missing.append(criterion)
        ok = len(missing) == 0
        score = 1.0 if ok else 0.5
        issues = [f"missing_criteria:{m}" for m in missing]
        return JudgeReport(ok=ok, score=score, issues=issues)


class LastMessageAggregator:
    def fuse(self, task: TaskSpec, plan, state: dict) -> str:
        if not state.get("messages"):
            return ""
        return state["messages"][-1].content
