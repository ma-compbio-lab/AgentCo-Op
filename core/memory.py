from __future__ import annotations

from typing import Any


class Memory:
    def __init__(self) -> None:
        self._working: dict[str, list[dict[str, Any]]] = {}
        self._episodic: list[dict[str, Any]] = []

    def add_working(self, run_id: str, item: dict[str, Any]) -> None:
        self._working.setdefault(run_id, []).append(item)

    def get_working(self, run_id: str) -> list[dict[str, Any]]:
        return self._working.get(run_id, [])

    def add_episodic(self, item: dict[str, Any]) -> None:
        self._episodic.append(item)

    def get_episodic(self) -> list[dict[str, Any]]:
        return list(self._episodic)
