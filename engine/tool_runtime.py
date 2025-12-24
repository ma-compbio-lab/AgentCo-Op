from __future__ import annotations

from typing import Any, Callable

from core.hooks import HookManager
from core.observability import Observability
from core.safety import SafetyGate


class ToolRuntime:
    def __init__(
        self,
        tools: dict[str, Callable[..., Any]],
        safety: SafetyGate,
        hooks: HookManager,
        observability: Observability,
    ) -> None:
        self.tools = tools
        self.safety = safety
        self.hooks = hooks
        self.obs = observability

    def call(self, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        if tool_name not in self.tools:
            raise KeyError(f"Tool not found: {tool_name}")
        if not self.safety.is_tool_allowed(tool_name):
            raise PermissionError(f"Tool not allowed: {tool_name}")
        try:
            result = self.tools[tool_name](*args, **kwargs)
            self.obs.event("tool_call", {"tool": tool_name})
            return result
        except Exception as exc:  # noqa: BLE001 - minimal runtime
            self.hooks.on_tool_error(None, None, {}, exc)
            self.obs.event("tool_error", {"tool": tool_name, "error": str(exc)})
            raise
