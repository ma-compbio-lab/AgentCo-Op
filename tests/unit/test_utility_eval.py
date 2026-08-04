"""Tests for the check-report -> utility-vector mapping.

The load-bearing behaviour is what happens when a dimension *cannot* be
measured. Those cases get the most attention here, including the counterfactual
that shows why 0.0 would have been wrong.
"""

from __future__ import annotations

from agentcoop.evaluate.utility_eval import derive_utility, utility_from_report
from agentcoop.ir.capability import (
    CapabilityCard,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    FailureSignature,
)
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    ResourceLimits,
    TaskEvidenceDossier,
)
from agentcoop.ir.evidence import (
    DecisionKind,
    DesignEvidenceRecord,
    EvidenceLedger,
    requirement_evidence,
)
from agentcoop.ir.faults import FaultClass
from agentcoop.ir.utility import Objective, dominates, hypervolume
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, Sequence as SeqTerm, Verify


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _dossier(**evaluators: str) -> TaskEvidenceDossier:
    mapping = {
        CheckLevel(level): EvaluatorAvailability(value) for level, value in evaluators.items()
    }
    return TaskEvidenceDossier(task_id="t1", goal="do the thing", evaluators=mapping)


def _empty_ledger() -> EvidenceLedger:
    return EvidenceLedger()


def _limits(**kwargs: float) -> ResourceLimits:
    return ResourceLimits(**kwargs)


def _derive(
    report: CheckReport,
    *,
    cost: CostProfile | None = None,
    ledger: EvidenceLedger | None = None,
    limits: ResourceLimits | None = None,
    dossier: TaskEvidenceDossier | None = None,
    workflow: CompiledWorkflow | None = None,
    library: ComponentLibrary | None = None,
):
    return derive_utility(
        report,
        cost=cost or CostProfile(),
        ledger=ledger or _empty_ledger(),
        limits=limits or _limits(),
        dossier=dossier or _dossier(),
        workflow=workflow,
        library=library,
    )


def _hard(check_id: str, status: CheckStatus, *, blocking: bool = False) -> CheckResult:
    return CheckResult(
        check_id=check_id, level=CheckLevel.HARD, status=status, blocking=blocking
    )


# ---------------------------------------------------------------------------
# validity
# ---------------------------------------------------------------------------


def test_a_failed_blocking_check_pins_validity_to_zero() -> None:
    report = CheckReport(
        results=[
            _hard("exit_status", CheckStatus.PASS),
            _hard("invariant:mass_balance", CheckStatus.FAIL, blocking=True),
            _hard("schema_valid", CheckStatus.PASS),
        ]
    )
    vector = utility_from_report(
        report,
        cost=CostProfile(),
        ledger=_empty_ledger(),
        limits=_limits(),
        dossier=_dossier(),
    )
    assert vector.get(Objective.VALIDITY) == 0.0
    assert Objective.VALIDITY not in vector.unavailable


def test_an_unevaluable_blocking_check_leaves_validity_unavailable() -> None:
    """An unverified hard invariant is not a satisfied one."""
    report = CheckReport(
        results=[
            _hard("exit_status", CheckStatus.PASS),
            _hard("schema_valid", CheckStatus.PASS),
            _hard("invariant:mass_balance", CheckStatus.UNAVAILABLE, blocking=True),
        ]
    )
    derivation = _derive(report)
    assert derivation.vector.get(Objective.VALIDITY) is None
    assert Objective.VALIDITY in derivation.vector.unavailable
    dim = derivation.for_objective(Objective.VALIDITY)
    assert dim is not None and dim.basis == "blocking_unavailable"
    # the two-thirds pass rate must not leak out as if it certified anything
    assert dim.value is None


def test_validity_is_the_pass_rate_when_nothing_blocking_is_broken() -> None:
    report = CheckReport(
        results=[
            _hard("exit_status", CheckStatus.PASS),
            _hard("no_empty_result", CheckStatus.FAIL),
            _hard("schema_valid", CheckStatus.PASS),
            _hard("required_outputs_present", CheckStatus.UNAVAILABLE),
        ]
    )
    # 2 of the 3 *decided* checks pass; the unavailable one is not counted as
    # either a pass or a failure.
    assert _derive(report).vector.get(Objective.VALIDITY) == 2 / 3


