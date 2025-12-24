from __future__ import annotations

from typing import Iterable


class SafetyGate:
    def __init__(self, tool_allowlist: Iterable[str] | None = None) -> None:
        self._tool_allowlist = set(tool_allowlist or [])

    def is_tool_allowed(self, tool_name: str) -> bool:
        if not self._tool_allowlist:
            return True
        return tool_name in self._tool_allowlist

    def filter_output(self, text: str) -> str:
        return text
