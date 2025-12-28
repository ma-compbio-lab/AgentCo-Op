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

    def event_from_result(self, event_type: str, result: Any, extra: dict[str, Any] | None = None) -> None:
        payload = dict(extra or {})
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if usage is not None:
            if isinstance(usage, dict):
                payload["usage"] = usage
            elif hasattr(usage, "model_dump"):
                payload["usage"] = usage.model_dump()
            elif hasattr(usage, "to_dict"):
                payload["usage"] = usage.to_dict()
            else:
                payload["usage"] = {"raw": str(usage)}
        self.event(event_type, payload)