def test_validity_unavailable_when_no_hard_check_decided() -> None:
    report = CheckReport(
        results=[_hard("exit_status", CheckStatus.UNAVAILABLE)]
    )
    vector = _derive(report).vector
    assert Objective.VALIDITY in vector.unavailable
    assert vector.get(Objective.VALIDITY) is None


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


def _ledger_with(admissible: int, inadmissible: int) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for i in range(admissible):
        ledger.add(
            DesignEvidenceRecord(
                decision_id=f"ok{i}",
                decision_kind=DecisionKind.ADD_VERIFIER,
                decision="attach verifier",
                target=f"n{i}",
                evidence=[requirement_evidence("s1", "subgoal requires verification")],
            )
        )
    for i in range(inadmissible):
        ledger.add(
            DesignEvidenceRecord(
                decision_id=f"bad{i}",
                decision_kind=DecisionKind.BIND_COMPONENT,
                decision="bind component",
                target=f"m{i}",
                # requirement only: no capability evidence, so inadmissible
                evidence=[requirement_evidence("s1", "subgoal exists")],
            )
        )
    return ledger


def test_evidence_blends_ledger_coverage_with_provenance_checks() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="provenance_complete",
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.PASS,
            ),
            # a non-provenance artifact check must not move the evidence score
            CheckResult(
                check_id="facet_match",
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.FAIL,
            ),
        ]
    )
    derivation = _derive(report, ledger=_ledger_with(admissible=1, inadmissible=1))
    dim = derivation.for_objective(Objective.EVIDENCE)
    assert dim is not None and dim.basis == "ledger_provenance_blend"
    assert dim.value == 0.5 * 0.5 + 0.5 * 1.0


def test_evidence_is_unavailable_when_neither_signal_exists() -> None:
    derivation = _derive(CheckReport())
    dim = derivation.for_objective(Objective.EVIDENCE)
    assert dim is not None and not dim.available
    assert derivation.vector.get(Objective.EVIDENCE) is None
    assert Objective.EVIDENCE in derivation.vector.unavailable
    assert any("zero decisions" in note for note in dim.notes)


def test_evidence_uses_the_ledger_alone_when_no_provenance_check_ran() -> None:
    derivation = _derive(CheckReport(), ledger=_ledger_with(admissible=3, inadmissible=1))
    dim = derivation.for_objective(Objective.EVIDENCE)
    assert dim is not None and dim.basis == "ledger_only"
    assert dim.value == 0.75


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------


def test_robustness_is_unavailable_when_no_sensitivity_check_was_run() -> None:
    """Never having looked is not the same as having looked and found fragility."""
    report = CheckReport(
        results=[
            CheckResult(
                check_id="required_steps_ran",
                level=CheckLevel.PROCESS,
                status=CheckStatus.PASS,
            )
        ]
    )
    vector = _derive(report).vector
    assert Objective.ROBUSTNESS in vector.unavailable
    assert vector.get(Objective.ROBUSTNESS) is None


def test_robustness_is_zero_when_the_sensitivity_check_failed() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="sensitivity_ran",
                level=CheckLevel.PROCESS,
                status=CheckStatus.FAIL,
            ),
            CheckResult(
                check_id="negative_control_ran",
                level=CheckLevel.PROCESS,
                status=CheckStatus.FAIL,
            ),
        ]
    )
    assert _derive(report).vector.get(Objective.ROBUSTNESS) == 0.0


def test_an_unavailable_sensitivity_evaluator_does_not_pass() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="sensitivity_ran",
                level=CheckLevel.PROCESS,
                status=CheckStatus.UNAVAILABLE,
            ),
            CheckResult(
                check_id="reproducible",
                level=CheckLevel.RESOURCE,
                status=CheckStatus.PASS,
            ),
        ]
    )
    # one decided robustness check (reproducible) passes -> 1.0; the unavailable
    # one neither helps nor hurts, but it is named in the reasoning.
    derivation = _derive(report)
    assert derivation.vector.get(Objective.ROBUSTNESS) == 1.0
    dim = derivation.for_objective(Objective.ROBUSTNESS)
    assert dim is not None and dim.inputs["n_candidates"] == 2.0


def test_a_statistical_process_check_counts_as_robustness_evidence() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="bootstrap_effect_stable",
                level=CheckLevel.PROCESS,
                status=CheckStatus.PASS,
                source=SignalSource.STATISTICAL,
            )
        ]
    )
    assert _derive(report).vector.get(Objective.ROBUSTNESS) == 1.0


