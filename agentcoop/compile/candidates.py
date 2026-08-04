"""Candidate enumeration under the grammar.

The compiler does not search a topology space against a reward — it cannot,
because in this setting there is no reward and re-running candidates is
expensive. Instead it enumerates a small, bounded set of structures that the
grammar actually licenses, and hands them to static analysis and Pareto
selection.

One rule matters more than the rest: **whenever a single component covers
every subgoal, the single-component candidate is always enumerated**, and it
carries a `DECLINE_STRUCTURE` evidence record. The system has to be able to
conclude "you do not need a multi-agent workflow here" and be measured on
getting that right. A compiler that can only ever add agents will always add
agents.
"""

from __future__ import annotations

import itertools
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.grammar import (
    RuleContext,
    can_bind,
    can_join,
    can_parallelize,
    can_verify,
    term_io,
)
from agentcoop.compile.requirements import RequirementPlan, plan_requirements
from agentcoop.ir.capability import CapabilityCard
from agentcoop.ir.dossier import Subgoal, TaskEvidenceDossier
from agentcoop.ir.evidence import (
    Alternative,
    DecisionKind,
    DesignEvidenceRecord,
    EvidenceLedger,
    requirement_evidence,
)
from agentcoop.ir.workflow import (
    Atomic,
    Join,
    Parallel,
    Sequence,
    Verify,
    WorkflowTerm,
)


class CandidateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    term: WorkflowTerm
    ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    #: subgoal id -> bound component.
    bindings: dict[str, str] = Field(default_factory=dict)
    construction_log: list[str] = Field(default_factory=list)
    #: True when every subgoal is served by the same component.
    single_component: bool = False

    @property
    def components(self) -> list[str]:
        return sorted(set(self.bindings.values()))


class EnumerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateWorkflow] = Field(default_factory=list)
    plan: RequirementPlan
    #: Subgoals no component in the library can serve, with the reasons.
    unbindable: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def bindable_components(
    subgoal: Subgoal, ctx: RuleContext
) -> tuple[list[CapabilityCard], list[str]]:
    """Components licensed to serve ``subgoal``, best first, plus refusals.

    Ranked by certification level then by recorded reliability then by name —
    all deterministic, none of it a model's opinion.
    """
    allowed: list[CapabilityCard] = []
    refusals: list[str] = []
    for name in ctx.library.names():
        verdict = can_bind(name, subgoal, ctx)
        if verdict.allowed:
            card = ctx.library.require(name)
            allowed.append(card)
        else:
            refusals.append(f"{name}: {verdict.reason}")

    def rank(card: CapabilityCard) -> tuple:
        reliability = 0.0
        if ctx.statistics is not None:
            getter = getattr(ctx.statistics, "component_reliability", None)
            if callable(getter):
                reliability = float(getattr(getter(card.name), "lcb", 0.0))
        return (-int(card.certification_level), -reliability, card.name)

    return sorted(allowed, key=rank), refusals


