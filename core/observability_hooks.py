from __future__ import annotations

import re
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
    _URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")

    def __init__(self, event_bus=None) -> None:
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

    @classmethod
    def _extract_urls(cls, text: str, limit: int = 5) -> list[str]:
        if not text:
            return []
        out: list[str] = []
        for match in cls._URL_RE.findall(text):
            match = match.rstrip(".,;:!?)]}\"'>")
            if match in out:
                continue
            out.append(match)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _is_web_search_tool(tool_name: str) -> bool:
        normalized = (tool_name or "").strip().lower()
        return normalized in {"web_search", "websearch"} or "web_search" in normalized

    @staticmethod
    def _safe_log_preview(text: str, limit: int = 500) -> str:
        if not text:
            return ""
        compact = " ".join(str(text).split())
        return compact if len(compact) <= limit else compact[:limit] + "..."

    @staticmethod
    def _emit(event_bus, event_type: str, data: dict[str, Any]) -> None:
        if event_bus:
            event_bus.emit(event_type, data)

    async def on_agent_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        agent = kwargs.get("agent") or self._get_arg(args, 1)
        self._emit(self.event_bus, "agent_start", {"agent": self._get_name(agent)})

    async def on_agent_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        agent = kwargs.get("agent") or self._get_arg(args, 1)
        self._emit(self.event_bus, "agent_end", {"agent": self._get_name(agent)})

    async def on_tool_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        from utils import log_event

        agent = kwargs.get("agent") or self._get_arg(args, 1)
        tool = kwargs.get("tool") or self._get_arg(args, 2)
        agent_name = self._get_name(agent)
        tool_name = self._get_name(tool)
        self._emit(self.event_bus, "tool_start", {"tool": tool_name})
        if self._is_web_search_tool(tool_name):
            payload = {"agent": agent_name, "tool": tool_name}
            self._emit(self.event_bus, "web_search_start", payload)
            log_event("TOOLS", "web_search_start", "web search started", data=payload)

    async def on_tool_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        from utils import log_event

        context = kwargs.get("context") or self._get_arg(args, 0)
        agent = kwargs.get("agent") or self._get_arg(args, 1)
        tool = kwargs.get("tool") or self._get_arg(args, 2)
        result = kwargs.get("result")
        if result is None:
            result = self._get_arg(args, 3) or self._get_arg(args, 2)
        tool_name = self._get_name(tool)
        self._emit(self.event_bus, "tool_end", {"tool": tool_name})
        if not self._is_web_search_tool(tool_name):
            return

        agent_name = self._get_name(agent)
        run_id = getattr(getattr(context, "context", None), "run_id", None)
        result_text = str(result) if result is not None else ""
        preview = self._safe_log_preview(result_text)
        urls = self._extract_urls(result_text)
        payload = {
            "agent": agent_name,
            "tool": tool_name,
            "run_id": run_id,
            "preview": preview,
            "urls": urls,
        }
        self._emit(self.event_bus, "web_search_result", payload)
        log_event("TOOLS", "web_search_result", "web search result", data=payload)

    async def on_llm_start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._emit(self.event_bus, "llm_start", {})

    async def on_llm_end(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._emit(self.event_bus, "llm_end", {})

    async def on_handoff(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        from_agent = kwargs.get("from_agent") or self._get_arg(args, 1)
        to_agent = kwargs.get("to_agent") or self._get_arg(args, 2)
        self._emit(
            self.event_bus,
            "handoff",
            {
                "from": self._get_name(from_agent),
                "to": self._get_name(to_agent),
            },
        )