# ---------------------------------------------------------------------------
# scientific utility: the dimension the whole design turns on
# ---------------------------------------------------------------------------


def _claim_report(status: CheckStatus) -> CheckReport:
    return CheckReport(
        results=[
            _hard("exit_status", CheckStatus.PASS),
            CheckResult(
                check_id="claim_bundle_wellformed", level=CheckLevel.CLAIM, status=status
            ),
        ]
    )


def test_unavailable_claim_evaluator_is_not_a_score_of_zero() -> None:
    dossier = _dossier(claim="unavailable")
    derivation = _derive(_claim_report(CheckStatus.UNAVAILABLE), dossier=dossier)
    vector = derivation.vector
    assert vector.get(Objective.SCIENTIFIC_UTILITY) is None
    assert Objective.SCIENTIFIC_UTILITY in vector.unavailable
    dim = derivation.for_objective(Objective.SCIENTIFIC_UTILITY)
    assert dim is not None and "no claim-level evaluator" in dim.reason


def test_unevaluable_and_measurably_bad_are_incomparable_not_ranked() -> None:
    """The reason unavailable != 0.0, stated as an executable assertion.

    ``a`` could not be scored on claim quality; ``b`` was scored and failed.
    They must not be ranked against each other on that axis. The final two
    assertions are the counterfactual: had the unmeasured dimension been
    written down as 0.0, dominance would have manufactured a verdict.
    """
    a = _derive(_claim_report(CheckStatus.UNAVAILABLE)).vector
    b = _derive(_claim_report(CheckStatus.FAIL)).vector

    assert a.get(Objective.SCIENTIFIC_UTILITY) is None
    assert b.get(Objective.SCIENTIFIC_UTILITY) == 0.0
    assert Objective.SCIENTIFIC_UTILITY not in a.comparable_with(b)
    assert not dominates(a, b)
    assert not dominates(b, a)

    a_if_zeroed = a.with_value(Objective.SCIENTIFIC_UTILITY, 0.0)
    b_better = b.with_value(Objective.SCIENTIFIC_UTILITY, 0.5)
    assert dominates(b_better, a_if_zeroed)


def test_an_unmeasured_dimension_earns_no_hypervolume_credit() -> None:
    """Unavailability is not a free pass either: it wins no volume."""
    a = _derive(_claim_report(CheckStatus.UNAVAILABLE)).vector
    b = _derive(_claim_report(CheckStatus.FAIL)).vector
    context = [a, b]
    assert hypervolume([a], context=context) < hypervolume([b], context=context)


# ---------------------------------------------------------------------------
# cost and latency
# ---------------------------------------------------------------------------


def test_cost_reports_the_tightest_declared_budget() -> None:
    cost = CostProfile(usd=1.0, tokens=9_000)
    limits = _limits(max_usd=10.0, max_tokens=10_000)
    derivation = _derive(CheckReport(), cost=cost, limits=limits)
    dim = derivation.for_objective(Objective.COST)
    assert dim is not None and dim.basis == "budget_fraction"
    assert dim.value == 0.9  # tokens bind, not dollars


def test_over_budget_is_not_clamped_to_one() -> None:
    derivation = _derive(
        CheckReport(), cost=CostProfile(usd=25.0), limits=_limits(max_usd=10.0)
    )
    assert derivation.vector.get(Objective.COST) == 2.5


def test_cost_falls_back_to_absolute_spend_without_a_budget() -> None:
    derivation = _derive(CheckReport(), cost=CostProfile(usd=3.5, tokens=100))
    dim = derivation.for_objective(Objective.COST)
    assert dim is not None and dim.basis == "absolute_usd"
    assert dim.value == 3.5


def test_token_spend_without_a_price_is_flagged_as_an_incomplete_signal() -> None:
    derivation = _derive(CheckReport(), cost=CostProfile(usd=0.0, tokens=5_000))
    dim = derivation.for_objective(Objective.COST)
    assert dim is not None and any("incomplete" in n for n in dim.notes)


def test_latency_is_normalized_against_the_wall_time_limit() -> None:
    derivation = _derive(
        CheckReport(), cost=CostProfile(latency_s=30.0), limits=_limits(max_wall_time_s=60.0)
    )
    assert derivation.vector.get(Objective.LATENCY) == 0.5


# ---------------------------------------------------------------------------
# risk
# ---------------------------------------------------------------------------


