"""Production rules with applicability conditions.

Every way of adding structure to a workflow has a condition that must be
discharged *with evidence* before the production may be used. This is the
mechanism that replaces "the designer agent proposed this graph": the rules
below either return the evidence that licenses a decision, or a concrete
reason for refusing it.

The conditions worth stating plainly, because they are the research content:

``bind``
    The component must be certified at or above the subgoal's requirement, and
    it must produce what the subgoal needs with facets the consumer can read.
    Certification comes from executed probes, so the capability evidence is
    ``OBSERVED``. A component known only from its README yields ``DOCUMENTED``
    evidence, which is not load-bearing, so the bind is inadmissible.

``parallelize``
    Requires *both* independence (no branch consumes what another produces, no
    overlapping side effects) *and* a positive reason: complementary artifact
    types, distinct modality facets, or recorded error decorrelation. Task
    difficulty is explicitly not a reason. This is the rule that stops
    "complex task, therefore more agents".

``join``
    Requires a registered merge algebra for the branches' common output type.
    No merge, no join. Majority voting is one registered merge among several,
    and needs three branches, because a two-way vote is a tie.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.artifacts import Compatibility, TypeRegistry
from agentcoop.ir.capability import (
    CapabilityCard,
    CertificationLevel,
    ComponentLibrary,
)
from agentcoop.ir.checks import CheckLevel
from agentcoop.ir.dossier import EvaluatorAvailability, Subgoal, TaskEvidenceDossier
from agentcoop.ir.evidence import (
    EvidenceItem,
    capability_evidence,
    coordination_evidence,
    outcome_evidence,
    requirement_evidence,
)
from agentcoop.ir.workflow import Atomic, WorkflowTerm, atomics
from agentcoop.merges import MergeRegistry, default_merge_registry


class RuleContext(BaseModel):
    """Everything a production rule may consult. Deliberately no LLM."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    dossier: TaskEvidenceDossier
    library: ComponentLibrary
    types: TypeRegistry = Field(default_factory=TypeRegistry)
    merges: MergeRegistry = Field(default_factory=default_merge_registry)
    #: Optional StatisticsStore. Duck-typed so compile need not import memory.
    statistics: Optional[Any] = None


class RuleVerdict(BaseModel):
    """A production is licensed, or it is not, and either way we say why."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    evidence: list[EvidenceItem] = Field(default_factory=list)
    reason: str = ""

    @classmethod
    def refuse(cls, reason: str) -> "RuleVerdict":
        return cls(allowed=False, reason=reason)

    @classmethod
    def allow(cls, *evidence: EvidenceItem, reason: str = "") -> "RuleVerdict":
        return cls(allowed=True, evidence=list(evidence), reason=reason)


# ---------------------------------------------------------------------------
# Term IO
# ---------------------------------------------------------------------------


def term_io(term: WorkflowTerm, library: ComponentLibrary) -> tuple[set[str], set[str]]:
    """Artifact types a term consumes and produces, over all its components."""
    consumes: set[str] = set()
    produces: set[str] = set()
    for atom in atomics(term):
        card = library.get(atom.component)
        if card is None:
            continue
        consumes.update(t.name for t in card.io.consumes)
        produces.update(t.name for t in card.io.produces)
    return consumes, produces


def term_components(term: WorkflowTerm) -> list[str]:
    return sorted({a.component for a in atomics(term)})


def _required_level(subgoal: Subgoal) -> CertificationLevel:
    try:
        return CertificationLevel[subgoal.min_certification.upper()]
    except KeyError:
        return CertificationLevel.PROBED


def _probe_ref(card: CapabilityCard) -> Optional[str]:
    """The probe id that most strongly evidences this component's capability."""
    for kind in ("smoke", "schema", "reachable"):
        outcomes = [p for p in card.empirical.probes_of(kind) if p.passed]
        if outcomes:
            return outcomes[0].probe_id
    return None


# ---------------------------------------------------------------------------
# bind
# ---------------------------------------------------------------------------


