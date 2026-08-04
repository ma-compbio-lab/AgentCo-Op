"""Repair-quality metrics — the numbers the paper reports.

These are pure functions over *recorded* runs plus *injected* ground truth.
Nothing here executes a workflow, consults a model, or reads a clock; given the
same records they return the same numbers on any machine.

Two design decisions run through the whole module.

**Every metric states its denominator.** Repair metrics are trivially gameable
by quietly changing what you divide by — "repair success" over runs where a
patch was committed is a very different number from the same ratio over all
faulty runs. So each function returns a :class:`MetricValue` carrying the
numerator, the denominator, a prose description of exactly which runs the
denominator contains, and how many runs were *excluded* because the record did
not determine the outcome.

**An undefined metric is ``None``, never 0.0.** With no healthy control runs
there is no false-repair rate; reporting 0.0 would advertise a property that
was never measured. This is the same rule the utility vector enforces for
unavailable evaluators, applied to the metric layer.

The record types below are a metrics-facing *view* of a run, deliberately
independent of how runs are stored. The benchmark harness populates them from
its own ``RunRecord`` / ``InjectedFault`` objects; keeping the coupling one-way
means the metric definitions cannot drift with the storage schema.

Definitions, with denominators:

============================== ============================================
metric                         denominator
============================== ============================================
failure-detection recall       runs with an injected fault
false-repair rate              healthy runs whose pre-repair health was
                               established (no failing check before)
localization accuracy          faulty runs that produced a localization
diagnosis top-k accuracy       faulty runs that produced a ranking
patch precision                patches at the requested stage, over all runs
repair success                 runs with a committed patch and a determinate
                               post-repair outcome
collateral regression rate     runs with a committed patch and comparable
                               before/after check maps
utility improvement            per objective: runs where both vectors
                               measured that objective
repair regret                  per objective: runs with an oracle vector that
                               measured that objective
time-to-recovery               runs that recovered (censored count reported)
escalation calibration         escalated runs / runs warranting escalation
============================== ============================================
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.checks import CheckReport, CheckStatus
from agentcoop.ir.faults import FAULT_TAXONOMY, BlameTarget, FaultClass, RepairTier
from agentcoop.ir.utility import Objective, UtilityVector, dominates

# ---------------------------------------------------------------------------
# Recorded inputs
# ---------------------------------------------------------------------------


#: Ordering used when one check id appears several times in a report. Lower is
#: worse; the worst status wins, so a check that failed anywhere is recorded as
#: failed rather than being averaged away.
_STATUS_SEVERITY: dict[CheckStatus, int] = {
    CheckStatus.FAIL: 0,
    CheckStatus.UNAVAILABLE: 1,
    CheckStatus.INCONCLUSIVE: 2,
    CheckStatus.WARN: 3,
    CheckStatus.PASS: 4,
}

#: Statuses that count as "this check is currently satisfied".
_OK_STATUSES: frozenset[CheckStatus] = frozenset({CheckStatus.PASS, CheckStatus.WARN})


def check_status_map(report: CheckReport) -> dict[str, CheckStatus]:
    """Flatten a report to ``check_id -> worst status``.

    Repeated ids collapse to their worst status: if a check passed on one node
    and failed on another, the workflow does not satisfy it.
    """
    out: dict[str, CheckStatus] = {}
    for result in report.results:
        current = out.get(result.check_id)
        if current is None or _STATUS_SEVERITY[result.status] < _STATUS_SEVERITY[current]:
            out[result.check_id] = result.status
    return out


class GroundTruthFault(BaseModel):
    """The fault that was actually injected, known only to the harness."""

    model_config = ConfigDict(extra="forbid")

    fault_id: str
    fault_class: FaultClass
    #: Identifier of the node / edge / artifact genuinely responsible.
    blame_target: str
    #: ``NONE`` means "do not constrain the blame kind" when scoring.
    blame_kind: BlameTarget = BlameTarget.NONE
    mechanism: str = ""
    #: Signal ids a competent detector is expected to raise. Used only by the
    #: strict variant of detection recall.
    expected_signals: list[str] = Field(default_factory=list)
    #: Patch families that would genuinely address this fault. Defaults to the
    #: taxonomy's admissible list, which is the contract the repair planner is
    #: held to.
    corrective_patch_families: list[str] = Field(default_factory=list)
    #: Override for whether a competent system should escalate instead of
    #: auto-repairing. ``None`` defers to the taxonomy.
    escalation_warranted: Optional[bool] = None

    def admissible_patch_families(self) -> list[str]:
        if self.corrective_patch_families:
            return list(self.corrective_patch_families)
        return list(FAULT_TAXONOMY[self.fault_class].admissible_patches)

    def should_escalate(self) -> bool:
        if self.escalation_warranted is not None:
            return self.escalation_warranted
        return FAULT_TAXONOMY[self.fault_class].escalate


class RepairAttempt(BaseModel):
    """One repair transaction as recorded by the repair loop."""

    model_config = ConfigDict(extra="forbid")

    #: Monotone counter within the run. Not a timestamp — time-to-recovery is
    #: measured in transactions precisely so it stays machine-independent.
    attempt_index: int
    patch_id: str
    patch_family: str
    tier: RepairTier = RepairTier.CONTRACT_REPAIR
    #: Term / node / edge the patch was aimed at.
    target: str = ""
    #: Fault class the patch was acting on, for hypothesis/patch coherence.
    hypothesis_class: Optional[FaultClass] = None
    shadow_ok: Optional[bool] = None
    committed: bool = False
    rolled_back: bool = False
    #: Whether the originally failing assertion passed after this attempt.
    #: Used only to index time-to-recovery; the run-level verdict is computed
    #: from the before/after check maps, which cannot be talked up.
    resolved_original_failure: bool = False


class RunOutcome(BaseModel):
    """Everything one recorded run contributes to the repair metrics."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str = ""
    #: ``None`` marks a healthy control run — the denominator of false repair.
    ground_truth: Optional[GroundTruthFault] = None
    #: Signal ids raised by the detectors. Empty means nothing fired.
    detected_signals: list[str] = Field(default_factory=list)
    localized_to: Optional[str] = None
    localized_kind: BlameTarget = BlameTarget.NONE
    #: Fault classes ranked most likely first.
    diagnosis_ranking: list[FaultClass] = Field(default_factory=list)
    attempts: list[RepairAttempt] = Field(default_factory=list)
    #: ``check_id -> status`` before and after repair. Build with
    #: :func:`check_status_map`.
    checks_before: dict[str, CheckStatus] = Field(default_factory=dict)
    checks_after: dict[str, CheckStatus] = Field(default_factory=dict)
    utility_before: Optional[UtilityVector] = None
    utility_after: Optional[UtilityVector] = None
    #: Utility an oracle patch would have achieved, for regret.
    oracle_utility: Optional[UtilityVector] = None
    oracle_patch_family: Optional[str] = None
    escalated: bool = False
    notes: list[str] = Field(default_factory=list)

    # -- derived views ------------------------------------------------------

    @property
    def faulty(self) -> bool:
        return self.ground_truth is not None

    @property
    def detected(self) -> bool:
        return bool(self.detected_signals)

    def ordered_attempts(self) -> list[RepairAttempt]:
        return sorted(self.attempts, key=lambda a: (a.attempt_index, a.patch_id))

    def committed_attempts(self) -> list[RepairAttempt]:
        return [a for a in self.ordered_attempts() if a.committed]

    @property
    def repair_attempted(self) -> bool:
        return bool(self.committed_attempts())

    def failing_checks_before(self) -> list[str]:
        return sorted(
            check_id
            for check_id, status in self.checks_before.items()
            if status is CheckStatus.FAIL
        )

    def health_established_before(self) -> bool:
        """True when the record proves the run was healthy before repair.

        Requires an actual pre-repair evaluation: an empty ``checks_before`` is
        "we did not look", which is not the same as "nothing was wrong".
        """
        return bool(self.checks_before) and not self.failing_checks_before()

    def original_failure_resolved(self) -> Optional[bool]:
        """Did every check that was failing before now pass?

        ``None`` when the record cannot decide: nothing was failing to begin
        with, or no post-repair evaluation was recorded. A check that was
        failing and is *absent* from a non-empty post-repair map counts as
        unresolved — a check that stopped being evaluated has not been fixed.
        """
        failing = self.failing_checks_before()
        if not failing or not self.checks_after:
            return None
        return all(self.checks_after.get(check_id) in _OK_STATUSES for check_id in failing)

    def regressions_measurable(self) -> bool:
        return bool(self.checks_before) and bool(self.checks_after)

    def collateral_regressions(self, *, count_dropped_checks: bool = True) -> list[str]:
        """Checks that were satisfied before repair and are not satisfied now.

        "Not satisfied" includes ``UNAVAILABLE`` and ``INCONCLUSIVE``: a patch
        that makes a previously passing check unevaluable has removed our
        ability to certify that property, which is a regression in evidence
        even though nothing turned red.

        ``count_dropped_checks`` governs checks that disappear entirely. The
        default counts them, because silently dropping a check is the cheapest
        way to make a report look clean.
        """
        if not self.regressions_measurable():
            return []
        out: list[str] = []
        for check_id in sorted(self.checks_before):
            if self.checks_before[check_id] not in _OK_STATUSES:
                continue
            if check_id not in self.checks_after:
                if count_dropped_checks:
                    out.append(check_id)
                continue
            if self.checks_after[check_id] not in _OK_STATUSES:
                out.append(check_id)
        return out


