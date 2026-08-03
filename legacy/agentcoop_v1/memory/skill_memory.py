"""Skill memory — append-only JSONL of (task_profile_hash → outcome).

Used as a prior during compilation, not as a search driver. It records which
blueprints and skill combinations succeeded or failed for a given task
profile signature.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Iterator

from agentcoop.core.schema import TaskProfile


def profile_signature(profile: TaskProfile) -> str:
    """Stable hash over the fields that should change routing."""
    payload = {
        "domain": sorted(profile.domain),
        "answer_type": profile.answer_type,
        "objective": profile.objective,
        "difficulty": profile.difficulty,
        "verification_available": profile.verification_available,
        "tool_need_bucket": _bucket(profile.tool_need),
        "retrieval_need_bucket": _bucket(profile.retrieval_need),
        "repo_execution_need_bucket": _bucket(profile.repo_execution_need),
        "risk_level": profile.risk_level,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def _bucket(x: float) -> str:
    if x < 0.2:
        return "low"
    if x < 0.5:
        return "med"
    return "high"


class SkillMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        profile: TaskProfile,
        *,
        blueprint_id: str,
        skills_used: list[str],
        success: bool,
        metric: float | None = None,
        cost_usd: float = 0.0,
        latency_s: float = 0.0,
        gate_counts: dict[str, int] | None = None,
        failure_modes: list[str] | None = None,
        summary: str = "",
    ) -> None:
        entry = {
            "task_profile_hash": profile_signature(profile),
            "task_id": profile.task_id,
            "blueprint_id": blueprint_id,
            "skills_used": list(skills_used),
            "success": success,
            "metric": metric,
            "cost_usd": cost_usd,
            "latency_s": latency_s,
            "gate_counts": gate_counts or {},
            "failure_modes": failure_modes or [],
            "summary": summary,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def lookup(self, profile: TaskProfile) -> list[dict[str, Any]]:
        sig = profile_signature(profile)
        return [e for e in self.iter_entries() if e.get("task_profile_hash") == sig]

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


__all__ = ["SkillMemory", "profile_signature"]