def can_bind(component: str, subgoal: Subgoal, ctx: RuleContext) -> RuleVerdict:
    card = ctx.library.get(component)
    if card is None:
        return RuleVerdict.refuse(f"component '{component}' is not in the library")

    if subgoal.required_capability not in card.functional_capabilities:
        return RuleVerdict.refuse(
            f"'{component}' does not claim capability "
            f"'{subgoal.required_capability}' required by subgoal '{subgoal.subgoal_id}'"
        )

    required = _required_level(subgoal)
    actual = card.certification_level
    if actual < required:
        return RuleVerdict.refuse(
            f"'{component}' is certified at {actual.name} but subgoal "
            f"'{subgoal.subgoal_id}' requires {required.name}; probe it before binding"
        )

    missing = [a for a in subgoal.produces if not card.can_produce(a)]
    if missing:
        return RuleVerdict.refuse(
            f"'{component}' does not produce required artifact(s): {', '.join(sorted(missing))}"
        )

    # Facet obligations: the subgoal may demand that outputs arrive in a
    # particular namespace/organism/unit. Observation beats declaration here.
    for artifact_type, wanted in sorted(subgoal.required_output_facets.items()):
        effective = card.effective_facets(artifact_type)
        for key, value in sorted(wanted.items()):
            have = effective.get(key)
            if have is None:
                return RuleVerdict.refuse(
                    f"'{component}' never declares facet '{key}' on '{artifact_type}', "
                    f"which subgoal '{subgoal.subgoal_id}' requires to be '{value}'"
                )
            if have != value:
                return RuleVerdict.refuse(
                    f"'{component}' emits {artifact_type}.{key}={have}, but subgoal "
                    f"'{subgoal.subgoal_id}' requires '{value}'"
                )

    # The *subgoal* is authoritative about what flows into this role: it must
    # be possible to hand the component everything the subgoal supplies. The
    # converse would be wrong — a component certified for several capabilities
    # necessarily declares the union of their inputs, and refusing it for that
    # would make a generalist unbindable everywhere and quietly bias the
    # compiler toward composition.
    unacceptable = [a for a in subgoal.consumes if not card.can_consume(a)]
    if unacceptable:
        return RuleVerdict.refuse(
            f"'{component}' cannot accept input(s) the subgoal supplies: "
            + ", ".join(sorted(unacceptable))
        )

    probe_id = _probe_ref(card)
    evidence = [
        requirement_evidence(
            subgoal.subgoal_id,
            f"subgoal '{subgoal.subgoal_id}' requires capability "
            f"'{subgoal.required_capability}' producing {', '.join(subgoal.produces) or 'no artifact'}",
            produces=list(subgoal.produces),
        ),
        capability_evidence(
            component,
            probe_id or "unprobed",
            f"'{component}' is certified {actual.name} and emits "
            + ", ".join(sorted(t.name for t in card.io.produces)),
            observed=probe_id is not None,
            certification=actual.name,
        ),
    ]

    if ctx.statistics is not None:
        item = _reliability_evidence(component, ctx)
        if item is not None:
            evidence.append(item)

    return RuleVerdict.allow(
        *evidence, reason=f"'{component}' can serve '{subgoal.subgoal_id}'"
    )


def _reliability_evidence(component: str, ctx: RuleContext) -> Optional[EvidenceItem]:
    getter = getattr(ctx.statistics, "component_reliability", None)
    if not callable(getter):
        return None
    posterior = getter(component)
    n = int(getattr(posterior, "n_observations", 0))
    if n <= 0:
        return None
    return outcome_evidence(
        f"'{component}' succeeded with lower-confidence-bound "
        f"{getattr(posterior, 'lcb', 0.0):.2f} over {n} prior runs",
        f"stats:component:{component}",
        n,
        lcb=float(getattr(posterior, "lcb", 0.0)),
    )


# ---------------------------------------------------------------------------
# sequence
# ---------------------------------------------------------------------------


