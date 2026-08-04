"""Claim bundles — the structured scientific output of a run.

A workflow that "ran to completion" has produced nothing scientific. What a
reviewer needs is a *claim* together with the machinery required to attack it:
the evidence for it, the evidence against it, what had to be assumed, what was
varied to see whether the conclusion moved, which competing explanations were
considered, how uncertain the conclusion is, and which raw inputs it is
ultimately derived from.

This module makes that object typed and checkable, for three reasons.

1. **A claim without load-bearing evidence is an assertion.** ``EvidenceRef``
   carries the same :class:`~agentcoop.ir.evidence.EvidenceStrength` ladder the
   compiler uses, defaulting to ``ASSERTED``. A bundle whose entire support is
   asserted is ill-formed — it does not become a finding because a model wrote
   it down confidently.
2. **Selective reporting is a defect, not a style choice.** If a sensitivity
   analysis destabilised the conclusion, or contradicting evidence exists, the
   bundle has to carry that somewhere a reader will see it. The well-formedness
   rules below refuse a bundle that found instability and then presented only
   support.
3. **Traceability is mechanical, not rhetorical.** :func:`trace_claim` walks
   ``Artifact.derived_from`` backwards from the claim's provenance to the raw
   inputs and reports exactly which required artifact types were reached. A
   claim that cannot be walked back to its inputs is untraceable regardless of
   how convincing its prose is.

Everything here is deterministic and offline: no model is consulted, no clock
is read, and every list returned is sorted or in declaration order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Artifact, lineage_of
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import EvidenceChainRequirement
from agentcoop.ir.evidence import LOAD_BEARING, EvidenceStrength


# ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


class EvidenceRefKind(str, Enum):
    """What sort of thing is being cited.

    Kept separate from :class:`~agentcoop.ir.checks.SignalSource` (*who*
    produced it) and :class:`~agentcoop.ir.evidence.EvidenceStrength` (*how
    much weight it can bear*), because those three are genuinely independent:
    a literature citation can be summarised by a model (weak) or extracted by a
    deterministic parser (stronger), and both are still literature.
    """

    ARTIFACT = "artifact"          # a value computed by the workflow itself
    CHECK = "check"                # a CheckResult emitted by the contract stack
    STATISTIC = "statistic"        # a test statistic, effect size, interval
    LITERATURE = "literature"      # a retrieved external reference
    TOOL = "tool"                  # an external database / analysis tool
    EXPERT = "expert"              # a human annotation
    MODEL_ASSERTION = "model_assertion"  # an LLM said so


class EvidenceRef(BaseModel):
    """One citable item for or against a claim.

    ``strength`` defaults to ``ASSERTED`` on purpose. Support has to be
    *claimed* explicitly by whoever recorded it, and the well-formedness rules
    below then check that claim against ``source``: an LLM judgment can never
    be load-bearing no matter what strength the caller writes down.
    """

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    kind: EvidenceRefKind
    statement: str = ""
    source: SignalSource = SignalSource.DETERMINISTIC
    strength: EvidenceStrength = EvidenceStrength.ASSERTED
    #: Artifacts this evidence was computed from. Used by :func:`trace_claim`
    #: to confirm the evidence lives inside the claim's own lineage rather
    #: than having been imported from somewhere unaccounted for.
    artifact_ids: list[str] = Field(default_factory=list)
    #: Check id, run id, DOI, accession — anything a reader can resolve.
    source_ref: str = ""
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def load_bearing(self) -> bool:
        return self.strength in LOAD_BEARING


class SensitivityKind(str, Enum):
    SENSITIVITY = "sensitivity"            # sweep a parameter / threshold
    NEGATIVE_CONTROL = "negative_control"  # the answer should vanish, and does
    ABLATION = "ablation"                  # remove a component / input
    RESEED = "reseed"                      # re-run under different seeds
    PERTURBATION = "perturbation"          # jitter the inputs


class SensitivityResult(BaseModel):
    """One robustness probe of the claim.

    ``executed`` exists so an analysis that was *planned but not run* can be
    recorded honestly instead of being silently omitted. An unexecuted analysis
    that nonetheless reports ``conclusion_stable=True`` is an ill-formed bundle,
    not a passing one — the same rule as "an unavailable evaluator is not a
    passing evaluator", applied at the claim level.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    kind: SensitivityKind = SensitivityKind.SENSITIVITY
    #: What was varied: a parameter name, a seed range, a removed input.
    parameter: str = ""
    #: Settings swept, stringified so the manifest diffs cleanly.
    variations: list[str] = Field(default_factory=list)
    #: Whether the claim survived this analysis. Only meaningful when executed.
    conclusion_stable: bool = False
    #: Largest change in the reported effect across the sweep, when quantified.
    max_effect_delta: Optional[float] = None
    executed: bool = True
    detail: str = ""
    #: ``EvidenceRef.ref_id`` values produced by this analysis.
    evidence_refs: list[str] = Field(default_factory=list)

    @property
    def destabilising(self) -> bool:
        """True when this analysis was actually run and the claim did not survive."""
        return self.executed and not self.conclusion_stable


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


