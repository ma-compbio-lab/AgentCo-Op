from __future__ import annotations

from agentcoop.core.integrator import integrate
from agentcoop.core.reviewer import review
from agentcoop.core.schema import EvalContract, NodeResult


def test_integrator_majority_vote_with_normalization() -> None:
    c = EvalContract(normalization="gsm8k_numeric")
    a = NodeResult(node_id="a", output={"final_answer": "42"}, confidence=0.9)
    b = NodeResult(node_id="b", output={"final_answer": "42.0"}, confidence=0.8)
    d = NodeResult(node_id="d", output={"final_answer": "43"}, confidence=0.7)
    r = integrate([a, b, d], c)
    assert abs(r.metrics["agreement"] - 2 / 3) < 1e-9
    assert r.output["final_answer"] in ("42", "42.0")


def test_reviewer_deterministic_clamps_confidence() -> None:
    c = EvalContract(grader="exact_match")
    candidate = NodeResult(node_id="s", output={"final_answer": "wrong"}, confidence=0.95)
    v = review(candidate, c, grader_output={"ok": False, "confidence": 0.0})
    assert v.valid is False and v.confidence == 0.0


def test_reviewer_rubric_requires_evidence() -> None:
    c = EvalContract(grader="llm_rubric", required_evidence=True)
    candidate = NodeResult(node_id="s", output={"final_answer": "x"}, confidence=0.9)
    v = review(candidate, c, llm_verdict={"valid": True, "confidence": 0.9})
    assert v.valid is False
    assert "missing_required_evidence" in v.detected_issues
