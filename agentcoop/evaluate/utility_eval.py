"""Turning a check report into a :class:`~agentcoop.ir.utility.UtilityVector`.

This is the seam where "the workflow ran" is prevented from becoming "the
result is good". Three rules do the work, and every branch below exists to
serve one of them.

**1. Unmeasured is not zero.** If every check contributing to a dimension came
back ``UNAVAILABLE`` — or no such check exists at all — the dimension is marked
via :meth:`UtilityVector.mark_unavailable` rather than scored 0.0. The
consequence is deliberate and asymmetric: dominance in
:func:`~agentcoop.ir.utility.dominates` only compares dimensions *both* vectors
measured, so an unmeasured dimension makes two workflows honestly
incomparable there instead of inventing a winner; while
:func:`~agentcoop.ir.utility.hypervolume` maps unmeasured to 0.0, below the
nadir floor, so being unevaluable earns no credit either. Scoring 0.0 would
collapse both behaviours into "worse than the worst measured workflow", which
is a different — and false — statement.

**2. An unverifiable hard invariant is not a satisfied one.** If a *blocking*
check could not be evaluated, validity is unavailable, not the pass rate of
whichever checks happened to run. A failed blocking check still pins validity
to 0.0: a definite violation is worse than an unknown one.

**3. Silence is exposure.** A component with no downstream verifier, or one
whose capability card is not in the library at all, counts toward risk. An
unknown component is not a safe component.

Everything is deterministic: no clock, no randomness, no dict-order
dependence. :func:`derive_utility` returns the same numbers plus the reasoning
that produced them, which is what the ``explain`` command prints.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.capability import CapabilityCard, ComponentLibrary, CostProfile
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckResult, CheckStatus, SignalSource
from agentcoop.ir.dossier import EvaluatorAvailability, ResourceLimits, TaskEvidenceDossier
from agentcoop.ir.evidence import EvidenceLedger
from agentcoop.ir.utility import Objective, UtilityVector
from agentcoop.ir.workflow import CompiledWorkflow, ExecGraph

# ---------------------------------------------------------------------------
# Which checks feed which dimension
# ---------------------------------------------------------------------------
#
# Matching is by substring on ``check_id`` so that subject-qualified ids
# ("provenance_complete:node_a") still resolve. The token lists are module
# constants rather than inline literals so the mapping is auditable and the
# check registry can be extended without editing the logic.

#: ARTIFACT-level checks that speak to *justification* rather than validity.
#: Deliberately narrow: a facet or coverage check says the artifact is sound,
#: not that the decision to produce it was evidenced.
PROVENANCE_CHECK_TOKENS: tuple[str, ...] = ("provenance", "lineage", "traceab")

#: PROCESS/RESOURCE checks that establish stability under perturbation. This
#: is the definition of robustness in ``ir/utility.py``: "stability under
#: reseeding, thresholds, and perturbation".
ROBUSTNESS_CHECK_TOKENS: tuple[str, ...] = (
    "sensitivity",
    "negative_control",
    "perturb",
    "reseed",
    "ablation",
    "determinis",
    "reproduc",
)

#: Checks whose failure means a step's output is never independently checked.
VERIFIER_CHECK_TOKENS: tuple[str, ...] = ("verifier",)

#: Checks whose failure means a component failed silently, or could.
SILENT_FAILURE_CHECK_TOKENS: tuple[str, ...] = ("silent",)

#: Node roles that constitute verification of an upstream result. A human gate
#: counts: it is verification of last resort, but it is not silence.
VERIFYING_ROLES: frozenset[str] = frozenset({"verifier", "human_gate"})

#: Weight given to the design-evidence ledger when blending it with the
#: artifact-level provenance checks. Equal weighting is a declared choice, not
#: a tuned constant; when only one side is measurable it is used alone.
EVIDENCE_LEDGER_WEIGHT: float = 0.5

#: Risk is the mean over component nodes of a two-term exposure. Both terms
#: are declared here rather than folded into the arithmetic so the trade-off
#: is visible and testable.
UNVERIFIED_WEIGHT: float = 0.5
SILENT_EXPOSURE_WEIGHT: float = 0.5


# ---------------------------------------------------------------------------
# Derivation record
# ---------------------------------------------------------------------------


class DimensionEvidence(BaseModel):
    """Why one dimension has the value it has — or why it has none."""

    model_config = ConfigDict(extra="forbid")

    objective: Objective
    available: bool
    value: Optional[float] = None
    #: Short machine token for the rule that fired, e.g. ``blocking_failure``.
    basis: str = ""
    reason: str = ""
    #: Numeric intermediates, kept so a reviewer can re-derive the value.
    inputs: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class UtilityDerivation(BaseModel):
    """The vector plus the reasoning that produced every coordinate."""

    model_config = ConfigDict(extra="forbid")

    vector: UtilityVector
    dimensions: list[DimensionEvidence] = Field(default_factory=list)

    def for_objective(self, objective: Objective) -> Optional[DimensionEvidence]:
        for dim in self.dimensions:
            if dim.objective is objective:
                return dim
        return None

    @property
    def unavailable(self) -> list[Objective]:
        return [d.objective for d in self.dimensions if not d.available]

    def explain(self) -> str:
        lines = []
        for dim in self.dimensions:
            value = "unavailable" if not dim.available else f"{dim.value:.4f}"
            lines.append(f"{dim.objective.value:>18}: {value:>12}  [{dim.basis}] {dim.reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Check selection helpers
# ---------------------------------------------------------------------------


def _matches(result: CheckResult, tokens: Sequence[str]) -> bool:
    lowered = result.check_id.lower()
    return any(token in lowered for token in tokens)


def _decided(results: Sequence[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.decided]


def _pass_rate(results: Sequence[CheckResult]) -> Optional[float]:
    """Pass rate over *decided* results; ``None`` when nothing was decided.

    ``None`` is the whole point: it propagates "we do not know" instead of
    silently becoming a 0 or a 1.
    """
    decided = _decided(results)
    if not decided:
        return None
    return sum(1 for r in decided if r.ok) / len(decided)


def _availability_note(dossier: TaskEvidenceDossier, level: CheckLevel) -> str:
    availability = dossier.availability(level)
    if availability is EvaluatorAvailability.UNAVAILABLE:
        return f"dossier declares no {level.value}-level evaluator for this task"
    return (
        f"dossier declares {level.value}-level evaluator availability="
        f"{availability.value}, but no such check reached a verdict"
    )


# ---------------------------------------------------------------------------
# Per-dimension derivations
# ---------------------------------------------------------------------------


def _derive_validity(report: CheckReport, dossier: TaskEvidenceDossier) -> DimensionEvidence:
    """Hard-invariant satisfaction.

    Ordering matters. A failed blocking check is a *known* violation and pins
    validity to 0.0. A blocking check that could not be evaluated is an
    *unknown*: reporting the pass rate of the checks that did run would assert
    the invariant holds on the strength of evidence that never addressed it.
    """
    blocking = [r for r in report.results if r.blocking]
    failed_blocking = [r for r in blocking if r.status is CheckStatus.FAIL]
    if failed_blocking:
        return DimensionEvidence(
            objective=Objective.VALIDITY,
            available=True,
            value=0.0,
            basis="blocking_failure",
            reason=(
                "blocking check(s) failed: "
                + ", ".join(sorted(r.check_id for r in failed_blocking))
            ),
            inputs={"n_blocking_failed": float(len(failed_blocking))},
        )

    undecided_blocking = [r for r in blocking if not r.decided]
    if undecided_blocking:
        return DimensionEvidence(
            objective=Objective.VALIDITY,
            available=False,
            basis="blocking_unavailable",
            reason=(
                "blocking check(s) could not be evaluated, so the hard invariant is "
                "unverified, not satisfied: "
                + ", ".join(sorted(r.check_id for r in undecided_blocking))
            ),
            inputs={"n_blocking_undecided": float(len(undecided_blocking))},
        )

    hard = report.by_level(CheckLevel.HARD)
    rate = _pass_rate(hard)
    if rate is None:
        return DimensionEvidence(
            objective=Objective.VALIDITY,
            available=False,
            basis="no_hard_checks",
            reason=_availability_note(dossier, CheckLevel.HARD),
            inputs={"n_hard_checks": float(len(hard))},
        )
    return DimensionEvidence(
        objective=Objective.VALIDITY,
        available=True,
        value=rate,
        basis="hard_pass_rate",
        reason=f"{len(_decided(hard))} hard check(s) decided, no blocking failure",
        inputs={
            "n_hard_decided": float(len(_decided(hard))),
            "n_hard_total": float(len(hard)),
        },
    )


def _derive_evidence(
    report: CheckReport, ledger: EvidenceLedger, dossier: TaskEvidenceDossier
) -> DimensionEvidence:
    """Are the design decisions and the artifacts actually justified?

    Two independent signals, blended with a declared weight:

    * the fraction of structural decisions carrying load-bearing justification
      (``EvidenceLedger.evidence_coverage``);
    * the pass rate of artifact-level provenance checks.

    An *empty* ledger is undefined rather than 0.0 — the coverage of zero
    decisions is 0/0 — but it is recorded as a note, because a compiled
    workflow with no recorded decisions is itself a defect.
    """
    notes: list[str] = []
    inputs: dict[str, float] = {}

    ledger_component: Optional[float] = None
    if ledger.records:
        ledger_component = ledger.evidence_coverage()
        inputs["ledger_coverage"] = ledger_component
        inputs["n_decisions"] = float(len(ledger.records))
        inputs["observed_fraction"] = ledger.observed_fraction()
    else:
        notes.append(
            "evidence ledger holds no decisions; coverage of zero decisions is "
            "undefined (a compiled workflow with no recorded decisions is a defect)"
        )

    provenance_checks = [
        r for r in report.by_level(CheckLevel.ARTIFACT) if _matches(r, PROVENANCE_CHECK_TOKENS)
    ]
    provenance_component = _pass_rate(provenance_checks)
    if provenance_component is None:
        notes.append(
            f"no artifact-level provenance check reached a verdict "
            f"({len(provenance_checks)} present); "
            + _availability_note(dossier, CheckLevel.ARTIFACT)
        )
    else:
        inputs["provenance_pass_rate"] = provenance_component
        inputs["n_provenance_decided"] = float(len(_decided(provenance_checks)))

    if ledger_component is None and provenance_component is None:
        return DimensionEvidence(
            objective=Objective.EVIDENCE,
            available=False,
            basis="no_evidence_signal",
            reason=(
                "neither the design-evidence ledger nor any provenance check yielded a "
                "measurement; justification is unassessed, not absent"
            ),
            inputs=inputs,
            notes=notes,
        )

    if ledger_component is None:
        assert provenance_component is not None  # both-None handled above
        value, basis = provenance_component, "provenance_checks_only"
    elif provenance_component is None:
        value, basis = ledger_component, "ledger_only"
    else:
        value = (
            EVIDENCE_LEDGER_WEIGHT * ledger_component
            + (1.0 - EVIDENCE_LEDGER_WEIGHT) * provenance_component
        )
        basis = "ledger_provenance_blend"

    return DimensionEvidence(
        objective=Objective.EVIDENCE,
        available=True,
        value=float(value),
        basis=basis,
        reason=(
            f"blend weight {EVIDENCE_LEDGER_WEIGHT} on the ledger; "
            f"ledger={_fmt(ledger_component)}, provenance={_fmt(provenance_component)}"
        ),
        inputs=inputs,
        notes=notes,
    )


def _derive_robustness(report: CheckReport, dossier: TaskEvidenceDossier) -> DimensionEvidence:
    """Stability under reseeding, thresholds, and perturbation.

    Only sensitivity / negative-control / reproducibility style checks count.
    ``required_steps_ran`` passing tells us the pipeline executed, which is not
    evidence that the conclusion is stable. A robustness check that *failed*
    scores 0.0 (we looked, and it was fragile); no such check at all is
    unavailable (we never looked).
    """
    candidates = [
        r
        for r in report.results
        if r.level in (CheckLevel.PROCESS, CheckLevel.RESOURCE)
        and (
            _matches(r, ROBUSTNESS_CHECK_TOKENS)
            # A process check whose evidence is statistical (resampling,
            # bootstrap, control comparison) is a robustness measurement even
            # when its id does not say so.
            or (r.level is CheckLevel.PROCESS and r.source is SignalSource.STATISTICAL)
        )
    ]
    rate = _pass_rate(candidates)
    if rate is None:
        undecided = [r.check_id for r in candidates if not r.decided]
        reason = "no robustness check reached a verdict; " + _availability_note(
            dossier, CheckLevel.PROCESS
        )
        if undecided:
            reason += f"; present but unavailable: {', '.join(sorted(undecided))}"
        return DimensionEvidence(
            objective=Objective.ROBUSTNESS,
            available=False,
            basis="no_robustness_checks",
            reason=reason,
            inputs={"n_candidates": float(len(candidates))},
        )
    return DimensionEvidence(
        objective=Objective.ROBUSTNESS,
        available=True,
        value=rate,
        basis="robustness_pass_rate",
        reason=(
            f"{len(_decided(candidates))} sensitivity / negative-control / reproducibility "
            "check(s) decided"
        ),
        inputs={
            "n_decided": float(len(_decided(candidates))),
            "n_candidates": float(len(candidates)),
        },
    )


def _derive_scientific_utility(
    report: CheckReport, dossier: TaskEvidenceDossier
) -> DimensionEvidence:
    """Claim support, sensitivity coverage, provenance depth.

    This is the dimension most often unmeasurable, and the one where scoring an
    absent evaluator as 0.0 would do the most damage: an open-ended task with
    no claim oracle would look identical to a task whose claim was checked and
    found unsupported.
    """
    claim_checks = report.by_level(CheckLevel.CLAIM)
    rate = _pass_rate(claim_checks)
    if rate is None:
        undecided = [r.check_id for r in claim_checks if not r.decided]
        reason = _availability_note(dossier, CheckLevel.CLAIM)
        if undecided:
            reason = (
                f"claim-level checks present but undecided "
                f"({', '.join(sorted(undecided))}); {reason}"
            )
        return DimensionEvidence(
            objective=Objective.SCIENTIFIC_UTILITY,
            available=False,
            basis="no_claim_checks",
            reason=reason,
            inputs={"n_claim_checks": float(len(claim_checks))},
        )
    return DimensionEvidence(
        objective=Objective.SCIENTIFIC_UTILITY,
        available=True,
        value=rate,
        basis="claim_pass_rate",
        reason=f"{len(_decided(claim_checks))} claim check(s) decided",
        inputs={
            "n_decided": float(len(_decided(claim_checks))),
            "n_claim_checks": float(len(claim_checks)),
        },
    )


def _derive_cost(cost: CostProfile, limits: ResourceLimits) -> DimensionEvidence:
    """Spend as a fraction of the declared budget (higher is worse).

    When several budgets are declared, the *tightest* one wins: a run at 95% of
    its token budget and 10% of its dollar budget is a 95%-spent run. Values
    above 1.0 are left unclamped so an over-budget run stays strictly worse
    than one that merely exhausted its budget.

    With no declared budget there is no scale, so the absolute dollar figure is
    used. That is consistent within one comparison (every candidate shares the
    dossier's limits) and is recorded in ``basis`` so nobody compares a
    budget fraction against a dollar amount by accident.
    """
    ratios: dict[str, float] = {}
    if limits.max_usd is not None and limits.max_usd > 0:
        ratios["usd_fraction"] = cost.usd / limits.max_usd
    if limits.max_tokens is not None and limits.max_tokens > 0:
        ratios["token_fraction"] = cost.tokens / limits.max_tokens

    notes: list[str] = []
    if ratios:
        binding = max(sorted(ratios.items()), key=lambda kv: kv[1])
        return DimensionEvidence(
            objective=Objective.COST,
            available=True,
            value=binding[1],
            basis="budget_fraction",
            reason=f"tightest declared budget is {binding[0]} at {binding[1]:.4f}",
            inputs={**ratios, "usd": cost.usd, "tokens": float(cost.tokens)},
            notes=notes,
        )

    if cost.tokens > 0 and cost.usd == 0.0:
        notes.append(
            "no budget declared and no dollar cost recorded while tokens were spent; "
            "the cost signal is incomplete"
        )
    return DimensionEvidence(
        objective=Objective.COST,
        available=True,
        value=cost.usd,
        basis="absolute_usd",
        reason="no usd/token budget declared in limits; using absolute spend",
        inputs={"usd": cost.usd, "tokens": float(cost.tokens)},
        notes=notes,
    )


def _derive_latency(cost: CostProfile, limits: ResourceLimits) -> DimensionEvidence:
    """Wall-time as a fraction of the declared limit (higher is worse).

    ``cost.latency_s`` is a *recorded measurement* carried on the cost profile,
    not a clock read during the decision — the determinism constraint forbids
    the latter, not the former.
    """
    if limits.max_wall_time_s is not None and limits.max_wall_time_s > 0:
        fraction = cost.latency_s / limits.max_wall_time_s
        return DimensionEvidence(
            objective=Objective.LATENCY,
            available=True,
            value=fraction,
            basis="wall_time_fraction",
            reason=f"{cost.latency_s:.4f}s against a {limits.max_wall_time_s:.4f}s limit",
            inputs={"latency_s": cost.latency_s, "max_wall_time_s": limits.max_wall_time_s},
        )
    return DimensionEvidence(
        objective=Objective.LATENCY,
        available=True,
        value=cost.latency_s,
        basis="absolute_seconds",
        reason="no wall-time limit declared in limits; using absolute latency",
        inputs={"latency_s": cost.latency_s},
    )


def _downstream_verified(graph: ExecGraph, node_id: str) -> bool:
    """True iff some verifying node is reachable downstream of ``node_id``.

    Breadth-first over successors with sorted expansion, so the traversal is
    order-independent. Visited-set guards a corrupted (cyclic) graph.
    """
    seen = {node_id}
    frontier = sorted(graph.successors(node_id))
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        node = graph.node(current)
        if node is not None and node.role in VERIFYING_ROLES:
            return True
        frontier.extend(sorted(graph.successors(current)))
        frontier.sort()
    return False


def _derive_risk(
    report: CheckReport,
    workflow: Optional[CompiledWorkflow],
    library: Optional[ComponentLibrary],
) -> DimensionEvidence:
    """Unverified steps and silent-failure exposure (higher is worse).

    Structural computation when the workflow is available: every component node
    contributes ``UNVERIFIED_WEIGHT`` if nothing downstream verifies it and
    ``SILENT_EXPOSURE_WEIGHT`` if its capability card declares a silent failure
    mode — *or if no card is available for it at all*. Treating an unknown
    component as safe would be the same mistake as treating an unavailable
    evaluator as a pass.

    Without the workflow we fall back to the static-analysis checks
    (``missing_verifier``, ``silent_failure_exposure``), reading a FAIL as
    "the defect is present", which is the convention the check stack uses.
    """
    if workflow is not None:
        graph = workflow.graph()
        component_nodes = graph.component_nodes
        if not component_nodes:
            return DimensionEvidence(
                objective=Objective.RISK,
                available=False,
                basis="no_component_nodes",
                reason="workflow contains no component nodes; risk is undefined",
            )
        unverified = 0
        silent = 0
        unknown_cards: list[str] = []
        for node in sorted(component_nodes, key=lambda n: n.node_id):
            verified = _downstream_verified(graph, node.node_id)
            if not verified:
                unverified += 1
            card: Optional[CapabilityCard] = (
                library.get(node.component) if library is not None and node.component else None
            )
            if card is None:
                unknown_cards.append(node.component or node.node_id)
                silent += 1
            elif card.has_silent_failure_mode():
                silent += 1
        total = float(len(component_nodes))
        unverified_fraction = unverified / total
        silent_fraction = silent / total
        value = (
            UNVERIFIED_WEIGHT * unverified_fraction + SILENT_EXPOSURE_WEIGHT * silent_fraction
        )
        notes = []
        if unknown_cards:
            notes.append(
                "no capability card for "
                + ", ".join(sorted(set(unknown_cards)))
                + "; counted as silent-failure exposed because an unknown component "
                "cannot be shown to be safe"
            )
        return DimensionEvidence(
            objective=Objective.RISK,
            available=True,
            value=value,
            basis="structural_exposure",
            reason=(
                f"{unverified}/{len(component_nodes)} component nodes lack a downstream "
                f"verifier; {silent}/{len(component_nodes)} carry silent-failure exposure"
            ),
            inputs={
                "unverified_fraction": unverified_fraction,
                "silent_fraction": silent_fraction,
                "n_component_nodes": total,
            },
            notes=notes,
        )

    verifier_checks = [r for r in report.results if _matches(r, VERIFIER_CHECK_TOKENS)]
    silent_checks = [r for r in report.results if _matches(r, SILENT_FAILURE_CHECK_TOKENS)]
    verifier_rate = _pass_rate(verifier_checks)
    silent_rate = _pass_rate(silent_checks)
    if verifier_rate is None and silent_rate is None:
        return DimensionEvidence(
            objective=Objective.RISK,
            available=False,
            basis="no_risk_signal",
            reason=(
                "no workflow supplied and no verifier / silent-failure check reached a "
                "verdict; exposure is unassessed"
            ),
        )
    parts: list[tuple[float, float]] = []
    inputs: dict[str, float] = {}
    if verifier_rate is not None:
        parts.append((UNVERIFIED_WEIGHT, 1.0 - verifier_rate))
        inputs["verifier_check_pass_rate"] = verifier_rate
    if silent_rate is not None:
        parts.append((SILENT_EXPOSURE_WEIGHT, 1.0 - silent_rate))
        inputs["silent_check_pass_rate"] = silent_rate
    weight_sum = sum(w for w, _ in parts)
    value = sum(w * v for w, v in parts) / weight_sum
    return DimensionEvidence(
        objective=Objective.RISK,
        available=True,
        value=value,
        basis="check_derived_exposure",
        reason="derived from static-analysis checks because no workflow was supplied",
        inputs=inputs,
    )


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4f}"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def derive_utility(
    report: CheckReport,
    *,
    cost: CostProfile,
    ledger: EvidenceLedger,
    limits: ResourceLimits,
    dossier: TaskEvidenceDossier,
    workflow: Optional[CompiledWorkflow] = None,
    library: Optional[ComponentLibrary] = None,
) -> UtilityDerivation:
    """Compute the utility vector *and* the reasoning behind every coordinate.

    ``workflow`` and ``library`` are optional because the risk dimension is the
    only one that needs structure. Supplying them yields a structural exposure
    measurement; omitting them falls back to whatever the static-analysis
    checks recorded, and failing that the dimension is unavailable rather than
    optimistically zero.
    """
    dimensions = [
        _derive_validity(report, dossier),
        _derive_evidence(report, ledger, dossier),
        _derive_robustness(report, dossier),
        _derive_scientific_utility(report, dossier),
        _derive_cost(cost, limits),
        _derive_latency(cost, limits),
        _derive_risk(report, workflow, library),
    ]

    vector = UtilityVector()
    for dimension in dimensions:
        if dimension.available and dimension.value is not None:
            vector = vector.with_value(dimension.objective, dimension.value)
        else:
            # Never 0.0. See the module docstring: an unmeasured dimension has
            # to stay unmeasured or the comparison lies.
            vector = vector.mark_unavailable(dimension.objective)

    return UtilityDerivation(vector=vector, dimensions=dimensions)


def utility_from_report(
    report: CheckReport,
    *,
    cost: CostProfile,
    ledger: EvidenceLedger,
    limits: ResourceLimits,
    dossier: TaskEvidenceDossier,
    workflow: Optional[CompiledWorkflow] = None,
    library: Optional[ComponentLibrary] = None,
) -> UtilityVector:
    """The interface-contract entry point: a report in, a utility vector out."""
    return derive_utility(
        report,
        cost=cost,
        ledger=ledger,
        limits=limits,
        dossier=dossier,
        workflow=workflow,
        library=library,
    ).vector


__all__ = [
    "EVIDENCE_LEDGER_WEIGHT",
    "PROVENANCE_CHECK_TOKENS",
    "ROBUSTNESS_CHECK_TOKENS",
    "SILENT_EXPOSURE_WEIGHT",
    "SILENT_FAILURE_CHECK_TOKENS",
    "UNVERIFIED_WEIGHT",
    "VERIFIER_CHECK_TOKENS",
    "VERIFYING_ROLES",
    "DimensionEvidence",
    "UtilityDerivation",
    "derive_utility",
    "utility_from_report",
]