class ClaimBundle(BaseModel):
    """A scientific conclusion plus everything needed to attack it."""

    model_config = ConfigDict(extra="forbid")

    claim: str = ""
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceRef] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    sensitivity_analyses: list[SensitivityResult] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    #: Artifact ids the claim is read off. Roots of the traceability walk.
    provenance: list[str] = Field(default_factory=list)
    #: Identifier so several claims from one run stay distinguishable.
    claim_id: str = "claim"

    # -- convenience --------------------------------------------------------

    def all_evidence(self) -> list[EvidenceRef]:
        """Supporting then contradicting, in declaration order."""
        return list(self.supporting_evidence) + list(self.contradicting_evidence)

    def load_bearing_support(self) -> list[EvidenceRef]:
        """Supporting items that may actually carry the claim.

        An LLM judgment is excluded here even if the caller labelled it
        ``OBSERVED``; see :data:`_NEVER_LOAD_BEARING_SOURCES`.
        """
        return [
            ref
            for ref in self.supporting_evidence
            if ref.load_bearing and ref.source not in _NEVER_LOAD_BEARING_SOURCES
        ]

    def cited_artifact_ids(self) -> list[str]:
        seen: list[str] = []
        for ref in self.all_evidence():
            for aid in ref.artifact_ids:
                if aid not in seen:
                    seen.append(aid)
        return seen


#: Sources whose output can never be load-bearing, whatever strength was
#: recorded. Constraint 1 of the interface contract: anything a model produces
#: enters as ASSERTED, so a bundle claiming otherwise is mislabelled.
_NEVER_LOAD_BEARING_SOURCES: frozenset[SignalSource] = frozenset({SignalSource.LLM_JUDGE})

#: Uncertainty strings that declare nothing. Treated as absent, because
#: "n/a" in an uncertainty field is how an unqualified claim gets past a
#: presence check.
_VACUOUS_UNCERTAINTY: frozenset[str] = frozenset(
    {"", "-", "--", "n/a", "na", "none", "none.", "no uncertainty", "not applicable", "tbd"}
)


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


# ---------------------------------------------------------------------------
# Defects
# ---------------------------------------------------------------------------


class DefectSeverity(str, Enum):
    ERROR = "error"      # the bundle is not well-formed
    WARNING = "warning"  # admissible, but a reviewer should see it


class ClaimDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: DefectSeverity
    detail: str
    #: Ref id / analysis id the defect is about, when it is about one thing.
    subject: Optional[str] = None


