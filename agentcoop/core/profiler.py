"""Task profiler.

Hybrid rules + optional LLM-structured-output. Rules alone are enough to
profile standard-benchmark tasks in a deterministic way; the LLM layer is
available when the backend is wired.

Design note: we never persist hidden chain-of-thought. The LLM layer may
only return `rationale_summary` (one paragraph).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from agentcoop.core.schema import Budget, TaskProfile


CODE_HINTS = re.compile(
    r"\b(implement|bug|fix|function|class|write\s+(?:a\s+)?(?:python|code|program)|"
    r"humaneval|mbpp|def\s+\w+\s*\()\b",
    re.IGNORECASE,
)
MATH_HINTS = re.compile(
    r"(?:\b(?:equation|solve\s+for|integer|prime|modulo|probability|geometry|"
    r"triangle|gsm8k|math\s+problem)\b"
    r"|=\s*[-0-9]+"
    r"|\b\d+\s*[-+*/×÷]\s*\d+\b"
    r"|\bwhat\s+is\s+\d)",
    re.IGNORECASE,
)
QA_HINTS = re.compile(
    r"\b(who|when|where|which|what\s+year|multi-hop|hotpotqa|cite|reference)\b",
    re.IGNORECASE,
)
DROP_HINTS = re.compile(
    r"\b(how\s+many|how\s+much|difference\s+between|total\s+of|sum\s+of|drop)\b",
    re.IGNORECASE,
)
REPO_HINTS = re.compile(
    r"\b(github\.com/|biodiscoveryagent|spatialagent|install|clone|run\s+the\s+repo|"
    r"dockerfile|docker\s+run|entrypoint)\b",
    re.IGNORECASE,
)
RETRIEVAL_HINTS = re.compile(
    r"\b(latest|recent|paper|citation|external\s+source|search\s+the\s+web|"
    r"documentation|api\s+docs)\b",
    re.IGNORECASE,
)
TOOL_HINTS = re.compile(
    r"\b(calculator|compute|execute|run\s+code|python\s+interpreter|shell|fetch|http)\b",
    re.IGNORECASE,
)
UNIT_TEST_HINTS = re.compile(
    r"\b(unit\s+tests?|assert\s+|test\s+cases?|test_\w+|pytest|check\()\b",
    re.IGNORECASE,
)
HIGH_RISK_HINTS = re.compile(
    r"\b(rm\s+-rf|delete\s+file|uninstall|shutdown|curl\s+.*\|\s*sh|"
    r"upload\s+to|publish\s+to|post\s+to\s+twitter)\b",
    re.IGNORECASE,
)


@dataclass
class ProfilerResult:
    profile: TaskProfile
    rule_signals: dict[str, Any]


def _difficulty_from_signals(decomp: float, tool: float, retrieval: float) -> str:
    score = decomp * 0.5 + tool * 0.2 + retrieval * 0.3
    if score < 0.15:
        return "trivial"
    if score < 0.35:
        return "simple"
    if score < 0.6:
        return "moderate"
    if score < 0.85:
        return "complex"
    return "open_ended"


DATASET_PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    # Locked-in profile shape per AFlow-aligned dataset. The runner passes
    # `dataset` so we can route deterministically — relying purely on regex
    # signals over short prompts misclassified ~30% of MATH / HotpotQA tasks
    # in the smoke run. These overrides keep the compiler picking the right
    # meta-skill (math_specialist_route / retrieval_grounded_qa /
    # code_test_repair_loop / numeric_reading_comprehension).
    "gsm8k": {
        # GSM8K is grade-school arithmetic — single_agent_tool_use (L1) /
        # simple_direct_answer (L0) are the right targets. Marking it
        # `simple` keeps complex math_specialist_route off the table where
        # an over-aggressive verifier was harming correct answers.
        "domain": ["math", "gsm8k", "arithmetic"],
        "answer_type": "short_answer",
        "verification_available": "exact",
        "difficulty": "simple",
        "retrieval_need": 0.0,
        "decomposition_need": 0.35,
        "tool_need": 0.25,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
    "math": {
        "domain": ["math", "math-benchmark", "competition"],
        "answer_type": "short_answer",
        "verification_available": "exact",
        "difficulty": "complex",
        "retrieval_need": 0.0,
        "decomposition_need": 0.7,
        "tool_need": 0.35,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
    "humaneval": {
        "domain": ["code", "humaneval"],
        "answer_type": "program",
        "verification_available": "unit_test",
        "difficulty": "moderate",
        "retrieval_need": 0.0,
        "decomposition_need": 0.55,
        "tool_need": 0.75,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
    "mbpp": {
        "domain": ["code", "mbpp"],
        "answer_type": "program",
        "verification_available": "unit_test",
        "difficulty": "moderate",
        "retrieval_need": 0.0,
        "decomposition_need": 0.55,
        "tool_need": 0.75,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
    "hotpotqa": {
        # AFlow-aligned HotpotQA tasks ship the multi-hop context inline,
        # so no external retrieval is needed — keeping the retriever node
        # added a no-op MCP step + 6 LLM nodes that compounded errors. We
        # let the compiler choose a simpler chain (single_agent_tool_use /
        # simple_direct_answer) that focuses the model on the question.
        "domain": ["qa", "hotpot", "multi-hop"],
        "answer_type": "short_answer",
        "verification_available": "rubric",
        "difficulty": "simple",
        "retrieval_need": 0.2,
        "decomposition_need": 0.4,
        "tool_need": 0.2,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
    "drop": {
        "domain": ["qa", "reading-comprehension", "drop"],
        "answer_type": "short_answer",
        "verification_available": "exact",
        "difficulty": "moderate",
        "retrieval_need": 0.0,
        "decomposition_need": 0.55,
        "tool_need": 0.4,
        "repo_execution_need": 0.0,
        "risk_level": "low",
    },
}


def profile_task(
    raw_task: str,
    *,
    task_id: str | None = None,
    constraints: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    budget: Budget | None = None,
    dataset: str | None = None,
) -> ProfilerResult:
    """Rules-only profiling. Returns a TaskProfile and a dict of raw signals.

    `dataset`, when given, applies a deterministic profile override that
    matches the AFlow-aligned benchmark family (see `DATASET_PROFILE_OVERRIDES`).
    """

    text = raw_task or ""
    domain: list[str] = []
    answer_type = "short_answer"
    objective = "solve"

    code_hit = bool(CODE_HINTS.search(text))
    math_hit = bool(MATH_HINTS.search(text))
    qa_hit = bool(QA_HINTS.search(text))
    drop_hit = bool(DROP_HINTS.search(text))
    repo_hit = bool(REPO_HINTS.search(text))
    retrieval_hit = bool(RETRIEVAL_HINTS.search(text))
    tool_hit = bool(TOOL_HINTS.search(text))
    unit_test_hit = bool(UNIT_TEST_HINTS.search(text))
    high_risk_hit = bool(HIGH_RISK_HINTS.search(text))

    if code_hit:
        domain.append("code")
        answer_type = "program"
    if math_hit and not code_hit:
        domain.append("math")
    if qa_hit and not code_hit and not math_hit:
        domain.append("qa")
    if drop_hit:
        if "qa" not in domain:
            domain.append("qa")
        domain.append("reading-comprehension")
    if repo_hit:
        domain.append("repo")
    if not domain:
        domain = ["general"]

    tool_need = 0.2
    if tool_hit:
        tool_need = 0.6
    if code_hit:
        tool_need = max(tool_need, 0.7)
    if drop_hit:
        tool_need = max(tool_need, 0.5)

    retrieval_need = 0.0
    if retrieval_hit or qa_hit:
        retrieval_need = 0.6
    if "hotpot" in text.lower() or "multi-hop" in text.lower():
        retrieval_need = 0.9
    # DROP-style passages are in-prompt; they don't need external retrieval
    # unless the task is also a multi-hop QA.
    if drop_hit and not qa_hit and not retrieval_hit:
        retrieval_need = 0.0

    repo_execution_need = 0.9 if repo_hit else 0.0

    decomposition_need = 0.2
    if repo_hit:
        decomposition_need = 0.7
    elif code_hit or qa_hit or drop_hit:
        decomposition_need = 0.5

    if unit_test_hit or code_hit:
        verification_available = "unit_test"
    elif math_hit:
        verification_available = "exact"
    elif drop_hit:
        verification_available = "exact"
    elif qa_hit:
        verification_available = "rubric"
    else:
        verification_available = "none"

    risk_level = "high" if high_risk_hit else ("medium" if repo_hit else "low")

    difficulty = _difficulty_from_signals(decomposition_need, tool_need, retrieval_need)

    profile_kwargs: dict[str, Any] = dict(
        task_id=task_id or f"task-{uuid.uuid4().hex[:8]}",
        raw_task=text,
        domain=domain,
        answer_type=answer_type,
        objective=objective,
        difficulty=difficulty,
        decomposition_need=decomposition_need,
        tool_need=tool_need,
        retrieval_need=retrieval_need,
        repo_execution_need=repo_execution_need,
        verification_available=verification_available,
        risk_level=risk_level,
        budget=budget or Budget(),
        output_schema=output_schema,
        constraints=list(constraints or []),
    )
    if dataset:
        override = DATASET_PROFILE_OVERRIDES.get(str(dataset).lower())
        if override:
            profile_kwargs.update(override)
    profile = TaskProfile(**profile_kwargs)

    signals = {
        "code_hit": code_hit,
        "math_hit": math_hit,
        "qa_hit": qa_hit,
        "drop_hit": drop_hit,
        "repo_hit": repo_hit,
        "retrieval_hit": retrieval_hit,
        "tool_hit": tool_hit,
        "unit_test_hit": unit_test_hit,
        "high_risk_hit": high_risk_hit,
    }
    return ProfilerResult(profile=profile, rule_signals=signals)


async def profile_task_with_llm(
    raw_task: str,
    backend,
    *,
    task_id: str | None = None,
    constraints: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    budget: Budget | None = None,
) -> ProfilerResult:
    """Start from rules, then ask the LLM to refine and add a rationale_summary.

    The backend must accept `execute(node, payload, context)` matching the
    protocol in `agentcoop.backends.base.Backend`. We wrap it in a dummy
    NodeSpec so the same MockLLM canned-response path works.
    """
    from agentcoop.core.schema import NodeSpec
    from agentcoop.backends.base import NodeContext

    rule_result = profile_task(
        raw_task,
        task_id=task_id,
        constraints=constraints,
        output_schema=output_schema,
        budget=budget,
    )

    node = NodeSpec(node_id="profiler", role="router", backend="llm")
    ctx = NodeContext(run_id="profile", task_id=rule_result.profile.task_id)
    result = await backend.execute(
        node,
        {"task": raw_task, "rule_signals": rule_result.rule_signals},
        ctx,
    )
    data = result.output or {}

    # Only accept refinements; never overwrite task_id or raw_task.
    refined = rule_result.profile.model_copy(update={
        k: data[k]
        for k in (
            "domain",
            "answer_type",
            "objective",
            "difficulty",
            "decomposition_need",
            "tool_need",
            "retrieval_need",
            "repo_execution_need",
            "verification_available",
            "risk_level",
        )
        if k in data
    })
    if "rationale_summary" in data:
        # Strip hidden reasoning defensively: keep only the first paragraph.
        summary = str(data["rationale_summary"]).split("\n\n", 1)[0].strip()
        refined = refined.model_copy(update={"rationale_summary": summary})
    return ProfilerResult(profile=refined, rule_signals=rule_result.rule_signals)


__all__ = ["profile_task", "profile_task_with_llm", "ProfilerResult"]
