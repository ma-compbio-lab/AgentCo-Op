from __future__ import annotations

import queue
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    level: str = "info"
    ts: str = ""


class EventBus:
    def __init__(self, max_events: int = 1000) -> None:
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._events: list[dict[str, Any]] = []
        self._max_events = max_events

    def emit(self, event_type: str, data: dict[str, Any] | None = None, *, level: str = "info") -> None:
        event = {
            "type": event_type,
            "data": data or {},
            "level": level,
            "ts": _now_iso(),
        }
        self.publish(event)

    def publish(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        for callback in list(self._subscribers):
            callback(event)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def subscribe_queue(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subscribe(q.put)
        return q

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