def can_sequence(
    upstream: WorkflowTerm, downstream: WorkflowTerm, ctx: RuleContext
) -> RuleVerdict:
    """Sequencing is licensed by a real typed data dependency."""
    _, up_produces = term_io(upstream, ctx.library)
    down_consumes, _ = term_io(downstream, ctx.library)
    shared = sorted(up_produces & down_consumes)
    if not shared:
        return RuleVerdict.refuse(
            "no artifact flows from the upstream term to the downstream term; "
            "sequencing them would impose ordering without a data dependency"
        )

    problems: list[str] = []
    for type_name in shared:
        producer_card = _producer_of(upstream, type_name, ctx.library)
        consumer_card = _consumer_of(downstream, type_name, ctx.library)
        if producer_card is None or consumer_card is None:
            continue
        produced = producer_card.output_type(type_name)
        consumed = consumer_card.io.consumed_type(type_name)
        if produced is None or consumed is None:
            continue
        report = ctx.types.check_compatibility(produced, consumed)
        if report.verdict is Compatibility.INCOMPATIBLE:
            problems.append(f"{type_name}: {report.detail}")
        elif report.verdict is Compatibility.UNDERSPECIFIED:
            problems.append(f"{type_name}: {report.detail}")

    if problems:
        return RuleVerdict.refuse("; ".join(problems))

    return RuleVerdict.allow(
        coordination_evidence(
            f"downstream consumes {', '.join(shared)} produced upstream, with "
            "compatible structural and semantic contracts",
            f"typed_dependency:{'+'.join(shared)}",
            artifact_types=shared,
        ),
        reason=f"typed data dependency on {', '.join(shared)}",
    )


def _producer_of(
    term: WorkflowTerm, type_name: str, library: ComponentLibrary
) -> Optional[CapabilityCard]:
    for atom in atomics(term):
        card = library.get(atom.component)
        if card is not None and card.can_produce(type_name):
            return card
    return None


def _consumer_of(
    term: WorkflowTerm, type_name: str, library: ComponentLibrary
) -> Optional[CapabilityCard]:
    for atom in atomics(term):
        card = library.get(atom.component)
        if card is not None and card.can_consume(type_name):
            return card
    return None


# ---------------------------------------------------------------------------
# parallelize
# ---------------------------------------------------------------------------


def can_parallelize(branches: Sequence[WorkflowTerm], ctx: RuleContext) -> RuleVerdict:
    """Independence is necessary; a positive reason is also required."""
    if len(branches) < 2:
        return RuleVerdict.refuse("a parallel term needs at least two branches")

    ios = [term_io(b, ctx.library) for b in branches]

    # 1. Data independence.
    for i, (consumes_i, _) in enumerate(ios):
        for j, (_, produces_j) in enumerate(ios):
            if i == j:
                continue
            overlap = consumes_i & produces_j
            if overlap:
                return RuleVerdict.refuse(
                    f"branch {i} consumes {', '.join(sorted(overlap))} produced by "
                    f"branch {j}; these must be sequenced, not parallelized"
                )

    # 2. No shared mutable state.
    effects: list[set[str]] = []
    for branch in branches:
        collected: set[str] = set()
        for component in term_components(branch):
            card = ctx.library.get(component)
            if card is not None:
                collected.update(card.behavior.side_effects)
        effects.append(collected)
    for i in range(len(effects)):
        for j in range(i + 1, len(effects)):
            clash = effects[i] & effects[j]
            if clash:
                return RuleVerdict.refuse(
                    f"branches {i} and {j} share side effect(s) {', '.join(sorted(clash))}; "
                    "running them concurrently risks a stale-state fault"
                )

    # 3. A positive reason. Difficulty is not one.
    produced_sets = [produces for _, produces in ios]
    distinct_outputs = len({frozenset(p) for p in produced_sets}) == len(produced_sets)

    modalities: list[str] = []
    for branch in branches:
        for component in term_components(branch):
            card = ctx.library.get(component)
            if card is None:
                continue
            for artifact_type in card.io.produces:
                modality = card.effective_facets(artifact_type.name).get("modality")
                if modality:
                    modalities.append(modality)
    distinct_modalities = len(set(modalities)) >= len(branches) >= 2

    decorrelated = _error_decorrelation(branches, ctx)

    if distinct_modalities:
        return RuleVerdict.allow(
            coordination_evidence(
                "branches observe distinct modalities "
                f"({', '.join(sorted(set(modalities)))}), so their errors are not "
                "expected to be common-mode",
                "independence:modality",
                modalities=sorted(set(modalities)),
            ),
            reason="complementary modalities",
        )
    if distinct_outputs:
        return RuleVerdict.allow(
            coordination_evidence(
                "branches produce disjoint artifact types, so each contributes an "
                "observation the others cannot",
                "independence:artifact_types",
                produced=[sorted(p) for p in produced_sets],
            ),
            reason="complementary artifact types",
        )
    if decorrelated is not None:
        return RuleVerdict.allow(decorrelated, reason="recorded error decorrelation")

    return RuleVerdict.refuse(
        "branches are independent but redundant: they produce the same artifact "
        "types from the same modality with no recorded error decorrelation. "
        "Parallelism would add cost without adding evidence"
    )


