from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts import AgentSpec, Message, TaskSpec


class Agent(ABC):
    def __init__(self, spec: AgentSpec, model_backend, tools: dict[str, Any] | None = None) -> None:
        self.spec = spec
        self.model = model_backend
        self.tools = tools or {}

    @abstractmethod
    def run(self, task: TaskSpec, inbox: list[Message], **kwargs) -> Message:
        ...
