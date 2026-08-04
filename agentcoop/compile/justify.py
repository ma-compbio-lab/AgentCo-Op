"""Admissibility: does every decision in this candidate cite real evidence?

A candidate that contains an inadmissible decision is **rejected**, not
annotated after the fact. That ordering is the entire difference between
evidence-guided synthesis and a post-hoc rationale: if the compiler could
proceed without the evidence, the evidence was decorative.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.candidates import CandidateWorkflow
from agentcoop.compile.grammar import RuleContext
from agentcoop.ir.evidence import EvidenceLedger
from agentcoop.ir.workflow import atomics


class JustificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    admissible: bool
    ledger: EvidenceLedger
    rejections: list[str] = Field(default_factory=list)
    #: Fraction of decisions with load-bearing evidence.
    evidence_coverage: float = 0.0
    #: Fraction backed by something actually executed rather than derived.
    observed_fraction: float = 0.0


def justify(candidate: CandidateWorkflow, ctx: RuleContext) -> JustificationResult:
    ledger = candidate.ledger
    rejections: list[str] = []

    for record in ledger.inadmissible():
        reason = record.rejection_reason()
        if reason:
            rejections.append(reason)

    # Every component instance in the term must have a bind record. A node
    # nobody justified is exactly the failure mode this module exists to catch.
    justified_targets = {r.target for r in ledger.records.values()}
    for atom in atomics(candidate.term):
        target = f"{atom.subgoal_id}__{atom.component}"
        if target not in justified_targets and atom.term_id not in justified_targets:
            rejections.append(
                f"node '{atom.term_id}' binds '{atom.component}' with no design "
                "evidence record"
            )

    for subgoal_id, component in sorted(candidate.bindings.items()):
        if ctx.dossier.subgoal(subgoal_id) is None:
            rejections.append(
                f"binding references unknown subgoal '{subgoal_id}' -> '{component}'"
            )

    return JustificationResult(
        candidate_id=candidate.candidate_id,
        admissible=not rejections,
        ledger=ledger,
        rejections=rejections,
        evidence_coverage=ledger.evidence_coverage(),
        observed_fraction=ledger.observed_fraction(),
    )


__all__ = ["JustificationResult", "justify"]
