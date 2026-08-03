"""JSONL event tracing for AgentCo-Op runs.

One `events.jsonl` per run is the single source of truth for post-hoc
inspection. Each event is a flat dict with `run_id`, `ts`, `event`, plus
event-specific fields.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


class TraceWriter:
    """Append-only JSONL writer. Thread-safe via a single Lock."""

    def __init__(self, run_dir: str | os.PathLike, run_id: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or new_run_id()
        self._path = self.run_dir / "events.jsonl"
        self._lock = threading.Lock()
        # Touch the file so readers can tail it immediately.
        self._path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: str, **fields: Any) -> None:
        record = {
            "run_id": self.run_id,
            "ts": time.time(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=_json_default, ensure_ascii=False)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    # --- convenience event helpers ------------------------------------------------

    def run_start(self, blueprint_id: str, task_id: str) -> None:
        self.write("run_start", blueprint_id=blueprint_id, task_id=task_id)

    def run_end(self, status: str, **fields: Any) -> None:
        self.write("run_end", status=status, **fields)

    def node_start(self, node_id: str, input_hash: str | None = None) -> None:
        self.write("node_start", node_id=node_id, input_hash=input_hash)

    def node_end(
        self,
        node_id: str,
        *,
        ok: bool,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_s: float = 0.0,
        confidence: float | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.write(
            "node_end",
            node_id=node_id,
            ok=ok,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_s=latency_s,
            confidence=confidence,
            errors=errors or [],
        )

    def gate_triggered(self, gate: str, action: str, reason: str = "") -> None:
        self.write("gate_triggered", gate=gate, action=action, reason=reason)

    def patch_applied(self, op: str, node_id: str | None = None, reason: str = "") -> None:
        self.write("patch_applied", op=op, node_id=node_id, reason=reason)

    def budget_warning(self, kind: str, used: float, limit: float) -> None:
        self.write("budget_warning", kind=kind, used=used, limit=limit)

    def terminate(self, reason: str) -> None:
        self.write("terminate", reason=reason)


def read_events(path: str | os.PathLike) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


__all__ = ["TraceWriter", "read_events", "new_run_id"]
