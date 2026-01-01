from __future__ import annotations

from typing import Any

try:
    from agents import RunHooksBase
except Exception:  # noqa: BLE001 - optional dependency
    RunHooksBase = object  # type: ignore


class EventStreamHooks(RunHooksBase):
    def __init__(self, event_bus) -> None:
        self.event_bus = event_bus

    def on_agent_start(self, run_context, agent, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("agent_start", {"agent": getattr(agent, "name", str(agent))})

    def on_agent_end(self, run_context, agent, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("agent_end", {"agent": getattr(agent, "name", str(agent))})

    def on_tool_start(self, run_context, tool, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("tool_start", {"tool": getattr(tool, "name", str(tool))})

    def on_tool_end(self, run_context, tool, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("tool_end", {"tool": getattr(tool, "name", str(tool))})

    def on_llm_start(self, run_context, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("llm_start", {})

    def on_llm_end(self, run_context, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("llm_end", {})

    def on_handoff(self, run_context, from_agent, to_agent, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit(
            "handoff",
            {
                "from": getattr(from_agent, "name", str(from_agent)),
                "to": getattr(to_agent, "name", str(to_agent)),
            },
        )
