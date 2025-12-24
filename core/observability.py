from __future__ import annotations

from typing import Any

from core.contracts import TraceEvent
from utils import append_jsonl, ensure_dir


class Observability:
    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        ensure_dir(log_dir)
        self.trace_path = f"{log_dir}/traces.jsonl"

    def event(self, event_type: str, data: dict[str, Any]) -> None:
        event = TraceEvent(event_type=event_type, data=data)
        append_jsonl(self.trace_path, event.model_dump(mode="json"))