def enumerate_candidates(
    dossier: TaskEvidenceDossier,
    ctx: RuleContext,
    *,
    max_candidates: int = 16,
    alternatives_per_subgoal: int = 2,
) -> EnumerationResult:
    plan = plan_requirements(dossier)
    result = EnumerationResult(plan=plan)

    options: dict[str, list[CapabilityCard]] = {}
    for subgoal in dossier.subgoals:
        allowed, refusals = bindable_components(subgoal, ctx)
        if not allowed:
            result.unbindable[subgoal.subgoal_id] = refusals
        options[subgoal.subgoal_id] = allowed[:alternatives_per_subgoal]

    if result.unbindable:
        result.notes.append(
            "no candidate could be built: "
            + "; ".join(
                f"subgoal '{sid}' has no bindable component"
                for sid in sorted(result.unbindable)
            )
        )
        return result

    # -- the decline-structure candidate, always considered first -----------
    covering = _covering_components(dossier, options)
    for component in covering:
        candidate = _build_single_component(dossier, plan, component, ctx)
        if candidate is not None:
            result.candidates.append(candidate)

    # -- composed candidates ------------------------------------------------
    subgoal_ids = [s.subgoal_id for s in dossier.subgoals]
    assignments = itertools.product(*(options[sid] for sid in subgoal_ids))
    for index, assignment in enumerate(assignments):
        if len(result.candidates) >= max_candidates:
            result.notes.append(
                f"enumeration capped at {max_candidates} candidates; "
                "later component assignments were not explored"
            )
            break
        bindings = {sid: card.name for sid, card in zip(subgoal_ids, assignment)}
        if len(set(bindings.values())) == 1 and covering:
            continue  # already emitted as the single-component candidate
        candidate = _build_composed(dossier, plan, bindings, ctx, index=index)
        if candidate is not None and not _duplicate(candidate, result.candidates):
            result.candidates.append(candidate)

    if not result.candidates:
        result.notes.append("no structure satisfied the grammar's applicability rules")
    return result


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _covering_components(
    dossier: TaskEvidenceDossier, options: dict[str, list[CapabilityCard]]
) -> list[str]:
    """Components bindable to *every* subgoal."""
    if not dossier.subgoals:
        return []
    sets = [ {c.name for c in options[s.subgoal_id]} for s in dossier.subgoals ]
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def _build_single_component(
    dossier: TaskEvidenceDossier,
    plan: RequirementPlan,
    component: str,
    ctx: RuleContext,
) -> Optional[CandidateWorkflow]:
    bindings = {s.subgoal_id: component for s in dossier.subgoals}
    steps = [
        Atomic(component=component, subgoal_id=sid) for sid in plan.order
    ]
    term = (steps[0] if len(steps) == 1 else Sequence(children_terms=steps)).ensure_ids()

    ledger = EvidenceLedger()
    _record_bindings(ledger, bindings, ctx)
    ledger.add(
        DesignEvidenceRecord(
            decision_id="decline_structure",
            decision_kind=DecisionKind.DECLINE_STRUCTURE,
            decision=(
                f"'{component}' is certified for every subgoal, so no composition "
                "was added"
            ),
            target="workflow",
            evidence=[
                requirement_evidence(
                    plan.order[0] if plan.order else "workflow",
                    f"all {len(dossier.subgoals)} subgoal(s) are served by "
                    f"'{component}' alone: "
                    + ", ".join(s.required_capability for s in dossier.subgoals),
                    covered_subgoals=[s.subgoal_id for s in dossier.subgoals],
                )
            ],
            alternatives=[
                Alternative(
                    description="a multi-component workflow over the same subgoals",
                    rejection_reason=(
                        "would add handoff surface, cost, and failure modes without "
                        "serving a subgoal the single component cannot"
                    ),
                )
            ],
        )
    )
    return CandidateWorkflow(
        candidate_id=f"single::{component}",
        term=term,
        ledger=ledger,
        bindings=bindings,
        single_component=True,
        construction_log=[f"single component '{component}' covers every subgoal"],
    )


def _build_composed(
    dossier: TaskEvidenceDossier,
    plan: RequirementPlan,
    bindings: dict[str, str],
    ctx: RuleContext,
    *,
    index: int,
) -> Optional[CandidateWorkflow]:
    ledger = EvidenceLedger()
    log: list[str] = []
    _record_bindings(ledger, bindings, ctx)

    layer_terms: list[WorkflowTerm] = []
    for depth, layer in enumerate(plan.layers):
        branches = [
            Atomic(component=bindings[sid], subgoal_id=sid).ensure_ids()
            for sid in layer
        ]
        if len(branches) == 1:
            layer_terms.append(branches[0])
            continue

        parallel_verdict = can_parallelize(branches, ctx)
        if not parallel_verdict.allowed:
            log.append(
                f"layer {depth}: parallel refused ({parallel_verdict.reason}); "
                "sequencing instead"
            )
            layer_terms.append(Sequence(children_terms=branches).ensure_ids())
            continue

        merge = _pick_merge(branches, ctx)
        if merge is not None:
            join_verdict = can_join(branches, merge, ctx)
            if join_verdict.allowed:
                term = Join(branches=branches, merge=merge).ensure_ids()
                ledger.add(
                    DesignEvidenceRecord(
                        decision_id=f"join@{depth}",
                        decision_kind=DecisionKind.JOIN,
                        decision=f"combine layer {depth} branches with '{merge}'",
                        target=term.term_id,
                        evidence=list(parallel_verdict.evidence)
                        + list(join_verdict.evidence),
                    )
                )
                layer_terms.append(term)
                log.append(f"layer {depth}: joined with '{merge}'")
                continue
            log.append(f"layer {depth}: join refused ({join_verdict.reason})")

        term = Parallel(
            branches=branches,
            independence_ref=parallel_verdict.evidence[0].source_ref
            if parallel_verdict.evidence
            else "",
        ).ensure_ids()
        ledger.add(
            DesignEvidenceRecord(
                decision_id=f"parallel@{depth}",
                decision_kind=DecisionKind.PARALLELIZE,
                decision=f"run layer {depth} branches concurrently",
                target=term.term_id,
                evidence=list(parallel_verdict.evidence),
            )
        )
        layer_terms.append(term)
        log.append(f"layer {depth}: parallel ({parallel_verdict.reason})")

    if not layer_terms:
        return None

    term: WorkflowTerm = (
        layer_terms[0]
        if len(layer_terms) == 1
        else Sequence(children_terms=layer_terms).ensure_ids()
    )

    term, verify_log = _attach_verifiers(term, dossier, ctx, ledger)
    log.extend(verify_log)

    return CandidateWorkflow(
        candidate_id=f"composed::{index}::"
        + "+".join(f"{k}={v}" for k, v in sorted(bindings.items())),
        term=term.ensure_ids(),
        ledger=ledger,
        bindings=bindings,
        construction_log=log,
    )