def _card(name: str, *, silent: bool = False) -> CapabilityCard:
    return CapabilityCard(
        name=name,
        kind=ComponentKind.PYTHON_FUNCTION,
        failure_profile=(
            [
                FailureSignature(
                    name="empty_on_garbage",
                    fault_class=FaultClass.ARTIFACT_CONTRACT,
                    silent=True,
                )
            ]
            if silent
            else []
        ),
    )


def _workflow(term) -> CompiledWorkflow:
    return CompiledWorkflow(workflow_id="w1", task_id="t1", term=term)


def test_a_verified_workflow_of_known_components_carries_no_risk() -> None:
    term = Verify(body=Atomic(component="c1", subgoal_id="s1"), verifier="held_out_check")
    library = ComponentLibrary()
    library.add(_card("c1"))
    assert _derive(CheckReport(), workflow=_workflow(term), library=library).vector.get(
        Objective.RISK
    ) == 0.0


def test_an_unverified_component_with_a_silent_failure_mode_is_maximum_risk() -> None:
    library = ComponentLibrary()
    library.add(_card("c1", silent=True))
    term = Atomic(component="c1", subgoal_id="s1")
    assert _derive(CheckReport(), workflow=_workflow(term), library=library).vector.get(
        Objective.RISK
    ) == 1.0


def test_a_component_with_no_capability_card_counts_as_exposed() -> None:
    """An unknown component is not a safe component."""
    term = Verify(body=Atomic(component="mystery", subgoal_id="s1"), verifier="check")
    derivation = _derive(CheckReport(), workflow=_workflow(term), library=ComponentLibrary())
    dim = derivation.for_objective(Objective.RISK)
    assert dim is not None
    # verified downstream (0.0 from that term) but unknown card (0.5)
    assert dim.value == 0.5
    assert any("no capability card" in note for note in dim.notes)


def test_risk_averages_over_component_nodes() -> None:
    library = ComponentLibrary()
    library.add(_card("c1"))
    library.add(_card("c2", silent=True))
    # c1 is followed by the verifier; c2 sits after it and is unverified.
    term = SeqTerm(
        children_terms=[
            Verify(body=Atomic(component="c1", subgoal_id="s1"), verifier="check"),
            Atomic(component="c2", subgoal_id="s2"),
        ]
    )
    value = _derive(CheckReport(), workflow=_workflow(term), library=library).vector.get(
        Objective.RISK
    )
    # c1: verified, not silent -> 0.0 ; c2: unverified + silent -> 1.0
    assert value == 0.5


def test_risk_is_unavailable_without_a_workflow_or_a_risk_check() -> None:
    vector = _derive(CheckReport(results=[_hard("exit_status", CheckStatus.PASS)])).vector
    assert Objective.RISK in vector.unavailable
    assert vector.get(Objective.RISK) is None


def test_risk_falls_back_to_static_analysis_checks() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="missing_verifier",
                level=CheckLevel.PROCESS,
                status=CheckStatus.FAIL,
                subject="node_a",
            ),
            CheckResult(
                check_id="silent_failure_exposure",
                level=CheckLevel.PROCESS,
                status=CheckStatus.PASS,
            ),
        ]
    )
    derivation = _derive(report)
    dim = derivation.for_objective(Objective.RISK)
    assert dim is not None and dim.basis == "check_derived_exposure"
    assert dim.value == 0.5


# ---------------------------------------------------------------------------
# whole-vector properties
# ---------------------------------------------------------------------------


def test_every_objective_is_either_measured_or_explicitly_unavailable() -> None:
    vector = _derive(CheckReport()).vector
    for objective in Objective:
        measured = objective in vector.measured
        unavailable = objective in vector.unavailable
        assert measured != unavailable, objective


def test_derivation_is_deterministic() -> None:
    library = ComponentLibrary()
    library.add(_card("c1", silent=True))
    term = SeqTerm(
        children_terms=[
            Atomic(component="c1", subgoal_id="s1"),
            Verify(body=Atomic(component="c1", subgoal_id="s2"), verifier="check"),
        ]
    )
    report = _claim_report(CheckStatus.PASS)

    def run():
        return _derive(
            report,
            cost=CostProfile(usd=1.0, latency_s=2.0),
            ledger=_ledger_with(admissible=2, inadmissible=1),
            limits=_limits(max_usd=4.0, max_wall_time_s=8.0),
            workflow=_workflow(term),
            library=library,
        )

    assert run().model_dump() == run().model_dump()
