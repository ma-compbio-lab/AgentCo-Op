"""Design evidence — why each node and edge exists.

This module is the direct answer to the objection that workflow structure was
"imagined by an LLM". Every structural decision the compiler makes must carry
a :class:`DesignEvidenceRecord`, and a record is only *admissible* if it cites
evidence of the required kinds. The compiler refuses to emit an unjustified
decision rather than annotating it after the fact.

Four evidence kinds, each with a different burden of proof:

``REQUIREMENT``
    A subgoal, invariant, or required output makes this decision necessary.
    Derived from the dossier — cheap and reliable.
``CAPABILITY``
    A component demonstrably can do the job. Must cite a probe outcome or a
    reliability history, not a README.
``COORDINATION``
    Two components genuinely need to be connected, run in parallel, or
    joined. Must cite a typed data dependency, an independence analysis, or
    a registered merge algebra.
``OUTCOME``
    Prior runs support this choice. Must cite recorded statistics.

An LLM may *propose* a decision. It may never be the sole evidence for one.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(str, Enum):
    REQUIREMENT = "requirement"
    CAPABILITY = "capability"
    COORDINATION = "coordination"
    OUTCOME = "outcome"


class EvidenceStrength(str, Enum):
    """How much weight a piece of evidence can bear.

    ``ASSERTED`` exists so that LLM proposals can be *recorded* without being
    mistaken for support. Admissibility policies ignore asserted evidence
    when counting whether a decision is justified.
    """

    ASSERTED = "asserted"       # someone (or some model) said so
    DOCUMENTED = "documented"   # written in a manifest / README / paper
    DERIVED = "derived"         # follows mechanically from the dossier or types
    OBSERVED = "observed"       # a probe or a real run produced this
    STATISTICAL = "statistical" # aggregated over multiple observations


#: Strengths that count toward justification. Everything weaker is advisory.
LOAD_BEARING: frozenset[EvidenceStrength] = frozenset(
    {EvidenceStrength.DERIVED, EvidenceStrength.OBSERVED, EvidenceStrength.STATISTICAL}
)


class EvidenceItem(BaseModel):
    """One citable fact supporting a design decision."""

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    strength: EvidenceStrength
    claim: str
    #: Where this came from: a probe id, a subgoal id, a run id, a converter
    #: name, a statistics key. Must be resolvable by a reader.
    source_ref: str
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def load_bearing(self) -> bool:
        return self.strength in LOAD_BEARING


class DecisionKind(str, Enum):
    BIND_COMPONENT = "bind_component"
    ADD_EDGE = "add_edge"
    PARALLELIZE = "parallelize"
    JOIN = "join"
    ADD_VERIFIER = "add_verifier"
    ADD_ADAPTER = "add_adapter"
    ADD_FALLBACK = "add_fallback"
    ADD_HUMAN_GATE = "add_human_gate"
    #: Recorded when the compiler deliberately declines to add structure.
    #: "We considered a second agent and rejected it" is itself a result.
    DECLINE_STRUCTURE = "decline_structure"


class Alternative(BaseModel):
    """A candidate that was considered and rejected, with the reason."""

    model_config = ConfigDict(extra="forbid")

    description: str
    rejection_reason: str
    #: Evidence supporting the rejection, where available.
    evidence: list[EvidenceItem] = Field(default_factory=list)


#: Evidence kinds each decision kind must cite to be admissible.
REQUIRED_EVIDENCE: dict[DecisionKind, set[EvidenceKind]] = {
    DecisionKind.BIND_COMPONENT: {EvidenceKind.REQUIREMENT, EvidenceKind.CAPABILITY},
    DecisionKind.ADD_EDGE: {EvidenceKind.REQUIREMENT, EvidenceKind.COORDINATION},
    DecisionKind.PARALLELIZE: {EvidenceKind.COORDINATION},
    DecisionKind.JOIN: {EvidenceKind.COORDINATION},
    DecisionKind.ADD_VERIFIER: {EvidenceKind.REQUIREMENT},
    DecisionKind.ADD_ADAPTER: {EvidenceKind.COORDINATION},
    DecisionKind.ADD_FALLBACK: {EvidenceKind.OUTCOME},
    DecisionKind.ADD_HUMAN_GATE: {EvidenceKind.REQUIREMENT},
    DecisionKind.DECLINE_STRUCTURE: {EvidenceKind.REQUIREMENT},
}


class DesignEvidenceRecord(BaseModel):
    """The justification attached to one structural decision."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    decision_kind: DecisionKind
    decision: str
    #: Node id, edge id, or term id this record justifies.
    target: str
    #: Subgoal this decision serves, when applicable.
    subgoal_id: Optional[str] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    #: Set when an LLM proposed the decision. Recorded for transparency; it
    #: never counts toward admissibility.
    proposed_by: Optional[str] = None

    # -- admissibility ------------------------------------------------------

    def kinds_present(self, *, load_bearing_only: bool = True) -> set[EvidenceKind]:
        items = [e for e in self.evidence if e.load_bearing] if load_bearing_only else self.evidence
        return {e.kind for e in items}

    def missing_evidence_kinds(self) -> set[EvidenceKind]:
        required = REQUIRED_EVIDENCE.get(self.decision_kind, set())
        return required - self.kinds_present()

    @property
    def admissible(self) -> bool:
        """True iff every required evidence kind is present *and* load-bearing."""
        return not self.missing_evidence_kinds()

    def rejection_reason(self) -> Optional[str]:
        missing = self.missing_evidence_kinds()
        if not missing:
            return None
        return (
            f"decision '{self.decision_id}' ({self.decision_kind.value}) lacks "
            f"load-bearing evidence of kind(s): {', '.join(sorted(k.value for k in missing))}"
        )

    def strongest(self, kind: EvidenceKind) -> Optional[EvidenceItem]:
        order = {
            EvidenceStrength.STATISTICAL: 4,
            EvidenceStrength.OBSERVED: 3,
            EvidenceStrength.DERIVED: 2,
            EvidenceStrength.DOCUMENTED: 1,
            EvidenceStrength.ASSERTED: 0,
        }
        candidates = [e for e in self.evidence if e.kind == kind]
        if not candidates:
            return None
        return max(candidates, key=lambda e: order[e.strength])