# ---------------------------------------------------------------------------
# Metric containers
# ---------------------------------------------------------------------------


class MetricValue(BaseModel):
    """A ratio together with everything needed to audit it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    #: ``None`` when the denominator is empty. Undefined, not zero.
    value: Optional[float] = None
    numerator: float = 0.0
    denominator: float = 0.0
    denominator_description: str = ""
    #: Runs dropped because the record did not determine the outcome.
    excluded: int = 0
    notes: list[str] = Field(default_factory=list)

    @property
    def defined(self) -> bool:
        return self.value is not None


def _ratio(
    name: str,
    numerator: float,
    denominator: float,
    denominator_description: str,
    *,
    excluded: int = 0,
    notes: Sequence[str] = (),
) -> MetricValue:
    return MetricValue(
        name=name,
        value=(numerator / denominator) if denominator > 0 else None,
        numerator=float(numerator),
        denominator=float(denominator),
        denominator_description=denominator_description,
        excluded=excluded,
        notes=list(notes),
    )


# ---------------------------------------------------------------------------
# Detection, localization, diagnosis
# ---------------------------------------------------------------------------


def failure_detection_recall(
    runs: Sequence[RunOutcome], *, require_expected_signal: bool = False
) -> MetricValue:
    """Fraction of injected faults that produced any detector signal.

    Denominator: runs with an injected fault. Runs where the fault was injected
    but the harness recorded no expected signals are still counted — a fault
    that produces no observable symptom is a detection failure, not an excused
    one.

    ``require_expected_signal=True`` demands that one of the *specific* signals
    the harness expected actually fired, which distinguishes "noticed this
    fault" from "noticed something".
    """
    faulty = [r for r in runs if r.faulty]
    hits = 0
    skipped_strict = 0
    for run in faulty:
        assert run.ground_truth is not None
        if require_expected_signal and run.ground_truth.expected_signals:
            expected = set(run.ground_truth.expected_signals)
            if expected & set(run.detected_signals):
                hits += 1
        elif require_expected_signal and not run.ground_truth.expected_signals:
            # No expectation was declared; fall back to "anything fired" and
            # say so rather than silently scoring it either way.
            skipped_strict += 1
            if run.detected:
                hits += 1
        elif run.detected:
            hits += 1
    notes = []
    if skipped_strict:
        notes.append(
            f"{skipped_strict} faulty run(s) declared no expected signals; scored "
            "leniently (any signal counts)"
        )
    return _ratio(
        "failure_detection_recall",
        hits,
        len(faulty),
        "runs with an injected fault",
        notes=notes,
    )


def false_repair_rate(runs: Sequence[RunOutcome]) -> MetricValue:
    """Fraction of healthy runs where the system committed a patch anyway.

    Denominator: runs with no injected fault *and* an established clean
    pre-repair state (a non-empty ``checks_before`` with no failures). Healthy
    runs whose health was never evaluated are excluded rather than assumed
    clean — otherwise a harness that forgot to evaluate would flatter the
    system.
    """
    healthy = [r for r in runs if not r.faulty]
    eligible = [r for r in healthy if r.health_established_before()]
    excluded = len(healthy) - len(eligible)
    false_repairs = sum(1 for r in eligible if r.repair_attempted)
    notes = []
    if excluded:
        notes.append(
            f"{excluded} healthy run(s) excluded: no pre-repair evaluation recorded, "
            "so their health could not be established"
        )
    return _ratio(
        "false_repair_rate",
        false_repairs,
        len(eligible),
        "healthy runs with an established clean pre-repair state",
        excluded=excluded,
        notes=notes,
    )


def localization_accuracy(
    runs: Sequence[RunOutcome], *, require_kind_match: bool = True
) -> MetricValue:
    """Fraction of localizations that named the truly responsible entity.

    Denominator: faulty runs that produced a localization at all. Runs where
    nothing was detected are excluded — they are counted by
    :func:`failure_detection_recall`, and folding them in here would conflate
    two different failures. Multiply the two for an end-to-end rate.

    With ``require_kind_match`` the blame *kind* must match too, so blaming the
    right identifier as a node when the truth is an edge does not score. A
    ground truth of ``BlameTarget.NONE`` leaves the kind unconstrained.
    """
    faulty = [r for r in runs if r.faulty]
    localized = [r for r in faulty if r.localized_to is not None]
    excluded = len(faulty) - len(localized)
    correct = 0
    for run in localized:
        truth = run.ground_truth
        assert truth is not None
        if run.localized_to != truth.blame_target:
            continue
        if (
            require_kind_match
            and truth.blame_kind is not BlameTarget.NONE
            and run.localized_kind is not truth.blame_kind
        ):
            continue
        correct += 1
    notes = []
    if excluded:
        notes.append(f"{excluded} faulty run(s) produced no localization")
    return _ratio(
        "localization_accuracy",
        correct,
        len(localized),
        "faulty runs that produced a localization",
        excluded=excluded,
        notes=notes,
    )


def diagnosis_top_k_accuracy(runs: Sequence[RunOutcome], k: int = 1) -> MetricValue:
    """Fraction of faulty runs whose true fault class is in the top ``k``.

    Denominator: faulty runs that produced a non-empty ranking. A run that
    declined to guess (empty ranking) is excluded and reported as such — under
    the repair policy, refusing to guess on a high-entropy diagnosis is correct
    behaviour and must not be scored as a wrong answer here.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    faulty = [r for r in runs if r.faulty]
    ranked = [r for r in faulty if r.diagnosis_ranking]
    excluded = len(faulty) - len(ranked)
    hits = 0
    for run in ranked:
        assert run.ground_truth is not None
        if run.ground_truth.fault_class in run.diagnosis_ranking[:k]:
            hits += 1
    notes = []
    if excluded:
        notes.append(
            f"{excluded} faulty run(s) produced no ranked diagnosis (abstention is not "
            "scored as a wrong answer)"
        )
    return _ratio(
        f"diagnosis_top{k}_accuracy",
        hits,
        len(ranked),
        f"faulty runs that produced a ranked diagnosis (top-{k})",
        excluded=excluded,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Patch quality
# ---------------------------------------------------------------------------


def patch_precision(
    runs: Sequence[RunOutcome],
    *,
    stage: Literal["committed", "proposed"] = "committed",
    require_target_match: bool = False,
) -> MetricValue:
    """Fraction of patches whose family is admissible for the true fault.

    This is the metric that catches symptom-to-action dispatch: retrying a node
    is not an admissible response to an artifact-contract fault, however often
    it makes the test go green. Admissibility comes from
    ``FAULT_TAXONOMY[fault_class].admissible_patches`` unless the harness
    declared a narrower corrective set.

    Denominator: every patch at ``stage``, across all runs. Patches applied to
    healthy control runs are included and can never be correct — a repair with
    no fault to repair is by construction imprecise.

    ``require_target_match`` additionally requires the patch to be aimed at the
    ground-truth blame target, i.e. the right fix in the right place.
    """
    total = 0
    correct = 0
    for run in runs:
        attempts = (
            run.committed_attempts() if stage == "committed" else run.ordered_attempts()
        )
        truth = run.ground_truth
        admissible = set(truth.admissible_patch_families()) if truth else set()
        for attempt in attempts:
            total += 1
            if truth is None:
                continue
            if attempt.patch_family not in admissible:
                continue
            if require_target_match and attempt.target != truth.blame_target:
                continue
            correct += 1
    return _ratio(
        "patch_precision",
        correct,
        total,
        f"all {stage} patches across all runs",
    )


def repair_success_rate(
    runs: Sequence[RunOutcome],
    *,
    require_no_regression: bool = True,
    count_dropped_checks: bool = True,
) -> MetricValue:
    """Fraction of repairs that fixed the failure *without breaking anything*.

    Denominator: runs where at least one patch was committed and the record
    determines the post-repair outcome. Runs that escalated without patching are
    excluded — declining to patch is a legitimate outcome and scoring it as a
    failed repair would punish exactly the behaviour the escalation policy is
    supposed to produce.

    With ``require_no_regression`` (the default) a run only counts as a success
    if no previously satisfied check stopped being satisfied. "The failing test
    now passes" is not success on its own; that conflation is the reason this
    metric is defined this way.
    """
    attempted = [r for r in runs if r.repair_attempted]
    eligible = [r for r in attempted if r.original_failure_resolved() is not None]
    excluded = len(attempted) - len(eligible)
    successes = 0
    for run in eligible:
        if not run.original_failure_resolved():
            continue
        if require_no_regression and run.collateral_regressions(
            count_dropped_checks=count_dropped_checks
        ):
            continue
        successes += 1
    notes = []
    if excluded:
        notes.append(
            f"{excluded} run(s) with a committed patch excluded: no failing check before, "
            "or no post-repair evaluation recorded"
        )
    if require_no_regression:
        notes.append("success requires the original failure resolved AND no regression")
    return _ratio(
        "repair_success_rate",
        successes,
        len(eligible),
        "runs with a committed patch and a determinate post-repair outcome",
        excluded=excluded,
        notes=notes,
    )


def collateral_regression_rate(
    runs: Sequence[RunOutcome], *, count_dropped_checks: bool = True
) -> MetricValue:
    """Fraction of repaired runs that broke something that used to work.

    Denominator: runs with a committed patch and comparable before/after check
    maps. See :meth:`RunOutcome.collateral_regressions` for what counts as
    broken — notably, a check that became unevaluable counts.
    """
    attempted = [r for r in runs if r.repair_attempted]
    eligible = [r for r in attempted if r.regressions_measurable()]
    excluded = len(attempted) - len(eligible)
    regressed = sum(
        1
        for r in eligible
        if r.collateral_regressions(count_dropped_checks=count_dropped_checks)
    )
    notes = []
    if excluded:
        notes.append(
            f"{excluded} repaired run(s) excluded: before/after check maps not comparable"
        )
    return _ratio(
        "collateral_regression_rate",
        regressed,
        len(eligible),
        "runs with a committed patch and comparable before/after check maps",
        excluded=excluded,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Utility movement
# ---------------------------------------------------------------------------


class ObjectiveDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: Objective
    #: Mean *oriented* delta (positive is always better, including for cost).
    mean_delta: Optional[float] = None
    n_runs: int = 0
    n_improved: int = 0
    n_regressed: int = 0
    n_unchanged: int = 0


class UtilityImprovementReport(BaseModel):
    """How repair moved the utility vector, without scalarizing it."""

    model_config = ConfigDict(extra="forbid")

    per_objective: list[ObjectiveDelta] = Field(default_factory=list)
    n_runs: int = 0
    #: Runs where the post-repair vector Pareto-dominates the pre-repair one.
    n_dominating: int = 0
    #: Runs where the *pre*-repair vector dominates — an unambiguous regression.
    n_dominated: int = 0
    n_incomparable: int = 0
    #: ``run_id: objective`` pairs where a dimension that was measured before
    #: repair became unavailable after. A mean delta cannot show this, because
    #: the dimension simply drops out of the average.
    evaluability_losses: list[str] = Field(default_factory=list)
    denominator_description: str = (
        "runs with a committed patch and both a pre- and post-repair utility vector"
    )

    def delta(self, objective: Objective) -> Optional[ObjectiveDelta]:
        for item in self.per_objective:
            if item.objective is objective:
                return item
        return None


def utility_improvement(
    runs: Sequence[RunOutcome], *, committed_only: bool = True
) -> UtilityImprovementReport:
    """Per-objective utility movement across repair.

    Deltas are computed on *oriented* values, so a cost or latency reduction
    shows up as a positive improvement rather than a negative number that a
    reader has to remember to flip.

    Only dimensions both vectors measured contribute, mirroring
    :meth:`UtilityVector.comparable_with`. Dimensions that were measurable
    before and are not afterwards are recorded separately in
    ``evaluability_losses`` instead of quietly vanishing from the mean.
    """
    eligible = [
        r
        for r in runs
        if (r.repair_attempted or not committed_only)
        and r.utility_before is not None
        and r.utility_after is not None
    ]

    deltas: dict[Objective, list[float]] = {o: [] for o in Objective}
    improved: dict[Objective, int] = {o: 0 for o in Objective}
    regressed: dict[Objective, int] = {o: 0 for o in Objective}
    unchanged: dict[Objective, int] = {o: 0 for o in Objective}
    losses: list[str] = []
    n_dominating = n_dominated = n_incomparable = 0

    for run in eligible:
        before = run.utility_before
        after = run.utility_after
        assert before is not None and after is not None
        for objective in Objective:
            if objective in before.measured and objective not in after.measured:
                losses.append(f"{run.run_id}:{objective.value}")
        for objective in sorted(before.comparable_with(after), key=lambda o: o.value):
            a = after.oriented(objective)
            b = before.oriented(objective)
            if a is None or b is None:
                continue
            delta = a - b
            deltas[objective].append(delta)
            if delta > 1e-12:
                improved[objective] += 1
            elif delta < -1e-12:
                regressed[objective] += 1
            else:
                unchanged[objective] += 1
        if dominates(after, before):
            n_dominating += 1
        elif dominates(before, after):
            n_dominated += 1
        else:
            n_incomparable += 1

    per_objective = [
        ObjectiveDelta(
            objective=objective,
            mean_delta=(
                sum(deltas[objective]) / len(deltas[objective]) if deltas[objective] else None
            ),
            n_runs=len(deltas[objective]),
            n_improved=improved[objective],
            n_regressed=regressed[objective],
            n_unchanged=unchanged[objective],
        )
        for objective in Objective
    ]

    return UtilityImprovementReport(
        per_objective=per_objective,
        n_runs=len(eligible),
        n_dominating=n_dominating,
        n_dominated=n_dominated,
        n_incomparable=n_incomparable,
        evaluability_losses=sorted(losses),
    )


class ObjectiveRegret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: Objective
    #: Mean oriented shortfall against the oracle. Negative means the system
    #: beat the oracle patch on this axis, which is informative and is not
    #: clamped away.
    mean_regret: Optional[float] = None
    max_regret: Optional[float] = None
    n_runs: int = 0


class RepairRegretReport(BaseModel):
    """Distance from the best patch available, per objective.

    There is no aggregate scalar regret here on purpose: summing regret across
    non-commensurable axes would need exchange rates the system refuses to
    invent. ``oracle_dominates_rate`` is the scalarization-free summary — the
    fraction of runs where the oracle result Pareto-dominates what was achieved.
    """

    model_config = ConfigDict(extra="forbid")

    per_objective: list[ObjectiveRegret] = Field(default_factory=list)
    n_runs_with_oracle: int = 0
    n_oracle_dominates: int = 0
    oracle_dominates_rate: Optional[float] = None
    denominator_description: str = (
        "per objective: runs with an oracle vector where both the oracle and the "
        "achieved vector measured that objective"
    )

    def regret(self, objective: Objective) -> Optional[ObjectiveRegret]:
        for item in self.per_objective:
            if item.objective is objective:
                return item
        return None


def repair_regret(runs: Sequence[RunOutcome]) -> RepairRegretReport:
    """Oriented shortfall of the achieved utility against an oracle patch."""
    eligible = [r for r in runs if r.oracle_utility is not None and r.utility_after is not None]
    regrets: dict[Objective, list[float]] = {o: [] for o in Objective}
    n_dominated = 0
    for run in eligible:
        achieved = run.utility_after
        oracle = run.oracle_utility
        assert achieved is not None and oracle is not None
        for objective in sorted(oracle.comparable_with(achieved), key=lambda o: o.value):
            o_val = oracle.oriented(objective)
            a_val = achieved.oriented(objective)
            if o_val is None or a_val is None:
                continue
            regrets[objective].append(o_val - a_val)
        if dominates(oracle, achieved):
            n_dominated += 1

    per_objective = [
        ObjectiveRegret(
            objective=objective,
            mean_regret=(
                sum(regrets[objective]) / len(regrets[objective])
                if regrets[objective]
                else None
            ),
            max_regret=max(regrets[objective]) if regrets[objective] else None,
            n_runs=len(regrets[objective]),
        )
        for objective in Objective
    ]
    return RepairRegretReport(
        per_objective=per_objective,
        n_runs_with_oracle=len(eligible),
        n_oracle_dominates=n_dominated,
        oracle_dominates_rate=(n_dominated / len(eligible)) if eligible else None,
    )


# ---------------------------------------------------------------------------
# Time to recovery and escalation
# ---------------------------------------------------------------------------


class TimeToRecoveryReport(BaseModel):
    """How many repair transactions recovery took.

    Measured in transactions, not seconds: wall-clock is forbidden in any
    decision path and would make the number machine-dependent anyway.

    ``censored_runs`` is reported alongside the mean because a mean over only
    the runs that recovered is a survivorship statistic — a system that
    recovers once in twenty runs would otherwise report a beautiful "1.0".
    """

    model_config = ConfigDict(extra="forbid")

    mean_transactions: Optional[float] = None
    median_transactions: Optional[float] = None
    recovered_runs: int = 0
    censored_runs: int = 0
    attempted_runs: int = 0
    recovery_rate: Optional[float] = None
    denominator_description: str = (
        "mean/median over runs that recovered; recovery_rate over runs where repair "
        "was attempted"
    )
    notes: list[str] = Field(default_factory=list)


def time_to_recovery(runs: Sequence[RunOutcome]) -> TimeToRecoveryReport:
    """Transactions consumed before the original failure was resolved.

    A run counts as recovered only if the before/after check maps say the
    original failure is gone. The per-attempt ``resolved_original_failure``
    flag is used solely to locate *which* transaction did it; if no attempt
    claims it, the count falls back to the number of committed transactions and
    a note is recorded, so an under-instrumented harness degrades to an upper
    bound rather than to silence.
    """
    attempted = [r for r in runs if r.repair_attempted]
    counts: list[int] = []
    censored = 0
    notes: list[str] = []
    for run in attempted:
        if run.original_failure_resolved() is not True:
            censored += 1
            continue
        committed = run.committed_attempts()
        index = next(
            (i for i, a in enumerate(committed) if a.resolved_original_failure), None
        )
        if index is None:
            notes.append(
                f"run '{run.run_id}' recovered but no attempt is flagged as the fix; "
                "counted as the full number of committed transactions (upper bound)"
            )
            counts.append(len(committed))
        else:
            counts.append(index + 1)
    return TimeToRecoveryReport(
        mean_transactions=(sum(counts) / len(counts)) if counts else None,
        median_transactions=statistics.median(counts) if counts else None,
        recovered_runs=len(counts),
        censored_runs=censored,
        attempted_runs=len(attempted),
        recovery_rate=(len(counts) / len(attempted)) if attempted else None,
        notes=sorted(notes),
    )


class EscalationCalibrationReport(BaseModel):
    """Did the system escalate when — and only when — it should have?

    Ground truth for "should have" comes from the injected fault: either the
    harness said so explicitly, or the taxonomy's ``escalate`` flag decides
    (``TASK_SPECIFICATION`` and ``IRREDUCIBLE_UNCERTAINTY`` are not repairable
    by patching). A healthy run never warrants escalation.
    """

    model_config = ConfigDict(extra="forbid")

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    precision: Optional[float] = None
    recall: Optional[float] = None
    accuracy: Optional[float] = None
    n_runs: int = 0
    denominator_description: str = (
        "precision over escalated runs; recall over runs warranting escalation; "
        "accuracy over all runs"
    )


def escalation_calibration(runs: Sequence[RunOutcome]) -> EscalationCalibrationReport:
    """Precision / recall of the escalate-instead-of-patch decision."""
    tp = fp = fn = tn = 0
    for run in runs:
        warranted = run.ground_truth.should_escalate() if run.ground_truth else False
        if run.escalated and warranted:
            tp += 1
        elif run.escalated and not warranted:
            fp += 1
        elif not run.escalated and warranted:
            fn += 1
        else:
            tn += 1
    escalated = tp + fp
    warranted_total = tp + fn
    total = tp + fp + fn + tn
    return EscalationCalibrationReport(
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=(tp / escalated) if escalated else None,
        recall=(tp / warranted_total) if warranted_total else None,
        accuracy=((tp + tn) / total) if total else None,
        n_runs=total,
    )


# ---------------------------------------------------------------------------
# The whole table
# ---------------------------------------------------------------------------


class RepairMetricsReport(BaseModel):
    """Every repair metric over one set of runs — the paper's table."""

    model_config = ConfigDict(extra="forbid")

    n_runs: int = 0
    n_faulty: int = 0
    n_healthy: int = 0
    detection_recall: MetricValue
    false_repair_rate: MetricValue
    localization_accuracy: MetricValue
    diagnosis_top1_accuracy: MetricValue
    diagnosis_topk_accuracy: MetricValue
    patch_precision: MetricValue
    repair_success_rate: MetricValue
    collateral_regression_rate: MetricValue
    utility_improvement: UtilityImprovementReport
    repair_regret: RepairRegretReport
    time_to_recovery: TimeToRecoveryReport
    escalation: EscalationCalibrationReport

    def rows(self) -> list[dict[str, object]]:
        """Flat rows for the CLI table; undefined metrics stay ``None``."""
        return [
            {
                "metric": m.name,
                "value": m.value,
                "numerator": m.numerator,
                "denominator": m.denominator,
                "over": m.denominator_description,
                "excluded": m.excluded,
            }
            for m in (
                self.detection_recall,
                self.false_repair_rate,
                self.localization_accuracy,
                self.diagnosis_top1_accuracy,
                self.diagnosis_topk_accuracy,
                self.patch_precision,
                self.repair_success_rate,
                self.collateral_regression_rate,
            )
        ]


def repair_metrics_report(runs: Sequence[RunOutcome], *, k: int = 3) -> RepairMetricsReport:
    """Compute every metric in this module over ``runs``."""
    return RepairMetricsReport(
        n_runs=len(runs),
        n_faulty=sum(1 for r in runs if r.faulty),
        n_healthy=sum(1 for r in runs if not r.faulty),
        detection_recall=failure_detection_recall(runs),
        false_repair_rate=false_repair_rate(runs),
        localization_accuracy=localization_accuracy(runs),
        diagnosis_top1_accuracy=diagnosis_top_k_accuracy(runs, 1),
        diagnosis_topk_accuracy=diagnosis_top_k_accuracy(runs, k),
        patch_precision=patch_precision(runs),
        repair_success_rate=repair_success_rate(runs),
        collateral_regression_rate=collateral_regression_rate(runs),
        utility_improvement=utility_improvement(runs),
        repair_regret=repair_regret(runs),
        time_to_recovery=time_to_recovery(runs),
        escalation=escalation_calibration(runs),
    )


__all__ = [
    "EscalationCalibrationReport",
    "GroundTruthFault",
    "MetricValue",
    "ObjectiveDelta",
    "ObjectiveRegret",
    "RepairAttempt",
    "RepairMetricsReport",
    "RepairRegretReport",
    "RunOutcome",
    "TimeToRecoveryReport",
    "UtilityImprovementReport",
    "check_status_map",
    "collateral_regression_rate",
    "diagnosis_top_k_accuracy",
    "escalation_calibration",
    "failure_detection_recall",
    "false_repair_rate",
    "localization_accuracy",
    "patch_precision",
    "repair_metrics_report",
    "repair_regret",
    "repair_success_rate",
    "time_to_recovery",
    "utility_improvement",
]