def _error_decorrelation(
    branches: Sequence[WorkflowTerm], ctx: RuleContext
) -> Optional[EvidenceItem]:
    """Historical evidence that these branches fail independently."""
    if ctx.statistics is None:
        return None
    getter = getattr(ctx.statistics, "failure_distribution", None)
    if not callable(getter):
        return None
    distributions = []
    for branch in branches:
        for component in term_components(branch):
            dist = getter(component)
            if dist:
                distributions.append((component, dist))
    if len(distributions) < 2:
        return None
    # Common-mode if the two components' modal fault classes coincide.
    modes = [max(d.items(), key=lambda kv: kv[1])[0] for _, d in distributions]
    if len(set(modes)) < len(modes):
        return None
    return outcome_evidence(
        "recorded failure distributions differ across branches "
        f"({', '.join(str(m) for m in modes)}), indicating decorrelated errors",
        "stats:failure_decorrelation",
        n=len(distributions),
    )


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------


def can_join(
    branches: Sequence[WorkflowTerm], merge: str, ctx: RuleContext
) -> RuleVerdict:
    if len(branches) < 2:
        return RuleVerdict.refuse("a join needs at least two branches")
    if not merge:
        return RuleVerdict.refuse(
            "a join must name a merge algebra; combining branch outputs without "
            "declaring what the combination means is not expressible"
        )

    spec = ctx.merges.get(merge)
    if spec is None:
        return RuleVerdict.refuse(
            f"no merge algebra named '{merge}' is registered "
            f"(available: {', '.join(ctx.merges.names()) or 'none'})"
        )

    produced_sets = [term_io(b, ctx.library)[1] for b in branches]
    common = set.intersection(*produced_sets) if produced_sets else set()
    if not common:
        return RuleVerdict.refuse(
            "branches share no common output artifact type, so there is nothing "
            "for a merge to combine"
        )

    type_name = sorted(common)[0]
    if not spec.accepts_type(type_name):
        return RuleVerdict.refuse(
            f"merge '{merge}' is not defined for artifact type '{type_name}'"
        )
    if not spec.accepts_arity(len(branches)):
        return RuleVerdict.refuse(
            f"merge '{merge}' requires at least {spec.min_branches} branches, "
            f"got {len(branches)}"
        )

    if spec.requires_matching_facets:
        facet_values: dict[str, set[str]] = {}
        for branch in branches:
            for component in term_components(branch):
                card = ctx.library.get(component)
                if card is None:
                    continue
                for key, value in card.effective_facets(type_name).items():
                    if key in spec.facet_exemptions:
                        continue
                    facet_values.setdefault(key, set()).add(value)
        clashes = {k: sorted(v) for k, v in facet_values.items() if len(v) > 1}
        if clashes:
            detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(clashes.items()))
            return RuleVerdict.refuse(
                f"merge '{merge}' requires matching facets but branches disagree ({detail})"
            )

    return RuleVerdict.allow(
        coordination_evidence(
            f"merge '{merge}' is registered for '{type_name}' with semantics: "
            f"{spec.semantics}",
            f"merge:{merge}",
            artifact_type=type_name,
            semantics=spec.semantics,
        ),
        reason=f"merge algebra '{merge}' applies to '{type_name}'",
    )


# ---------------------------------------------------------------------------
# verify / fallback / human gate
# ---------------------------------------------------------------------------


