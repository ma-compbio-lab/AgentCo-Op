from __future__ import annotations

from pathlib import Path

import pytest

from agentcoop.core.schema import Budget, TaskProfile
from agentcoop.memory import (
    ArtifactStore,
    Blackboard,
    BlackboardAccessError,
    SkillMemory,
    profile_signature,
    summarize,
)


def test_blackboard_private_read_blocked() -> None:
    bb = Blackboard()
    bb.write("n1", "secret", "shh", visibility="private")
    with pytest.raises(BlackboardAccessError):
        bb.read("n2", "secret")


def test_blackboard_shared_read_default() -> None:
    bb = Blackboard()
    bb.write("n1", "k", {"a": 1})
    assert bb.read("n2", "k") == {"a": 1}


def test_artifact_store_roundtrip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.put_text("a/b.txt", "hello")
    assert store.read_text("a/b.txt") == "hello"
    assert store.listdir("a") == ["a/b.txt"]


def test_skill_memory_lookup(tmp_path: Path) -> None:
    prof = TaskProfile(task_id="t", raw_task="q", budget=Budget(), domain=["math"])
    sm = SkillMemory(tmp_path / "sm.jsonl")
    sm.record(prof, blueprint_id="bp", skills_used=["s"], success=True, metric=1.0)
    hits = sm.lookup(prof)
    assert len(hits) == 1 and hits[0]["success"]
    assert hits[0]["task_profile_hash"] == profile_signature(prof)


def test_summarize_truncates() -> None:
    out = summarize(["a" * 200, "b" * 200], max_tokens=10)
    assert len(out) <= 10 * 4 + 2  # char budget + join slack