class WellFormednessReport(BaseModel):
    """Structural verdict on a bundle. Errors mean "not a claim yet"."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    defects: list[ClaimDefect] = Field(default_factory=list)
    n_load_bearing_support: int = 0
    n_contradicting: int = 0
    n_executed_sensitivity: int = 0

    @property
    def errors(self) -> list[ClaimDefect]:
        return [d for d in self.defects if d.severity is DefectSeverity.ERROR]

    @property
    def warnings(self) -> list[ClaimDefect]:
        return [d for d in self.defects if d.severity is DefectSeverity.WARNING]

    @property
    def well_formed(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.well_formed:
            return f"claim '{self.claim_id}' is well-formed ({len(self.warnings)} warning(s))"
        return "; ".join(f"{d.code}: {d.detail}" for d in self.errors)


def check_wellformed(bundle: ClaimBundle) -> WellFormednessReport:
    """Structural audit of a claim bundle.

    The rules encode the failure modes that make an output look scientific
    without being scientific:

    ``CLAIM_EMPTY``
        There is no claim.
    ``NO_SUPPORTING_EVIDENCE`` / ``SUPPORT_NOT_LOAD_BEARING``
        Nothing cited, or everything cited is asserted rather than derived,
        observed, or statistical.
    ``LLM_EVIDENCE_OVERSTATED``
        A model judgment was recorded at a load-bearing strength. Recording
        the judgment is fine; promoting it is not.
    ``NO_PROVENANCE``
        Nothing to trace back to, so :func:`trace_claim` cannot run at all.
    ``UNCERTAINTY_NOT_DECLARED``
        Blank or vacuous uncertainty. An unqualified claim over an open-ended
        task is an overclaim.
    ``DUPLICATE_EVIDENCE_REF``
        The same ref id used twice, which makes counts and citations
        ambiguous — and lets one observation masquerade as two.
    ``CONTRADICTION_UNADDRESSED``
        Load-bearing contradicting evidence exists but the bundle offers no
        alternative explanation and no sensitivity analysis that engaged with
        it.
    ``INSTABILITY_NOT_REPORTED``
        A sensitivity analysis ran and destabilised the conclusion, yet the
        bundle records no contradicting evidence and no alternatives. This is
        selective reporting.
    ``UNEXECUTED_SENSITIVITY_CLAIMED_STABLE``
        An analysis that never ran is reported as supporting stability.
    """
    defects: list[ClaimDefect] = []

    if not bundle.claim.strip():
        defects.append(
            ClaimDefect(
                code="CLAIM_EMPTY",
                severity=DefectSeverity.ERROR,
                detail="bundle carries no claim text",
            )
        )

    load_bearing = bundle.load_bearing_support()
    if not bundle.supporting_evidence:
        defects.append(
            ClaimDefect(
                code="NO_SUPPORTING_EVIDENCE",
                severity=DefectSeverity.ERROR,
                detail="no supporting evidence is cited",
            )
        )
    elif not load_bearing:
        defects.append(
            ClaimDefect(
                code="SUPPORT_NOT_LOAD_BEARING",
                severity=DefectSeverity.ERROR,
                detail=(
                    "every supporting item is asserted or model-sourced; a claim "
                    "needs at least one derived, observed, or statistical item"
                ),
            )
        )

    for ref in bundle.all_evidence():
        if ref.source in _NEVER_LOAD_BEARING_SOURCES and ref.strength in LOAD_BEARING:
            defects.append(
                ClaimDefect(
                    code="LLM_EVIDENCE_OVERSTATED",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        f"evidence '{ref.ref_id}' comes from {ref.source.value} but is "
                        f"recorded as {ref.strength.value}; model output is ASSERTED"
                    ),
                    subject=ref.ref_id,
                )
            )

    seen: set[str] = set()
    for ref in bundle.all_evidence():
        if ref.ref_id in seen:
            defects.append(
                ClaimDefect(
                    code="DUPLICATE_EVIDENCE_REF",
                    severity=DefectSeverity.ERROR,
                    detail=f"evidence ref id '{ref.ref_id}' is used more than once",
                    subject=ref.ref_id,
                )
            )
        seen.add(ref.ref_id)

    if not bundle.provenance:
        defects.append(
            ClaimDefect(
                code="NO_PROVENANCE",
                severity=DefectSeverity.ERROR,
                detail="no provenance artifact ids; the claim cannot be traced to inputs",
            )
        )

    if not uncertainty_declared(bundle):
        defects.append(
            ClaimDefect(
                code="UNCERTAINTY_NOT_DECLARED",
                severity=DefectSeverity.ERROR,
                detail=(
                    f"uncertainty statement is blank or vacuous "
                    f"({bundle.uncertainty.strip()!r})"
                ),
            )
        )

    contradicting_load_bearing = [r for r in bundle.contradicting_evidence if r.load_bearing]
    engaged_refs = {
        ref for analysis in bundle.sensitivity_analyses for ref in analysis.evidence_refs
    }
    if contradicting_load_bearing and not has_alternatives(bundle):
        unengaged = [r.ref_id for r in contradicting_load_bearing if r.ref_id not in engaged_refs]
        if unengaged:
            defects.append(
                ClaimDefect(
                    code="CONTRADICTION_UNADDRESSED",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        "load-bearing contradicting evidence "
                        f"({', '.join(sorted(unengaged))}) is neither explained by an "
                        "alternative nor engaged with by a sensitivity analysis"
                    ),
                )
            )

    destabilising = [a for a in bundle.sensitivity_analyses if a.destabilising]
    if destabilising and not (bundle.contradicting_evidence or bundle.alternative_explanations):
        defects.append(
            ClaimDefect(
                code="INSTABILITY_NOT_REPORTED",
                severity=DefectSeverity.ERROR,
                detail=(
                    "sensitivity analyses "
                    f"({', '.join(sorted(a.analysis_id for a in destabilising))}) "
                    "destabilised the conclusion, but the bundle records neither "
                    "contradicting evidence nor an alternative explanation"
                ),
            )
        )

    for analysis in bundle.sensitivity_analyses:
        if not analysis.executed and analysis.conclusion_stable:
            defects.append(
                ClaimDefect(
                    code="UNEXECUTED_SENSITIVITY_CLAIMED_STABLE",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        f"sensitivity analysis '{analysis.analysis_id}' was not executed "
                        "but reports the conclusion as stable"
                    ),
                    subject=analysis.analysis_id,
                )
            )

    if not bundle.assumptions:
        defects.append(
            ClaimDefect(
                code="NO_ASSUMPTIONS_STATED",
                severity=DefectSeverity.WARNING,
                detail="no assumptions recorded; every analysis makes some",
            )
        )
    if not has_alternatives(bundle):
        defects.append(
            ClaimDefect(
                code="NO_ALTERNATIVE_EXPLANATIONS",
                severity=DefectSeverity.WARNING,
                detail="no alternative explanation was considered and recorded",
            )
        )
    if not any(a.executed for a in bundle.sensitivity_analyses):
        defects.append(
            ClaimDefect(
                code="NO_SENSITIVITY_EXECUTED",
                severity=DefectSeverity.WARNING,
                detail="no sensitivity analysis was executed for this claim",
            )
        )

    return WellFormednessReport(
        claim_id=bundle.claim_id,
        defects=defects,
        n_load_bearing_support=len(load_bearing),
        n_contradicting=len(bundle.contradicting_evidence),
        n_executed_sensitivity=sum(1 for a in bundle.sensitivity_analyses if a.executed),
    )


def has_alternatives(bundle: ClaimBundle) -> bool:
    """True iff at least one *distinct, non-vacuous* alternative was recorded.

    Restating the claim as its own alternative is the obvious way to satisfy a
    presence check without having considered anything, so it is normalised away.
    """
    claim_norm = _norm(bundle.claim)
    alternatives = {_norm(a) for a in bundle.alternative_explanations}
    alternatives.discard("")
    alternatives.discard(claim_norm)
    return bool(alternatives)


def uncertainty_declared(bundle: ClaimBundle) -> bool:
    """True iff the uncertainty field says something."""
    return _norm(bundle.uncertainty) not in _VACUOUS_UNCERTAINTY


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


class TraceabilityReport(BaseModel):
    """Where a claim actually comes from, computed by walking lineage.

    Every list is sorted so two runs over the same trace produce byte-identical
    reports.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    #: Full transitive closure of ``derived_from`` from the provenance roots.
    lineage: list[str] = Field(default_factory=list)
    #: Artifacts in the closure with no parents — the raw inputs reached.
    root_artifacts: list[str] = Field(default_factory=list)
    root_types: list[str] = Field(default_factory=list)
    #: Every artifact type appearing anywhere in the closure.
    lineage_types: list[str] = Field(default_factory=list)
    #: Provenance ids with no matching recorded artifact.
    dangling_provenance: list[str] = Field(default_factory=list)
    #: Ancestor ids named by ``derived_from`` that were never recorded. The
    #: chain claims a parent we have no evidence of, which is strictly worse
    #: than a short chain.
    broken_lineage_links: list[str] = Field(default_factory=list)
    #: Evidence refs citing artifacts outside the claim's own lineage.
    unlinked_evidence_refs: list[str] = Field(default_factory=list)
    #: Required artifact types (from the dossier evidence chain) not reached.
    missing_required_types: list[str] = Field(default_factory=list)
    #: Required sensitivity analyses missing, or present but never executed.
    missing_required_sensitivity: list[str] = Field(default_factory=list)

    @property
    def traceable(self) -> bool:
        return not (
            self.dangling_provenance
            or self.broken_lineage_links
            or self.unlinked_evidence_refs
            or self.missing_required_types
            or self.missing_required_sensitivity
        ) and bool(self.lineage) and bool(self.root_artifacts)

    def defects(self) -> list[ClaimDefect]:
        """Traceability failures as defects, so checks can reuse the wording."""
        out: list[ClaimDefect] = []
        if not self.lineage:
            out.append(
                ClaimDefect(
                    code="NO_LINEAGE",
                    severity=DefectSeverity.ERROR,
                    detail="no provenance artifact resolved; the claim has no lineage",
                )
            )
        for aid in self.dangling_provenance:
            out.append(
                ClaimDefect(
                    code="DANGLING_PROVENANCE",
                    severity=DefectSeverity.ERROR,
                    detail=f"provenance artifact '{aid}' was never recorded in the run",
                    subject=aid,
                )
            )
        for aid in self.broken_lineage_links:
            out.append(
                ClaimDefect(
                    code="BROKEN_LINEAGE_LINK",
                    severity=DefectSeverity.ERROR,
                    detail=f"artifact '{aid}' is cited as an ancestor but was never recorded",
                    subject=aid,
                )
            )
        for ref_id in self.unlinked_evidence_refs:
            out.append(
                ClaimDefect(
                    code="EVIDENCE_OUTSIDE_LINEAGE",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        f"evidence '{ref_id}' cites artifacts that are not in the claim's "
                        "provenance closure"
                    ),
                    subject=ref_id,
                )
            )
        for type_name in self.missing_required_types:
            out.append(
                ClaimDefect(
                    code="REQUIRED_TYPE_NOT_TRACED",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        f"the evidence chain requires the claim to trace to '{type_name}', "
                        "which does not appear in its lineage"
                    ),
                    subject=type_name,
                )
            )
        for analysis_id in self.missing_required_sensitivity:
            out.append(
                ClaimDefect(
                    code="REQUIRED_SENSITIVITY_MISSING",
                    severity=DefectSeverity.ERROR,
                    detail=(
                        f"required sensitivity analysis '{analysis_id}' was not executed "
                        "for this claim"
                    ),
                    subject=analysis_id,
                )
            )
        if not self.root_artifacts and self.lineage:
            out.append(
                ClaimDefect(
                    code="NO_RAW_INPUT_REACHED",
                    severity=DefectSeverity.ERROR,
                    detail="the lineage walk never reached an artifact without parents",
                )
            )
        return out


