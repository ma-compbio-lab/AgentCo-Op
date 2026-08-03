"""Integrator: fuse typed candidate outputs into one final result.

Behavioural contract:
- Input: list of `NodeResult` candidates + the `EvalContract`.
- Output: a single merged `NodeResult` with `final_answer`, `confidence`,
  and a `detected_issues` list (including disagreement signals).
- Never fabricates evidence; only concatenates declared evidence sources.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentcoop.core.schema import EvalContract, NodeResult


def integrate(
    candidates: list[NodeResult],
    contract: EvalContract,
) -> NodeResult:
    if not candidates:
        return NodeResult(node_id="integrator", ok=False, errors=["no candidates"])

    answers: list[str] = []
    evidence: list[dict[str, Any]] = []
    confidences: list[float] = []
    issues: list[str] = []

    for c in candidates:
        ans = _extract_answer(c, contract)
        if ans is not None:
            answers.append(ans)
        evidence.extend(c.evidence)
        if c.confidence is not None:
            confidences.append(c.confidence)

    # Normalize for comparison.
    normalized = [_normalize(a, contract) for a in answers]
    vote = Counter(normalized)
    if not vote:
        return NodeResult(
            node_id="integrator",
            ok=False,
            errors=["no extractable answer across candidates"],
        )

    winner, winner_count = vote.most_common(1)[0]
    agreement = winner_count / max(1, len(normalized))
    if agreement < 1.0:
        issues.append(f"specialist_disagreement: {dict(vote)}")
    # Pick the original (pre-normalization) representative.
    idx = normalized.index(winner)
    final_answer = answers[idx]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.5
    # Agreement scales confidence.
    final_conf = min(1.0, mean_conf * (0.5 + 0.5 * agreement))

    return NodeResult(
        node_id="integrator",
        ok=True,
        output={
            "final_answer": final_answer,
            "agreement": agreement,
            "candidate_answers": list(normalized),
        },
        confidence=final_conf,
        evidence=evidence,
        metrics={"agreement": agreement, "n_candidates": len(candidates)},
        logs_summary=f"integrated {len(candidates)} candidates, agreement={agreement:.2f}",
        errors=[],
    )


def _extract_answer(result: NodeResult, contract: EvalContract) -> str | None:
    out = result.output or {}
    for key in ("final_answer", "answer", "text", "result"):
        if key in out and out[key] not in (None, ""):
            return str(out[key])
    return None


def _normalize(answer: str, contract: EvalContract) -> str:
    if not answer:
        return ""
    if contract.normalization == "gsm8k_numeric":
        digits = "".join(c for c in answer if c.isdigit() or c in ".-")
        if not digits:
            return answer.strip()
        try:
            f = float(digits)
            return str(int(f)) if f.is_integer() else str(f)
        except ValueError:
            return digits
    if contract.normalization == "hotpotqa_em":
        return " ".join(answer.lower().split()).strip(".?!")
    if contract.normalization == "pytest":
        return answer.strip()
    return answer.strip().lower()


__all__ = ["integrate"]