class EvidenceLedger(BaseModel):
    """All design evidence for one compiled workflow."""

    model_config = ConfigDict(extra="forbid")

    records: dict[str, DesignEvidenceRecord] = Field(default_factory=dict)

    def add(self, record: DesignEvidenceRecord) -> DesignEvidenceRecord:
        self.records[record.decision_id] = record
        return record

    def for_target(self, target: str) -> list[DesignEvidenceRecord]:
        return [r for r in self.records.values() if r.target == target]

    def inadmissible(self) -> list[DesignEvidenceRecord]:
        return [r for r in self.records.values() if not r.admissible]

    @property
    def fully_justified(self) -> bool:
        return not self.inadmissible()

    def evidence_coverage(self) -> float:
        """Fraction of decisions that are admissibly justified.

        Reported as a utility dimension so a workflow that runs but cannot
        explain itself scores worse than one that can.
        """
        if not self.records:
            return 0.0
        return sum(1 for r in self.records.values() if r.admissible) / len(self.records)

    def observed_fraction(self) -> float:
        """Fraction of decisions backed by at least one OBSERVED/STATISTICAL item.

        Distinguishes "derivable from the spec" from "we actually ran it".
        """
        if not self.records:
            return 0.0
        strong = 0
        for r in self.records.values():
            if any(
                e.strength in (EvidenceStrength.OBSERVED, EvidenceStrength.STATISTICAL)
                for e in r.evidence
            ):
                strong += 1
        return strong / len(self.records)

    def summary_rows(self) -> list[dict[str, Any]]:
        """Flat rows for the human-readable design report."""
        rows = []
        for r in sorted(self.records.values(), key=lambda x: x.decision_id):
            rows.append(
                {
                    "decision_id": r.decision_id,
                    "kind": r.decision_kind.value,
                    "target": r.target,
                    "subgoal": r.subgoal_id or "",
                    "admissible": r.admissible,
                    "evidence_kinds": sorted(k.value for k in r.kinds_present()),
                    "n_alternatives": len(r.alternatives),
                    "proposed_by": r.proposed_by or "compiler",
                }
            )
        return rows


# ---------------------------------------------------------------------------
# Constructors for the common, mechanically-derivable evidence items
# ---------------------------------------------------------------------------


def requirement_evidence(subgoal_id: str, claim: str, **data: Any) -> EvidenceItem:
    """Evidence derived from the dossier. Always DERIVED — it follows from the spec."""
    return EvidenceItem(
        kind=EvidenceKind.REQUIREMENT,
        strength=EvidenceStrength.DERIVED,
        claim=claim,
        source_ref=f"subgoal:{subgoal_id}",
        data=dict(data),
    )


def capability_evidence(
    component: str, probe_id: str, claim: str, *, observed: bool = True, **data: Any
) -> EvidenceItem:
    """Evidence that a component can actually do something.

    ``observed=False`` downgrades to DOCUMENTED, which is *not* load-bearing —
    exactly the point: a README claim cannot justify binding a component.
    """
    return EvidenceItem(
        kind=EvidenceKind.CAPABILITY,
        strength=EvidenceStrength.OBSERVED if observed else EvidenceStrength.DOCUMENTED,
        claim=claim,
        source_ref=f"probe:{component}:{probe_id}",
        data=dict(data),
    )


def coordination_evidence(claim: str, source_ref: str, **data: Any) -> EvidenceItem:
    """Evidence that two components should be connected in a particular way."""
    return EvidenceItem(
        kind=EvidenceKind.COORDINATION,
        strength=EvidenceStrength.DERIVED,
        claim=claim,
        source_ref=source_ref,
        data=dict(data),
    )


def outcome_evidence(claim: str, source_ref: str, n: int, **data: Any) -> EvidenceItem:
    """Evidence from recorded history. STATISTICAL once there is real support."""
    return EvidenceItem(
        kind=EvidenceKind.OUTCOME,
        strength=EvidenceStrength.STATISTICAL if n >= 3 else EvidenceStrength.ASSERTED,
        claim=claim,
        source_ref=source_ref,
        data={"n_observations": n, **data},
    )


def asserted_evidence(kind: EvidenceKind, claim: str, source_ref: str) -> EvidenceItem:
    """An LLM's or a human's unverified assertion. Recorded, never load-bearing."""
    return EvidenceItem(
        kind=kind,
        strength=EvidenceStrength.ASSERTED,
        claim=claim,
        source_ref=source_ref,
    )


__all__ = [
    "EvidenceKind",
    "EvidenceStrength",
    "LOAD_BEARING",
    "EvidenceItem",
    "DecisionKind",
    "Alternative",
    "REQUIRED_EVIDENCE",
    "DesignEvidenceRecord",
    "EvidenceLedger",
    "requirement_evidence",
    "capability_evidence",
    "coordination_evidence",
    "outcome_evidence",
    "asserted_evidence",
]
