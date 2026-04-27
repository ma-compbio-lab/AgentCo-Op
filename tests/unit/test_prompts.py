"""Sanity tests for the role × dataset prompt registry."""

from __future__ import annotations

from agentcoop.backends.prompts import build_messages, parse_output


def _payload(question: str, dataset: str | None = None, prior: dict | None = None) -> dict:
    p: dict = {"task_input": {"task": question, "input": {"prompt": question}}}
    if dataset:
        p["task_input"]["dataset"] = dataset
    if prior:
        p.update(prior)
    return p


def test_gsm8k_solver_prompt_asks_for_answer_marker() -> None:
    sys, user, want_json = build_messages(
        role="solver", node_id="solver", dataset="gsm8k",
        payload=_payload("Janet has 3 apples..."),
    )
    assert "Answer:" in sys
    assert want_json is False
    assert "Janet has 3 apples" in user


def test_math_formatter_uses_plain_text_for_latex_safety() -> None:
    sys, _u, want_json = build_messages(
        role="formatter", node_id="final_extractor", dataset="math",
        payload=_payload("Find x"),
    )
    # Plain-text avoids JSON mangling \boxed → backspace.
    assert want_json is False
    assert "\\boxed" in sys


def test_math_solver_output_extracts_boxed() -> None:
    out = parse_output(
        role="solver", node_id="specialist", dataset="math",
        raw_text="Long CoT...\n\n\\boxed{42}",
    )
    assert out["final_answer"] == "42"
    assert out["boxed"] == "42"


def test_math_formatter_repairs_backspace_corruption() -> None:
    # Simulates the JSON-folded LaTeX case: \boxed → \x08oxed
    out = parse_output(
        role="formatter", node_id="final_extractor", dataset="math",
        raw_text="\x08oxed{47}",
    )
    assert out["final_answer"] == "47"


def test_humaneval_programmer_extracts_fenced_code() -> None:
    out = parse_output(
        role="programmer", node_id="programmer", dataset="humaneval",
        raw_text="Sure!\n```python\ndef foo():\n    return 1\n```\n",
    )
    assert "def foo()" in out["code"]
    assert out["code"] == out["final_answer"]


def test_hotpotqa_solver_emphasizes_question_unknown() -> None:
    sys, user, want_json = build_messages(
        role="solver", node_id="answerer", dataset="hotpotqa",
        payload={"task_input": {"task": "Who...?", "input": {"question": "Who is X?", "context": [["T", ["s"]]]}}},
    )
    assert want_json is False
    assert "QUESTION" in user
    assert "CONTEXT" in user
    assert "verify" in sys.lower()


def test_drop_formatter_returns_json() -> None:
    sys, _u, want_json = build_messages(
        role="formatter", node_id="answer_formatter", dataset="drop",
        payload=_payload("How many yards?"),
    )
    assert want_json is True
    assert "DROP" in sys
