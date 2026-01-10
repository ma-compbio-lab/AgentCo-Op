from __future__ import annotations

from typing import Any

try:
    from agents import RunHooks  # type: ignore
    _HooksBase = RunHooks
except Exception:  # noqa: BLE001 - optional dependency
    try:
        from agents import RunHooksBase  # type: ignore

        _HooksBase = RunHooksBase
    except Exception:  # noqa: BLE001 - optional dependency
        _HooksBase = object  # type: ignore


class EventStreamHooks(_HooksBase):
    def __init__(self, event_bus) -> None:
        self.event_bus = event_bus

    @staticmethod
    def _get_arg(args: tuple[Any, ...], index: int) -> Any | None:
        if len(args) > index:
            return args[index]
        return None

    @staticmethod
    def _get_name(obj: Any | None) -> str:
        if obj is None:
            return "unknown"
        return getattr(obj, "name", str(obj))

    async def on_agent_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        agent = kwargs.get("agent") or self._get_arg(args, 1)
        self.event_bus.emit("agent_start", {"agent": self._get_name(agent)})

    async def on_agent_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        agent = kwargs.get("agent") or self._get_arg(args, 1)
        self.event_bus.emit("agent_end", {"agent": self._get_name(agent)})

    async def on_tool_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        tool = kwargs.get("tool") or self._get_arg(args, 1)
        self.event_bus.emit("tool_start", {"tool": self._get_name(tool)})

    async def on_tool_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        tool = kwargs.get("tool") or self._get_arg(args, 1)
        self.event_bus.emit("tool_end", {"tool": self._get_name(tool)})

    async def on_llm_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("llm_start", {})

    async def on_llm_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        self.event_bus.emit("llm_end", {})

    async def on_handoff(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        if not self.event_bus:
            return
        from_agent = kwargs.get("from_agent") or self._get_arg(args, 1)
        to_agent = kwargs.get("to_agent") or self._get_arg(args, 2)
        self.event_bus.emit(
            "handoff",
            {
                "from": self._get_name(from_agent),
                "to": self._get_name(to_agent),
            },
        )