def trace_claim(
    bundle: ClaimBundle,
    artifacts: Mapping[str, Artifact],
    *,
    requirements: Sequence[EvidenceChainRequirement] = (),
) -> TraceabilityReport:
    """Walk the claim back to the raw inputs it rests on.

    ``artifacts`` is the run's artifact index (``Trace.artifacts``). The walk
    is over recorded lineage only: we never infer that two artifacts are
    related because their names look similar, because an inferred chain is
    exactly the kind of unfalsifiable provenance this system exists to avoid.
    """
    index = dict(artifacts)

    dangling = sorted({aid for aid in bundle.provenance if aid not in index})

    closure: set[str] = set()
    for aid in bundle.provenance:
        if aid in index:
            closure.update(lineage_of(aid, index))

    resolved = sorted(aid for aid in closure if aid in index)
    broken = sorted(aid for aid in closure if aid not in index)
    roots = sorted(aid for aid in resolved if not index[aid].derived_from)
    root_types = sorted({index[aid].type_name for aid in roots})
    lineage_types = sorted({index[aid].type_name for aid in resolved})

    unlinked = sorted(
        {
            ref.ref_id
            for ref in bundle.all_evidence()
            if any(aid not in closure for aid in ref.artifact_ids)
        }
    )

    required_types: set[str] = set()
    required_sensitivity: set[str] = set()
    for requirement in requirements:
        required_types.update(requirement.must_trace_to)
        required_sensitivity.update(requirement.required_sensitivity)

    missing_types = sorted(t for t in required_types if t not in set(lineage_types))

    executed_ids: set[str] = set()
    for analysis in bundle.sensitivity_analyses:
        if not analysis.executed:
            continue
        executed_ids.add(analysis.analysis_id)
        if analysis.parameter:
            executed_ids.add(analysis.parameter)
        executed_ids.add(analysis.kind.value)
    missing_sensitivity = sorted(s for s in required_sensitivity if s not in executed_ids)

    return TraceabilityReport(
        claim_id=bundle.claim_id,
        lineage=resolved,
        root_artifacts=roots,
        root_types=root_types,
        lineage_types=lineage_types,
        dangling_provenance=dangling,
        broken_lineage_links=broken,
        unlinked_evidence_refs=unlinked,
        missing_required_types=missing_types,
        missing_required_sensitivity=missing_sensitivity,
    )


