"""Reviewer.

The reviewer evaluates a single candidate answer against the
`EvalContract`. A deterministic grader (if available) clamps the
reviewer's own confidence — an LLM reviewer cannot override a grader.

We support two review paths:
- `deterministic_review(result, contract, grader_output)` — when a
  deterministic grader has already run; returns a structured verdict with
  `valid`, `confidence`, `detected_issues`, `repair_request`.
- `llm_review(...)` — when only a rubric / none is available; returns a
  verdict seeded by a provided LLM summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcoop.core.schema import EvalContract, NodeResult


@dataclass
class ReviewVerdict:
    valid: bool
    confidence: float
    detected_issues: list[str]
    repair_request: str | None
    final_answer: Any
    provenance: str  # "deterministic" or "llm_rubric"


def deterministic_review(
    candidate: NodeResult,
    contract: EvalContract,
    grader_output: dict[str, Any],
) -> ReviewVerdict:
    """`grader_output` must include `ok: bool`, `confidence: float`, and
    optional `issues: list[str]`.
    """
    grader_ok = bool(grader_output.get("ok", False))
    grader_conf = float(grader_output.get("confidence", 1.0 if grader_ok else 0.0))
    reviewer_conf = float(candidate.confidence or 0.5)

    # Reviewer can never exceed grader confidence when the grader is
    # deterministic. This is the §5-Phase-5 clamp from instructions.md.
    final_conf = min(reviewer_conf, grader_conf)
    issues = list(grader_output.get("issues", []))
    if not grader_ok:
        issues.append("deterministic_grader_rejected")
    return ReviewVerdict(
        valid=grader_ok,
        confidence=final_conf,
        detected_issues=issues,
        repair_request="rerun_with_error_log" if not grader_ok else None,
        final_answer=candidate.output.get("final_answer") or candidate.output.get("answer"),
        provenance="deterministic",
    )


def llm_review(
    candidate: NodeResult,
    contract: EvalContract,
    llm_verdict: dict[str, Any],
) -> ReviewVerdict:
    valid = bool(llm_verdict.get("valid", False))
    confidence = float(llm_verdict.get("confidence", 0.5))
    issues = list(llm_verdict.get("detected_issues", []))
    if contract.required_evidence and not candidate.evidence:
        issues.append("missing_required_evidence")
        valid = False
        confidence = min(confidence, 0.3)
    return ReviewVerdict(
        valid=valid,
        confidence=confidence,
        detected_issues=issues,
        repair_request=llm_verdict.get("repair_request"),
        final_answer=candidate.output.get("final_answer") or candidate.output.get("answer"),
        provenance="llm_rubric",
    )


def review(
    candidate: NodeResult,
    contract: EvalContract,
    *,
    grader_output: dict[str, Any] | None = None,
    llm_verdict: dict[str, Any] | None = None,
) -> ReviewVerdict:
    """Policy: deterministic grader dominates whenever present."""
    if grader_output is not None and contract.grader in ("pytest", "exact_match", "deterministic"):
        return deterministic_review(candidate, contract, grader_output)
    return llm_review(candidate, contract, llm_verdict or {})


__all__ = ["review", "deterministic_review", "llm_review", "ReviewVerdict"]
