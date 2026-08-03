"""Trace store: thin wrapper around the JSONL TraceWriter that also loads
historical runs for the skill memory / evaluator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from agentcoop.core.tracing import TraceWriter, read_events


class TraceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def writer(self, run_id: str | None = None) -> TraceWriter:
        run_dir = self.root / (run_id or "run-unset")
        return TraceWriter(run_dir, run_id=run_id)

    def iter_runs(self) -> Iterator[Path]:
        if not self.root.exists():
            return
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and (p / "events.jsonl").exists():
                yield p

    def load_events(self, run_id: str) -> list[dict]:
        return list(read_events(self.root / run_id / "events.jsonl"))


__all__ = ["TraceStore"]