class ClaimAudit(BaseModel):
    """Both halves of the claim audit, for the check layer and the report."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    wellformedness: WellFormednessReport
    traceability: TraceabilityReport

    @property
    def admissible(self) -> bool:
        """A claim is admissible only if it is both well-formed and traceable."""
        return self.wellformedness.well_formed and self.traceability.traceable

    def defects(self) -> list[ClaimDefect]:
        return list(self.wellformedness.defects) + self.traceability.defects()

    def error_codes(self) -> list[str]:
        return sorted({d.code for d in self.defects() if d.severity is DefectSeverity.ERROR})


def audit_claim(
    bundle: ClaimBundle,
    artifacts: Mapping[str, Artifact],
    *,
    requirements: Sequence[EvidenceChainRequirement] = (),
) -> ClaimAudit:
    """Convenience wrapper running both audits over one bundle."""
    return ClaimAudit(
        claim_id=bundle.claim_id,
        wellformedness=check_wellformed(bundle),
        traceability=trace_claim(bundle, artifacts, requirements=requirements),
    )


def raw_inputs(bundle: ClaimBundle, artifacts: Mapping[str, Artifact]) -> list[Artifact]:
    """Raw input artifacts (no parents) reachable from the claim's provenance."""
    report = trace_claim(bundle, artifacts)
    return [artifacts[aid] for aid in report.root_artifacts]


__all__ = [
    "ClaimAudit",
    "ClaimBundle",
    "ClaimDefect",
    "DefectSeverity",
    "EvidenceRef",
    "EvidenceRefKind",
    "SensitivityKind",
    "SensitivityResult",
    "TraceabilityReport",
    "WellFormednessReport",
    "audit_claim",
    "check_wellformed",
    "has_alternatives",
    "raw_inputs",
    "trace_claim",
    "uncertainty_declared",
]