def _pick_merge(branches: list[WorkflowTerm], ctx: RuleContext) -> Optional[str]:
    """Deterministically choose a merge for the branches' shared output type.

    Preference order is declared here rather than left to a model: corroboration
    (`intersect`) is the conservative default for a shared type, since claiming
    a finding both branches saw is weaker than claiming either alone.
    """
    produced = [term_io(b, ctx.library)[1] for b in branches]
    common = set.intersection(*produced) if produced else set()
    if not common:
        return None
    type_name = sorted(common)[0]
    applicable = [
        s for s in ctx.merges.for_type(type_name) if s.accepts_arity(len(branches))
    ]
    if not applicable:
        return None
    preferred = ["intersect", "agreement_filter", "union", "concat_records"]
    by_name = {s.name: s for s in applicable}
    for name in preferred:
        if name in by_name:
            return name
    return applicable[0].name


def _attach_verifiers(
    term: WorkflowTerm,
    dossier: TaskEvidenceDossier,
    ctx: RuleContext,
    ledger: EvidenceLedger,
) -> tuple[WorkflowTerm, list[str]]:
    """Wrap the workflow in a verifier when a subgoal demands one and one exists."""
    log: list[str] = []
    needs = [s for s in dossier.subgoals if s.requires_verification]
    if not needs:
        return term, log

    for verifier in ("hard_required_outputs_present", "artifact_provenance_complete"):
        verdict = can_verify(term, verifier, ctx)
        if verdict.allowed:
            wrapped = Verify(body=term, verifier=verifier).ensure_ids()
            ledger.add(
                DesignEvidenceRecord(
                    decision_id=f"verify::{verifier}",
                    decision_kind=DecisionKind.ADD_VERIFIER,
                    decision=f"attach verifier '{verifier}'",
                    target=wrapped.term_id,
                    subgoal_id=needs[0].subgoal_id,
                    evidence=list(verdict.evidence),
                )
            )
            log.append(f"attached verifier '{verifier}'")
            return wrapped, log
        log.append(f"verifier '{verifier}' refused: {verdict.reason}")
    return term, log


def _record_bindings(
    ledger: EvidenceLedger, bindings: dict[str, str], ctx: RuleContext
) -> None:
    for subgoal_id, component in sorted(bindings.items()):
        subgoal = ctx.dossier.subgoal(subgoal_id)
        if subgoal is None:
            continue
        verdict = can_bind(component, subgoal, ctx)
        alternatives = [
            Alternative(
                description=f"bind '{other.name}' instead",
                rejection_reason=(
                    f"lower certification ({other.certification_level.name})"
                    if other.certification_level
                    < ctx.library.require(component).certification_level
                    else "equivalent; chosen candidate ranked first deterministically"
                ),
            )
            for other in bindable_components(subgoal, ctx)[0]
            if other.name != component
        ][:2]
        ledger.add(
            DesignEvidenceRecord(
                decision_id=f"bind::{subgoal_id}",
                decision_kind=DecisionKind.BIND_COMPONENT,
                decision=f"bind '{component}' to subgoal '{subgoal_id}'",
                target=f"{subgoal_id}__{component}",
                subgoal_id=subgoal_id,
                evidence=list(verdict.evidence),
                alternatives=alternatives,
            )
        )


def _duplicate(candidate: CandidateWorkflow, existing: list[CandidateWorkflow]) -> bool:
    key = candidate.term.structural_key()
    return any(c.term.structural_key() == key for c in existing)


__all__ = [
    "CandidateWorkflow",
    "EnumerationResult",
    "bindable_components",
    "enumerate_candidates",
]
