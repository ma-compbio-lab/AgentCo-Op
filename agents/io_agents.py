from __future__ import annotations

from agents.base import Agent
from core.contracts import Message, TaskSpec
from utils import now_iso


class PassthroughIOAgent(Agent):
    def run(self, task: TaskSpec, inbox: list[Message], **kwargs) -> Message:
        content = inbox[-1].content if inbox else ""
        return Message(
            sender=self.spec.agent_id,
            receiver="engine",
            content_type="text",
            content=content,
            meta={"ts": now_iso(), "note": "passthrough"},
        )