def can_verify(body: WorkflowTerm, verifier: str, ctx: RuleContext) -> RuleVerdict:
    if not verifier:
        return RuleVerdict.refuse("a verify term must name a verifier")

    subgoal_ids = {a.subgoal_id for a in atomics(body)}
    requiring = [
        s
        for s in ctx.dossier.subgoals
        if s.subgoal_id in subgoal_ids and s.requires_verification
    ]

    level = _verifier_level(verifier)
    availability = ctx.dossier.availability(level)
    if availability is EvaluatorAvailability.UNAVAILABLE:
        return RuleVerdict.refuse(
            f"verifier '{verifier}' operates at level {level.value}, which this task "
            "declares unavailable; attaching it would produce checks that can never "
            "return a verdict"
        )

    if not requiring:
        return RuleVerdict.refuse(
            "no subgoal in this term declares requires_verification; adding a "
            "verifier would cost without discharging a stated requirement"
        )

    return RuleVerdict.allow(
        requirement_evidence(
            requiring[0].subgoal_id,
            f"subgoal(s) {', '.join(s.subgoal_id for s in requiring)} declare "
            f"requires_verification, and a {level.value}-level evaluator is "
            f"{availability.value} for this task",
            verifier=verifier,
        ),
        reason=f"verification required and '{verifier}' is available",
    )


def _verifier_level(verifier: str) -> CheckLevel:
    """Level a verifier operates at, inferred from its registered name prefix."""
    for level in CheckLevel:
        if verifier.startswith(level.value):
            return level
    if "claim" in verifier:
        return CheckLevel.CLAIM
    if "provenance" in verifier or "coverage" in verifier or "facet" in verifier:
        return CheckLevel.ARTIFACT
    return CheckLevel.HARD


def can_fallback(
    primary: WorkflowTerm, alternate: WorkflowTerm, ctx: RuleContext
) -> RuleVerdict:
    _, primary_produces = term_io(primary, ctx.library)
    _, alternate_produces = term_io(alternate, ctx.library)
    if not primary_produces or primary_produces != alternate_produces:
        return RuleVerdict.refuse(
            "fallback arms must produce the same artifact types "
            f"(primary: {sorted(primary_produces)}, alternate: {sorted(alternate_produces)})"
        )

    if ctx.statistics is None:
        return RuleVerdict.refuse(
            "a fallback is only justified by an observed failure rate for the "
            "primary; no run statistics are available"
        )

    getter = getattr(ctx.statistics, "component_reliability", None)
    if not callable(getter):
        return RuleVerdict.refuse("statistics store exposes no component_reliability")

    worst_component, worst = None, 1.0
    total_n = 0
    for component in term_components(primary):
        posterior = getter(component)
        n = int(getattr(posterior, "n_observations", 0))
        total_n += n
        mean = float(getattr(posterior, "mean", 1.0))
        if n > 0 and mean < worst:
            worst_component, worst = component, mean

    if worst_component is None or worst >= 0.95:
        return RuleVerdict.refuse(
            "the primary has no recorded failure rate high enough to justify the "
            "cost of a fallback arm"
        )

    return RuleVerdict.allow(
        outcome_evidence(
            f"'{worst_component}' succeeds only {worst:.0%} of the time in recorded runs",
            f"stats:component:{worst_component}",
            total_n,
            success_rate=worst,
        ),
        reason=f"observed failure rate for '{worst_component}' justifies a fallback",
    )


def can_human_gate(body: WorkflowTerm, condition: str, ctx: RuleContext) -> RuleVerdict:
    matching = [c for c in ctx.dossier.human_review if c.condition_id == condition]
    if not matching:
        return RuleVerdict.refuse(
            f"no human-review condition '{condition}' is declared in the dossier"
        )
    return RuleVerdict.allow(
        requirement_evidence(
            atomics(body)[0].subgoal_id if atomics(body) else "workflow",
            f"dossier declares human review condition '{condition}': "
            f"{matching[0].description}",
            condition=condition,
        ),
        reason=f"declared human-review condition '{condition}'",
    )


__all__ = [
    "RuleContext",
    "RuleVerdict",
    "term_io",
    "term_components",
    "can_bind",
    "can_sequence",
    "can_parallelize",
    "can_join",
    "can_verify",
    "can_fallback",
    "can_human_gate",
]
