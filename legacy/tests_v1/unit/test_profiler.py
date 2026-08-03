from __future__ import annotations

from agentcoop.core.profiler import profile_task


def test_code_profile() -> None:
    r = profile_task("Implement a function that reverses a string. Pass the unit tests.")
    assert "code" in r.profile.domain
    assert r.profile.answer_type == "program"
    assert r.profile.verification_available == "unit_test"


def test_math_profile() -> None:
    r = profile_task("What is 13 * 17?")
    assert "math" in r.profile.domain
    assert r.profile.verification_available == "exact"


def test_hotpot_profile() -> None:
    r = profile_task("Multi-hop HotpotQA question about two entities.")
    assert "qa" in r.profile.domain
    assert r.profile.retrieval_need >= 0.9


def test_drop_profile_no_retrieval() -> None:
    r = profile_task("How many points did the team score in total in the passage?")
    assert "qa" in r.profile.domain
    assert r.profile.retrieval_need < 0.3


def test_repo_profile_high_risk() -> None:
    r = profile_task("Clone the github.com/... repo and run the Dockerfile.")
    assert "repo" in r.profile.domain
    assert r.profile.repo_execution_need >= 0.5
    assert r.profile.risk_level in ("medium", "high")
