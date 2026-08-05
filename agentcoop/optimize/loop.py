"""Execution-coupled objective gating and ECPS orchestration.

The objective boundary in this module deliberately excludes learned preference
signals.  Candidate executions establish feasibility and a masked Pareto front;
the judge protocol may compare only candidates that survive that boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from agentcoop.compile.compiler import CompilationResult
from agentcoop.compile.grammar import RuleContext
from agentcoop.compile.static_analysis import analyze
from agentcoop.diagnose.detectors import detect_all
from agentcoop.execute.engine import ExecutionResult
from agentcoop.execute.trace import EventKind
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import EvaluatorAvailability
from agentcoop.ir.evidence import DecisionKind
from agentcoop.ir.preference import (
    MetaRubric,
    PolicyObservationStatus,
    PolicyPreferenceObservation,
    PreferenceArchive,
    VerbosityMode,
)
from agentcoop.ir.utility import Objective, UtilityVector, dominates
from agentcoop.ir.workflow import CompiledWorkflow, atomics
from agentcoop.optimize.judge import JudgePanel, PanelResult
from agentcoop.optimize.mutations import (
    MutationProposal,
    validate_mutation_proposal,
    workflow_fingerprint,
)
from agentcoop.optimize.packets import CandidateView, build_candidate_view
from agentcoop.optimize.preference import (
    choose_next_comparison,
    fit_preference_models,
    preference_front as infer_preference_front,
    stable_selected_candidate,
)
from agentcoop.optimize.state import (
    CandidateEvaluation,
    CaseExecution,
    CostUnit,
    EvaluationCase,
    MutationRecord,
    ECPS_ALGORITHM_VERSION,
    OptimizationCandidate,
    OptimizationEvent,
    OptimizationLedger,
    OptimizationOutcome,
    OptimizationPolicy,
    OptimizationState,
    OptimizationStopReason,
)


OptimizationHarness = Callable[
    [CompiledWorkflow, EvaluationCase], Awaitable[CaseExecution]
]

_SUBJECTIVE_SOURCES = frozenset({SignalSource.LLM_JUDGE, SignalSource.HUMAN})
_BLOCKING_STATUSES = frozenset(
    {CheckStatus.FAIL, CheckStatus.UNAVAILABLE, CheckStatus.INCONCLUSIVE}
)


def _is_subjective(check: CheckResult) -> bool:
    return (
        check.level is CheckLevel.PREFERENCE
        or check.source in _SUBJECTIVE_SOURCES
    )


def objective_execution_snapshot(execution: ExecutionResult) -> ExecutionResult:
    """Return the execution view authoritative for objective decisions.

    Free-text CHECK events cannot be safely associated with a structured check,
    so every CHECK event is removed.  Other events retain their original steps.
    The raw ``ExecutionResult.ok`` is intentionally ignored because the engine
    may have derived it from a subjective blocking check.
    """

    objective_report = CheckReport(
        results=[
            check.model_copy(deep=True)
            for check in execution.report.results
            if not _is_subjective(check)
        ]
    )
    objective_trace = execution.trace.model_copy(
        deep=True,
        update={
            "checks": [
                check.model_copy(deep=True)
                for check in execution.trace.checks
                if not _is_subjective(check)
            ],
            "events": [
                event.model_copy(deep=True)
                for event in execution.trace.events
                if event.kind is not EventKind.CHECK
            ],
        },
    )
    structured_checks = (
        *objective_report.results,
        *objective_trace.checks,
    )
    blocked = any(
        check.blocking and check.status in _BLOCKING_STATUSES
        for check in structured_checks
    )
    return execution.model_copy(
        deep=True,
        update={
            "ok": execution.state.ok and not blocked,
            "trace": objective_trace,
            "report": objective_report,
        },
    )


def validate_cost_profile(cost: CostProfile) -> tuple[str, ...]:
    """Name every invalid field in authoritative resource accounting."""

    problems: list[str] = []
    for field_name, value in cost.model_dump().items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{field_name} is not numeric")
            continue
        if not math.isfinite(float(value)):
            problems.append(f"{field_name} is not finite")
        elif value < 0:
            problems.append(f"{field_name} is negative")
    if isinstance(cost.tokens, bool) or not isinstance(cost.tokens, int):
        problems.append("tokens is not an integer")
    return tuple(dict.fromkeys(problems))


def _append_once(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def execution_admission_reasons(
    workflow: CompiledWorkflow,
    case: EvaluationCase,
    execution: CaseExecution,
    ctx: RuleContext,
) -> tuple[str, ...]:
    """Explain every objective reason this candidate/case is inadmissible."""

    reasons: list[str] = []
    if ctx.dossier.specification_defects():
        _append_once(reasons, "dossier has specification defects")

    if not workflow.evidence.records:
        _append_once(reasons, "workflow evidence ledger is empty")
    elif not workflow.evidence.fully_justified:
        _append_once(reasons, "workflow evidence ledger is inadmissible")

    binding_targets = {
        record.target
        for record in workflow.evidence.records.values()
        if record.decision_kind is DecisionKind.BIND_COMPONENT and record.admissible
    }
    for atomic in atomics(workflow.term):
        if atomic.term_id not in binding_targets:
            _append_once(
                reasons,
                f"atomic lacks binding evidence: {atomic.term_id}",
            )

    static_report = analyze(workflow, ctx, ledger=workflow.evidence)
    for check in static_report.failures(blocking_only=True):
        _append_once(reasons, f"blocking static check: {check.check_id}")

    if execution.case_fingerprint != case.fingerprint():
        _append_once(reasons, "case fingerprint mismatch")

    snapshot = objective_execution_snapshot(execution.result)
    if not snapshot.ok:
        _append_once(reasons, "objective execution is not ok")
    for output_name in ctx.dossier.required_outputs:
        if output_name not in snapshot.outputs:
            _append_once(reasons, f"missing required output: {output_name}")
    for check in (*snapshot.report.results, *snapshot.trace.checks):
        if check.blocking and check.status in _BLOCKING_STATUSES:
            _append_once(reasons, f"blocking objective check: {check.check_id}")

    for signal in detect_all(
        snapshot.trace,
        snapshot.report,
        workflow,
        ctx.dossier,
    ).blocking():
        _append_once(reasons, f"blocking detector signal: {signal.signal_id}")

    for problem in validate_cost_profile(snapshot.cost):
        _append_once(reasons, f"invalid execution cost: {problem}")

    limits = case.limits
    if limits.max_usd is not None and snapshot.cost.usd > limits.max_usd:
        _append_once(reasons, "case USD limit exceeded")
    if limits.max_tokens is not None and snapshot.cost.tokens > limits.max_tokens:
        _append_once(reasons, "case token limit exceeded")
    if (
        limits.max_wall_time_s is not None
        and snapshot.cost.latency_s > limits.max_wall_time_s
    ):
        _append_once(reasons, "case latency limit exceeded")
    if (
        limits.max_component_calls is not None
        and len(snapshot.trace.node_results) > limits.max_component_calls
    ):
        _append_once(reasons, "case component-call limit exceeded")
    return tuple(reasons)


def _matched_feasible_executions(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[CaseExecution, ...] | None:
    feasible = tuple(
        evaluation for evaluation in evaluations if evaluation.feasible
    )
    if not feasible:
        return None
    matched_sets = tuple(
        tuple(sorted(execution.case_fingerprint for execution in evaluation.executions))
        for evaluation in feasible
    )
    if not matched_sets[0] or any(
        fingerprints != matched_sets[0] for fingerprints in matched_sets[1:]
    ):
        return None
    return tuple(
        execution
        for evaluation in feasible
        for execution in evaluation.executions
    )


def determine_front_cost_unit(
    evaluations: Sequence[CandidateEvaluation],
) -> CostUnit | None:
    """Choose a cost basis only when every feasible execution stamps it."""

    executions = _matched_feasible_executions(evaluations)
    if not executions:
        return None
    units = {execution.cost_unit for execution in executions}
    if len(units) != 1 or None in units:
        return None
    return next(iter(units))


def front_latency_available(
    evaluations: Sequence[CandidateEvaluation],
) -> bool:
    """True only for all-case, front-wide measured latency."""

    executions = _matched_feasible_executions(evaluations)
    return bool(executions) and all(
        execution.latency_measured for execution in executions
    )


def derive_observed_utility(
    candidate: OptimizationCandidate,
    executions: Sequence[CaseExecution],
    *,
    front_cost_unit: CostUnit | None,
    latency_available: bool,
) -> tuple[UtilityVector, dict[str, str]]:
    """Derive the first-release observed vector from objective evidence only."""

    utility = UtilityVector.of()
    basis: dict[str, str] = {}
    snapshots = tuple(
        objective_execution_snapshot(execution.result) for execution in executions
    )
    if snapshots and all(snapshot.ok for snapshot in snapshots):
        utility = utility.with_value(Objective.VALIDITY, 1.0)
        basis["validity"] = "all matched cases passed objective admission"
    else:
        utility = utility.mark_unavailable(Objective.VALIDITY)
        basis["validity"] = "unavailable: no admitted execution"

    coverage = candidate.workflow.evidence.evidence_coverage()
    utility = utility.with_value(Objective.EVIDENCE, coverage)
    basis["evidence"] = f"{coverage:.0%} of design decisions are justified"

    valid_costs = all(not validate_cost_profile(snapshot.cost) for snapshot in snapshots)
    stamped_cost_basis = bool(executions) and all(
        execution.cost_unit is front_cost_unit for execution in executions
    )
    if (
        front_cost_unit is CostUnit.USD
        and valid_costs
        and stamped_cost_basis
    ):
        total = sum(execution.result.cost.usd for execution in executions)
        utility = utility.with_value(Objective.COST, total)
        basis["cost"] = f"${total:.6g} total across matched cases"
    elif (
        front_cost_unit is CostUnit.TOKENS
        and valid_costs
        and stamped_cost_basis
    ):
        total_tokens = sum(
            execution.result.cost.tokens for execution in executions
        )
        utility = utility.with_value(Objective.COST, float(total_tokens))
        basis["cost"] = f"{total_tokens} tokens total across matched cases"
    else:
        utility = utility.mark_unavailable(Objective.COST)
        basis["cost"] = "unavailable: no common measured cost unit"

    if (
        latency_available
        and executions
        and valid_costs
        and all(execution.latency_measured for execution in executions)
    ):
        mean_latency = sum(
            execution.result.cost.latency_s for execution in executions
        ) / len(executions)
        utility = utility.with_value(Objective.LATENCY, mean_latency)
        basis["latency"] = f"{mean_latency:.6g}s mean measured latency"
    else:
        utility = utility.mark_unavailable(Objective.LATENCY)
        basis["latency"] = "unavailable: latency is not measured for every case"

    static_risk = candidate.static_estimate.vector.get(Objective.RISK)
    if static_risk is None:
        utility = utility.mark_unavailable(Objective.RISK)
        basis["risk"] = "unavailable in the compile-time estimate"
    else:
        utility = utility.with_value(Objective.RISK, static_risk)
        basis["risk"] = "compile-time risk retained without judge input"

    utility = utility.mark_unavailable(Objective.ROBUSTNESS)
    utility = utility.mark_unavailable(Objective.SCIENTIFIC_UTILITY)
    return utility, basis


def compatible_observed_front(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[str, ...]:
    """Return the deterministic Pareto front under identical-mask dominance."""

    feasible = sorted(
        (evaluation for evaluation in evaluations if evaluation.feasible),
        key=lambda evaluation: evaluation.candidate.candidate_id,
    )
    front: list[str] = []
    for evaluation in feasible:
        dominated = any(
            other.utility.measured == evaluation.utility.measured
            and dominates(other.utility, evaluation.utility)
            for other in feasible
            if other.candidate.candidate_id != evaluation.candidate.candidate_id
        )
        if not dominated:
            front.append(evaluation.candidate.candidate_id)
    return tuple(front)


class MutationSource(Protocol):
    """Finite deterministic discovery source; the loop revalidates authorization."""

    def propose(
        self,
        parents: Sequence[OptimizationCandidate],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]: ...


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}::{digest[:24]}"


def _dossier_fingerprint(ctx: RuleContext) -> str:
    return _stable_id("dossier", ctx.dossier.model_dump(mode="json"))


class OptimizationLoop:
    """Run evidence-constrained, non-gradient preference search.

    The loop never turns judge output into design evidence or objective utility.
    It first executes a complete matched case set, constructs an objective
    Pareto front, and only then permits pairwise preference comparisons.
    """

    def __init__(
        self,
        ctx: RuleContext,
        *,
        panel: JudgePanel | None = None,
        policy: OptimizationPolicy | None = None,
        mutation_source: MutationSource | None = None,
    ) -> None:
        self.ctx = ctx
        self.panel = panel
        self.policy = policy or OptimizationPolicy()
        self.mutation_source = mutation_source

    async def run(
        self,
        compilation: CompilationResult,
        cases: Sequence[EvaluationCase],
        harness: OptimizationHarness,
    ) -> OptimizationOutcome:
        ordered_cases = tuple(
            sorted(cases, key=lambda case: (case.case_id, case.fingerprint()))
        )
        events: list[OptimizationEvent] = []
        notes: list[str] = []
        evaluations: dict[str, CandidateEvaluation] = {}
        candidates: dict[str, OptimizationCandidate] = {}
        archive = PreferenceArchive()
        packet_archive: dict[str, CandidateView] = {}
        packet_by_target: dict[tuple[str, str], CandidateView] = {}
        model_snapshots = []
        mutation_records: list[MutationRecord] = []
        compiler_rejections = dict(compilation.rejected)
        objective_front_ids: tuple[str, ...] = ()
        preference_front_ids: tuple[str, ...] = ()
        contender_ids: tuple[str, ...] = ()
        effective_policy = self.policy

        candidate_executions = 0
        judge_calls = 0
        execution_cost = CostProfile()
        judge_cost = CostProfile()
        accounting_complete = True
        soft_limits_exceeded: tuple[str, ...] = ()

        def emit(
            state: OptimizationState,
            detail: str,
            *,
            candidate_ids: Sequence[str] = (),
            pair_id: str | None = None,
        ) -> None:
            events.append(
                OptimizationEvent(
                    index=len(events),
                    state=state,
                    detail=detail,
                    candidate_ids=tuple(candidate_ids),
                    pair_id=pair_id,
                )
            )

        def current_ledger() -> OptimizationLedger:
            return OptimizationLedger(
                candidate_executions=candidate_executions,
                judge_calls=judge_calls,
                execution_cost=execution_cost,
                judge_cost=judge_cost,
                cost_accounting_complete=accounting_complete,
                soft_limits_exceeded=soft_limits_exceeded,
            )

        def finish(
            reason: OptimizationStopReason,
            *,
            selected: str | None = None,
            contenders: Sequence[str] | None = None,
            detail: str | None = None,
        ) -> OptimizationOutcome:
            final_contenders = tuple(contenders) if contenders is not None else contender_ids
            if selected is not None and selected not in final_contenders:
                final_contenders = (selected, *final_contenders)
            for candidate_id in sorted(candidates):
                if candidate_id not in evaluations:
                    evaluations[candidate_id] = CandidateEvaluation(
                        candidate=candidates[candidate_id],
                        feasible=False,
                        rejection_reasons=("not executed before terminal stop",),
                    )
            emit(
                OptimizationState.STOPPED,
                detail or reason.value,
                candidate_ids=final_contenders,
            )
            return OptimizationOutcome(
                selected_candidate_id=selected,
                contender_ids=final_contenders,
                dossier_fingerprint=_dossier_fingerprint(self.ctx),
                cases=ordered_cases,
                policy=effective_policy,
                candidates=tuple(evaluations[key] for key in sorted(evaluations)),
                compiler_rejections=dict(sorted(compiler_rejections.items())),
                packet_archive=tuple(
                    packet_archive[key] for key in sorted(packet_archive)
                ),
                archive=archive,
                model_snapshots=tuple(model_snapshots),
                mutation_records=tuple(mutation_records),
                objective_front=objective_front_ids,
                preference_front=preference_front_ids,
                events=tuple(events),
                ledger=current_ledger(),
                state=OptimizationState.STOPPED,
                stop_reason=reason,
                notes=tuple(notes),
            )

        emit(OptimizationState.INITIALIZING, "validating ECPS inputs")
        if not ordered_cases:
            return finish(OptimizationStopReason.NO_CASES)
        if not self.ctx.dossier.preferences:
            return finish(OptimizationStopReason.NO_PREFERENCES)
        if self.ctx.dossier.availability(CheckLevel.PREFERENCE) not in {
            EvaluatorAvailability.RUBRIC,
            EvaluatorAvailability.PREFERENCE_ONLY,
        }:
            return finish(OptimizationStopReason.INELIGIBLE_EVALUATOR)

        preference_ids = tuple(
            preference.preference_id for preference in self.ctx.dossier.preferences
        )
        configured_epsilon = set(effective_policy.epsilon)
        expected_epsilon = set(preference_ids)
        if configured_epsilon and configured_epsilon != expected_epsilon:
            raise ValueError(
                "epsilon keys must exactly match dossier preference IDs"
            )
        if not configured_epsilon:
            effective_policy = effective_policy.model_copy(
                update={
                    "epsilon": {
                        preference_id: 0.0 for preference_id in preference_ids
                    }
                }
            )

        incomplete_compiler_archive = False
        for candidate_id in sorted(compilation.workflows):
            estimate = compilation.estimates.get(candidate_id)
            if estimate is None:
                incomplete_compiler_archive = True
                notes.append(
                    f"compiler archive omitted static estimate for {candidate_id}"
                )
                compiler_rejections[candidate_id] = (
                    "compiler archive missing static estimate"
                )
                continue
            candidates[candidate_id] = OptimizationCandidate(
                candidate_id=candidate_id,
                workflow=compilation.workflows[candidate_id],
                generation=0,
                static_estimate=estimate,
            )
        if incomplete_compiler_archive:
            return finish(
                OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
                detail="compiler archive is incomplete; refusing biased subset selection",
            )
        if not candidates:
            return finish(OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES)

        budget = effective_policy.budget
        initial_execution_reservation = len(candidates) * len(ordered_cases)
        if (
            len(candidates) > budget.max_candidates
            or initial_execution_reservation > budget.max_candidate_executions
        ):
            return finish(
                OptimizationStopReason.BUDGET_EXHAUSTED,
                detail="initial archive cannot receive a complete matched case set",
            )

        seen_fingerprints = {
            workflow_fingerprint(candidate.workflow)
            for candidate in candidates.values()
        }
        case_by_fingerprint = {
            case.fingerprint(): case for case in ordered_cases
        }

        def soft_limit_names() -> tuple[str, ...]:
            total = execution_cost + judge_cost
            exceeded: list[str] = []
            if budget.soft_max_usd is not None and total.usd > budget.soft_max_usd:
                exceeded.append("soft_max_usd")
            if (
                budget.soft_max_tokens is not None
                and total.tokens > budget.soft_max_tokens
            ):
                exceeded.append("soft_max_tokens")
            if (
                budget.soft_max_execution_latency_s is not None
                and execution_cost.latency_s
                > budget.soft_max_execution_latency_s
            ):
                exceeded.append("soft_max_execution_latency_s")
            return tuple(exceeded)

        async def execute_candidate(
            candidate: OptimizationCandidate,
        ) -> tuple[CandidateEvaluation, OptimizationStopReason | None]:
            nonlocal candidate_executions
            nonlocal execution_cost
            nonlocal accounting_complete
            nonlocal soft_limits_exceeded

            recorded: list[CaseExecution] = []
            rejection_reasons: list[str] = []
            terminal: OptimizationStopReason | None = None
            for case in ordered_cases:
                try:
                    execution = await harness(candidate.workflow, case)
                except Exception as exc:  # a failed harness has no admissible run
                    candidate_executions += 1
                    accounting_complete = False
                    rejection_reasons.append(
                        f"harness exception: {type(exc).__name__}"
                    )
                    terminal = OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
                    break
                candidate_executions += 1
                recorded.append(execution)
                cost_problems = validate_cost_profile(execution.result.cost)
                if cost_problems:
                    accounting_complete = False
                    rejection_reasons.extend(
                        f"invalid execution cost: {problem}"
                        for problem in cost_problems
                    )
                    terminal = OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
                    break
                accumulated_execution_cost = execution_cost + execution.result.cost
                aggregate_problems = validate_cost_profile(
                    accumulated_execution_cost
                )
                if not aggregate_problems:
                    aggregate_problems = validate_cost_profile(
                        accumulated_execution_cost + judge_cost
                    )
                if aggregate_problems:
                    accounting_complete = False
                    rejection_reasons.extend(
                        f"invalid cumulative execution cost: {problem}"
                        for problem in aggregate_problems
                    )
                    terminal = OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
                    break
                execution_cost = accumulated_execution_cost
                rejection_reasons.extend(
                    execution_admission_reasons(
                        candidate.workflow,
                        case,
                        execution,
                        self.ctx,
                    )
                )
                crossed = soft_limit_names()
                if crossed:
                    soft_limits_exceeded = tuple(
                        dict.fromkeys((*soft_limits_exceeded, *crossed))
                    )
                    terminal = OptimizationStopReason.BUDGET_EXHAUSTED
                    break

            if len(recorded) != len(ordered_cases):
                rejection_reasons.append("incomplete matched case set")
            unique_reasons = tuple(dict.fromkeys(rejection_reasons))
            feasible = len(recorded) == len(ordered_cases) and not unique_reasons
            return (
                CandidateEvaluation(
                    candidate=candidate,
                    executions=tuple(recorded),
                    feasible=feasible,
                    rejection_reasons=() if feasible else unique_reasons,
                ),
                terminal,
            )

        def rebuild_objective_epoch() -> tuple[str, ...]:
            nonlocal evaluations
            staged = tuple(evaluations[key] for key in sorted(evaluations))
            cost_unit = determine_front_cost_unit(staged)
            latency_available = front_latency_available(staged)
            rebuilt: dict[str, CandidateEvaluation] = {}
            for candidate_id in sorted(evaluations):
                evaluation = evaluations[candidate_id]
                if not evaluation.feasible:
                    rebuilt[candidate_id] = evaluation
                    continue
                utility, basis = derive_observed_utility(
                    evaluation.candidate,
                    evaluation.executions,
                    front_cost_unit=cost_unit,
                    latency_available=latency_available,
                )
                packet_ids: list[str] = []
                for execution in evaluation.executions:
                    case = case_by_fingerprint[execution.case_fingerprint]
                    snapshot = objective_execution_snapshot(execution.result)
                    view = build_candidate_view(
                        self.ctx.dossier,
                        snapshot,
                        case_id=case.case_id,
                        max_payload_chars=effective_policy.max_payload_chars,
                    )
                    packet_archive.setdefault(view.packet_id, view)
                    packet_by_target[(candidate_id, case.case_id)] = view
                    packet_ids.append(view.packet_id)
                rebuilt[candidate_id] = evaluation.model_copy(
                    update={
                        "utility": utility,
                        "utility_basis": basis,
                        "packet_ids": tuple(dict.fromkeys(packet_ids)),
                    }
                )
            evaluations = rebuilt
            return compatible_observed_front(tuple(rebuilt.values()))

        def mutation_step(
            parent_ids: Sequence[str],
        ) -> tuple[str, OptimizationCandidate | None]:
            """Return absent/exhausted/blocked/error/added and an optional child."""

            if self.mutation_source is None:
                return "absent", None
            emit(
                OptimizationState.MUTATING,
                "requesting an authorized unseen configuration child",
                candidate_ids=tuple(parent_ids),
            )
            parents = tuple(candidates[parent_id] for parent_id in parent_ids)
            try:
                proposed = self.mutation_source.propose(
                    parents,
                    seen_fingerprints=set(seen_fingerprints),
                )
            except Exception as exc:
                notes.append(f"mutation source failed: {type(exc).__name__}")
                return "error", None

            if not isinstance(proposed, tuple):
                notes.append("mutation source returned a non-tuple proposal set")
                return "error", None

            unseen: list[tuple[str, MutationProposal]] = []
            invalid_source_output = False
            parent_by_id = {parent.candidate_id: parent for parent in parents}
            for proposal in proposed:
                if not isinstance(proposal, MutationProposal):
                    notes.append("mutation source returned an untyped proposal")
                    invalid_source_output = True
                    continue
                parent = parent_by_id.get(proposal.record.parent_id)
                if (
                    parent is None
                    or proposal.candidate.generation != parent.generation + 1
                ):
                    notes.append("mutation proposal is outside the requested parent frontier")
                    invalid_source_output = True
                    continue
                try:
                    fingerprint = validate_mutation_proposal(
                        proposal,
                        parent=parent,
                        library=self.ctx.library,
                    )
                except (TypeError, ValueError):
                    notes.append(
                        f"mutation {proposal.record.mutation_id} failed authorization"
                    )
                    invalid_source_output = True
                    continue
                if proposal.candidate.candidate_id in candidates:
                    continue
                if fingerprint in seen_fingerprints:
                    continue
                unseen.append((fingerprint, proposal))
            if invalid_source_output:
                return "error", None
            if not unseen:
                return "exhausted", None

            allowed: list[tuple[str, MutationProposal]] = []
            for fingerprint, proposal in unseen:
                if proposal.candidate.generation > budget.max_generations:
                    continue
                if len(candidates) + 1 > budget.max_candidates:
                    continue
                if (
                    candidate_executions + len(ordered_cases)
                    > budget.max_candidate_executions
                ):
                    continue
                allowed.append((fingerprint, proposal))
            if not allowed:
                return "blocked", None

            fingerprint, chosen = min(
                allowed,
                key=lambda item: (
                    item[1].candidate.candidate_id,
                    item[1].record.mutation_id,
                    item[0],
                ),
            )
            child = chosen.candidate
            candidates[child.candidate_id] = child
            seen_fingerprints.add(fingerprint)
            mutation_records.append(chosen.record)
            return "added", child

        def apply_verbosity_policy(
            front: Sequence[str],
        ) -> tuple[tuple[str, ...] | None, OptimizationStopReason | None]:
            nonlocal archive

            verbosity = effective_policy.verbosity
            if verbosity.mode is VerbosityMode.NONE:
                return tuple(front), None
            baseline_id = verbosity.baseline_candidate_id
            if baseline_id is None or baseline_id not in front:
                return None, OptimizationStopReason.VERBOSITY_BASELINE_UNAVAILABLE

            baseline_lengths: dict[str, int] = {}
            for case in ordered_cases:
                view = packet_by_target.get((baseline_id, case.case_id))
                if view is None or view.output_length is None or view.output_length <= 0:
                    return None, OptimizationStopReason.VERBOSITY_BASELINE_UNAVAILABLE
                baseline_lengths[case.case_id] = view.output_length

            existing_targets = {
                (
                    observation.case_id,
                    *sorted(
                        (
                            observation.candidate_a_id,
                            observation.candidate_b_id,
                        )
                    ),
                )
                for observation in archive.policy_observations
                if observation.policy == verbosity
            }
            policy_observations = list(archive.policy_observations)
            excluded = {
                (
                    observation.candidate_b_id
                    if observation.candidate_a_id == baseline_id
                    else observation.candidate_a_id
                )
                for observation in archive.policy_observations
                if observation.policy == verbosity
                and observation.status is PolicyObservationStatus.DIRECTIONAL
            }
            preference_ids = tuple(
                preference.preference_id
                for preference in self.ctx.dossier.preferences
            )
            for other_id in sorted(set(front) - {baseline_id}):
                for case in ordered_cases:
                    candidate_a, candidate_b = sorted((baseline_id, other_id))
                    target = (case.case_id, candidate_a, candidate_b)
                    if target in existing_targets:
                        continue
                    view_a = packet_by_target.get((candidate_a, case.case_id))
                    view_b = packet_by_target.get((candidate_b, case.case_id))
                    length_a = view_a.output_length if view_a is not None else None
                    length_b = view_b.output_length if view_b is not None else None
                    baseline_length = baseline_lengths[case.case_id]
                    other_length = length_b if other_id == candidate_b else length_a
                    threshold = float(verbosity.sigma) * baseline_length
                    over_budget = other_length is not None and other_length > threshold
                    status = (
                        PolicyObservationStatus.DIRECTIONAL
                        if over_budget
                        else PolicyObservationStatus.ABSTAIN
                    )
                    if over_budget:
                        excluded.add(other_id)
                    identity = {
                        "case_id": case.case_id,
                        "candidate_a_id": candidate_a,
                        "candidate_b_id": candidate_b,
                        "policy": verbosity.model_dump(mode="json"),
                    }
                    pair_id = _stable_id(
                        "verbosity-pair",
                        {
                            "candidate_a_id": candidate_a,
                            "candidate_b_id": candidate_b,
                        },
                    )
                    policy_observations.append(
                        PolicyPreferenceObservation(
                            observation_id=_stable_id("policy-observation", identity),
                            pair_id=pair_id,
                            case_id=case.case_id,
                            preference_ids=preference_ids,
                            candidate_a_id=candidate_a,
                            candidate_b_id=candidate_b,
                            status=status,
                            preferred_candidate_id=baseline_id if over_budget else None,
                            policy=verbosity,
                            baseline_candidate_id=baseline_id,
                            candidate_a_length=length_a,
                            candidate_b_length=length_b,
                            baseline_length=baseline_length,
                            threshold=threshold,
                            reason=(
                                "candidate exceeded the deterministic verbosity budget"
                                if over_budget
                                else "candidate stayed within or lacked a measurable length"
                            ),
                        )
                    )
            archive = archive.model_copy(
                update={"policy_observations": tuple(policy_observations)}
            )
            return tuple(candidate for candidate in front if candidate not in excluded), None

        def configured_panel_families() -> tuple[str, ...]:
            if self.panel is None:
                return ()
            return tuple(
                sorted(
                    {
                        judge.family.strip()
                        for judge in self.panel.judges
                        if judge.family.strip()
                    }
                )
            )

        def append_panel_result(result: PanelResult) -> None:
            nonlocal archive
            archive = PreferenceArchive(
                calls=(*archive.calls, *result.calls),
                judgments=(*archive.judgments, *result.judgments),
                observations=(*archive.observations, *result.observations),
                policy_observations=archive.policy_observations,
                attempts=(*archive.attempts, result.attempt),
                notes=(*archive.notes, *result.notes),
            )

        epsilon = dict(effective_policy.epsilon)
        pending = list(sorted(candidates))

        while True:
            emit(
                OptimizationState.EXECUTING,
                "executing complete matched candidate/case sets",
                candidate_ids=tuple(sorted(pending)),
            )
            terminal_reason: OptimizationStopReason | None = None
            for candidate_id in sorted(pending):
                evaluation, terminal_reason = await execute_candidate(
                    candidates[candidate_id]
                )
                evaluations[candidate_id] = evaluation
                if terminal_reason is not None:
                    break
            pending = []
            if terminal_reason is not None:
                return finish(terminal_reason)

            emit(
                OptimizationState.OBJECTIVE_GATING,
                "recomputing objective admission and compatible Pareto front",
                candidate_ids=tuple(sorted(evaluations)),
            )
            objective_front_ids = rebuild_objective_epoch()
            contender_ids = objective_front_ids
            preference_front_ids = objective_front_ids
            if not objective_front_ids:
                return finish(OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES)

            if len(objective_front_ids) == 1:
                incumbent = objective_front_ids[0]
                mutation_status, child = mutation_step((incumbent,))
                if mutation_status == "added" and child is not None:
                    pending = [child.candidate_id]
                    continue
                if mutation_status == "blocked":
                    return finish(
                        OptimizationStopReason.BUDGET_EXHAUSTED,
                        contenders=objective_front_ids,
                    )
                if mutation_status == "error":
                    return finish(
                        OptimizationStopReason.MUTATION_SOURCE_INVALID,
                        contenders=objective_front_ids,
                        detail="mutation neighborhood could not be enumerated",
                    )
                return finish(
                    OptimizationStopReason.OBJECTIVE_SINGLETON,
                    selected=incumbent,
                    contenders=objective_front_ids,
                )

            verbosity_contenders, verbosity_stop = apply_verbosity_policy(
                objective_front_ids
            )
            if verbosity_stop is not None or verbosity_contenders is None:
                return finish(
                    verbosity_stop
                    or OptimizationStopReason.VERBOSITY_BASELINE_UNAVAILABLE,
                    contenders=objective_front_ids,
                )
            contender_ids = verbosity_contenders
            preference_front_ids = verbosity_contenders
            if len(verbosity_contenders) == 1:
                return finish(
                    OptimizationStopReason.VERBOSITY_POLICY_SINGLETON,
                    selected=verbosity_contenders[0],
                    contenders=verbosity_contenders,
                )

            if self.panel is None:
                return finish(
                    OptimizationStopReason.NO_JUDGES,
                    contenders=verbosity_contenders,
                )
            families = configured_panel_families()
            if len(families) < effective_policy.min_judge_families:
                return finish(
                    OptimizationStopReason.INSUFFICIENT_JUDGE_DIVERSITY,
                    contenders=verbosity_contenders,
                )

            meta_rubric = MetaRubric.from_dossier(
                self.ctx.dossier,
                version=ECPS_ALGORITHM_VERSION,
            )
            expanded = False
            while True:
                models = fit_preference_models(
                    verbosity_contenders,
                    preference_ids,
                    archive.observations,
                    ridge=effective_policy.ridge,
                    max_iterations=effective_policy.max_iterations,
                    tolerance=effective_policy.tolerance,
                )
                preference_front_ids = infer_preference_front(
                    models,
                    epsilon=epsilon,
                    delta=effective_policy.delta,
                )
                winner = stable_selected_candidate(
                    verbosity_contenders,
                    preference_ids,
                    archive,
                    epsilon=epsilon,
                    delta=effective_policy.delta,
                    min_judge_families=effective_policy.min_judge_families,
                    ridge=effective_policy.ridge,
                    max_iterations=effective_policy.max_iterations,
                    tolerance=effective_policy.tolerance,
                )
                if winner is not None:
                    preference_front_ids = (winner,)
                    mutation_status, child = mutation_step((winner,))
                    if mutation_status == "added" and child is not None:
                        pending = [child.candidate_id]
                        expanded = True
                        break
                    if mutation_status == "blocked":
                        return finish(
                            OptimizationStopReason.BUDGET_EXHAUSTED,
                            contenders=(winner,),
                        )
                    if mutation_status == "error":
                        return finish(
                            OptimizationStopReason.MUTATION_SOURCE_INVALID,
                            contenders=(winner,),
                            detail="mutation neighborhood could not be enumerated",
                        )
                    return finish(
                        OptimizationStopReason.CONFIDENT_PREFERENCE,
                        selected=winner,
                        contenders=(winner,),
                    )

                target = choose_next_comparison(
                    verbosity_contenders,
                    tuple(case.case_id for case in ordered_cases),
                    archive,
                    models,
                )
                if target is None:
                    evaluator_unstable = (
                        any(
                            "inconsistent evaluator key" in note
                            for note in models.notes
                        )
                        or any(
                            len(model.contributing_families)
                            < effective_policy.min_judge_families
                            for model in models.models
                        )
                    )
                    if evaluator_unstable:
                        return finish(
                            OptimizationStopReason.EVALUATOR_UNSTABLE,
                            contenders=preference_front_ids,
                        )
                    mutation_status, child = mutation_step(preference_front_ids)
                    if mutation_status == "added" and child is not None:
                        pending = [child.candidate_id]
                        expanded = True
                        break
                    if mutation_status == "blocked":
                        return finish(
                            OptimizationStopReason.BUDGET_EXHAUSTED,
                            contenders=preference_front_ids,
                        )
                    if mutation_status == "error":
                        return finish(
                            OptimizationStopReason.MUTATION_SOURCE_INVALID,
                            contenders=preference_front_ids,
                            detail="mutation neighborhood could not be enumerated",
                        )
                    if mutation_status == "exhausted":
                        return finish(
                            OptimizationStopReason.NO_MUTATIONS,
                            contenders=preference_front_ids,
                        )
                    return finish(
                        OptimizationStopReason.FRONT_STABLE_UNRESOLVED,
                        contenders=preference_front_ids,
                    )

                required_calls = 2 * effective_policy.min_judge_families
                remaining_calls = budget.max_judge_calls - judge_calls
                if remaining_calls < required_calls:
                    return finish(
                        OptimizationStopReason.BUDGET_EXHAUSTED,
                        contenders=preference_front_ids,
                    )
                view_a = packet_by_target.get(
                    (target.candidate_a_id, target.case_id)
                )
                view_b = packet_by_target.get(
                    (target.candidate_b_id, target.case_id)
                )
                if view_a is None or view_b is None:
                    notes.append("objective contender lacks an anonymous judge packet")
                    return finish(
                        OptimizationStopReason.EVALUATOR_UNSTABLE,
                        contenders=preference_front_ids,
                    )
                pair_id = _stable_id(
                    "pair",
                    {
                        "candidate_a_id": target.candidate_a_id,
                        "candidate_b_id": target.candidate_b_id,
                    },
                )
                emit(
                    OptimizationState.COMPARING,
                    "running order-swapped diverse judge panel",
                    candidate_ids=(target.candidate_a_id, target.candidate_b_id),
                    pair_id=pair_id,
                )
                case = next(
                    case for case in ordered_cases if case.case_id == target.case_id
                )

                def continue_after_family(
                    panel_cost: CostProfile, cost_complete: bool
                ) -> bool:
                    if not cost_complete or validate_cost_profile(panel_cost):
                        return False
                    projected = execution_cost + judge_cost + panel_cost
                    if validate_cost_profile(projected):
                        return False
                    if (
                        budget.soft_max_usd is not None
                        and projected.usd > budget.soft_max_usd
                    ):
                        return False
                    if (
                        budget.soft_max_tokens is not None
                        and projected.tokens > budget.soft_max_tokens
                    ):
                        return False
                    return True

                try:
                    panel_result = await self.panel.compare(
                        pair_id=pair_id,
                        case_id=target.case_id,
                        candidate_a_id=target.candidate_a_id,
                        candidate_b_id=target.candidate_b_id,
                        view_a=view_a,
                        view_b=view_b,
                        meta_rubric=meta_rubric,
                        seed=case.seed,
                        remaining_calls=remaining_calls,
                        continue_after_family=continue_after_family,
                    )
                except Exception as exc:
                    accounting_complete = False
                    notes.append(f"judge panel failed: {type(exc).__name__}")
                    return finish(
                        OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID,
                        contenders=preference_front_ids,
                    )
                judge_calls += panel_result.judge_calls
                cost_problems = validate_cost_profile(panel_result.cost)
                aggregate_problems: tuple[str, ...] = ()
                if not cost_problems:
                    accumulated_judge_cost = judge_cost + panel_result.cost
                    aggregate_problems = validate_cost_profile(
                        accumulated_judge_cost
                    )
                    if not aggregate_problems:
                        aggregate_problems = validate_cost_profile(
                            execution_cost + accumulated_judge_cost
                        )
                    if not aggregate_problems:
                        judge_cost = accumulated_judge_cost
                try:
                    append_panel_result(panel_result)
                except Exception as exc:
                    notes.append(
                        f"judge panel archive failed: {type(exc).__name__}"
                    )
                    if not panel_result.cost_complete:
                        accounting_complete = False
                        return finish(
                            OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID,
                            contenders=preference_front_ids,
                        )
                    return finish(
                        OptimizationStopReason.EVALUATOR_UNSTABLE,
                        contenders=preference_front_ids,
                    )
                if (
                    not panel_result.cost_complete
                    or cost_problems
                    or aggregate_problems
                ):
                    accounting_complete = False
                    notes.extend(
                        f"invalid judge cost: {problem}" for problem in cost_problems
                    )
                    notes.extend(
                        f"invalid cumulative judge cost: {problem}"
                        for problem in aggregate_problems
                    )
                    return finish(
                        OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID,
                        contenders=preference_front_ids,
                    )
                crossed = soft_limit_names()
                if crossed:
                    soft_limits_exceeded = tuple(
                        dict.fromkeys((*soft_limits_exceeded, *crossed))
                    )
                    return finish(
                        OptimizationStopReason.BUDGET_EXHAUSTED,
                        contenders=preference_front_ids,
                    )
                emit(
                    OptimizationState.MODELING,
                    "refitting criterion-wise preference models",
                    candidate_ids=verbosity_contenders,
                    pair_id=pair_id,
                )
                snapshot = fit_preference_models(
                    verbosity_contenders,
                    preference_ids,
                    archive.observations,
                    ridge=effective_policy.ridge,
                    max_iterations=effective_policy.max_iterations,
                    tolerance=effective_policy.tolerance,
                )
                model_snapshots.append(snapshot)

            if expanded:
                continue


__all__ = [
    "OptimizationHarness",
    "OptimizationLoop",
    "MutationSource",
    "compatible_observed_front",
    "derive_observed_utility",
    "determine_front_cost_unit",
    "execution_admission_reasons",
    "front_latency_available",
    "objective_execution_snapshot",
    "validate_cost_profile",
]
