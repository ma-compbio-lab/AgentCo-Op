"""Tests for `agentcoop.memory`.

The interesting tests here are the adversarial ones. Each corresponds to a way
a naive outcome memory manufactures confidence it has not earned:

* the underspecified facet -> an edge that "worked" without ever being checked;
* the silent empty output   -> exit code 0 with a useless artifact;
* the unavailable evaluator -> nothing measured, therefore nothing wrong;
* the symptom-fixing patch  -> the failing check passes, something else broke.

Every one of them must fail to accumulate positive evidence.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentcoop.ir.capability import CostProfile, ReliabilityPosterior
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
)
from agentcoop.ir.evidence import (
    DecisionKind,
    DesignEvidenceRecord,
    EvidenceKind,
    EvidenceStrength,
    outcome_evidence,
    requirement_evidence,
)
from agentcoop.ir.faults import BlameTarget, Diagnosis, FaultClass, FaultHypothesis
from agentcoop.ir.utility import Objective, UtilityVector
from agentcoop.memory import retrieval
from agentcoop.memory.statistics import (
    DEFAULT_LAPLACE_ALPHA,
    StatisticsSnapshot,
    StatisticsStore,
)
from agentcoop.memory.store import (
    ComponentOutcome,
    EdgeOutcome,
    Outcome,
    RepairAttempt,
    RunRecord,
    RunStatus,
    RunStore,
    canonicalize_payload,
)
from agentcoop.ir.artifacts import Compatibility


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def component_run(
    run_id: str,
    component: str,
    outcome: Outcome,
    *,
    task_id: str = "task-1",
    self_reported_ok: bool = True,
    fault_class: FaultClass | None = None,
    fault_class_confirmed: bool = False,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id=task_id,
        workflow_id="wf-1",
        structural_key="atomic(x,s1)",
        components=[
            ComponentOutcome(
                node_id=f"n_{component}",
                component=component,
                outcome=outcome,
                self_reported_ok=self_reported_ok,
                fault_class=fault_class,
                fault_class_confirmed=fault_class_confirmed,
            )
        ],
    )


def edge_run(
    run_id: str,
    producer: str,
    consumer: str,
    artifact_type: str,
    outcome: Outcome,
    *,
    compatibility: Compatibility | None = None,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id="task-1",
        handoffs=[
            EdgeOutcome(
                producer=producer,
                consumer=consumer,
                artifact_type=artifact_type,
                outcome=outcome,
                compatibility=compatibility,
            )
        ],
    )


def repair_run(run_id: str, attempt: RepairAttempt) -> RunRecord:
    return RunRecord(run_id=run_id, task_id="task-1", repairs=[attempt])


def stats_with(*records: RunRecord) -> StatisticsStore:
    return StatisticsStore.from_runs(records)


# ---------------------------------------------------------------------------
# Beta(1,1) prior: no history is never mistaken for reliability
# ---------------------------------------------------------------------------


def test_unknown_component_keeps_uninformative_prior() -> None:
    stats = StatisticsStore()
    posterior = stats.component_reliability("never-seen")
    assert (posterior.alpha, posterior.beta) == (1.0, 1.0)
    assert posterior.n_observations == 0
    assert posterior.mean == 0.5
    # The lower bound is what rankings use, and it must be near zero so an
    # unknown component cannot outrank a demonstrated one.
    assert posterior.lcb < 0.05


def test_unknown_edge_and_repair_keys_also_start_at_beta_1_1() -> None:
    stats = StatisticsStore()
    edge = stats.edge_compatibility("a", "b", "GeneSet")
    repair = stats.repair_success(FaultClass.TOOL_FAILURE, "retry_node")
    assert (edge.alpha, edge.beta) == (1.0, 1.0)
    assert (repair.alpha, repair.beta) == (1.0, 1.0)


def test_successes_and_failures_move_the_posterior() -> None:
    stats = stats_with(
        component_run("r1", "scgpt", Outcome.SUCCESS),
        component_run("r2", "scgpt", Outcome.SUCCESS),
        component_run("r3", "scgpt", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
    )
    posterior = stats.component_reliability("scgpt")
    assert (posterior.alpha, posterior.beta) == (3.0, 2.0)
    assert posterior.n_observations == 3


# ---------------------------------------------------------------------------
# Ranking by lower confidence bound, not by mean
# ---------------------------------------------------------------------------


def test_ranking_prefers_long_record_over_one_lucky_success() -> None:
    records = [component_run("lucky-1", "lucky", Outcome.SUCCESS)]
    for i in range(8):
        records.append(component_run(f"solid-s{i}", "solid", Outcome.SUCCESS))
    for i in range(4):
        records.append(
            component_run(
                f"solid-f{i}", "solid", Outcome.FAILURE, fault_class=FaultClass.CONFIGURATION
            )
        )
    stats = stats_with(*records)

    lucky = stats.component_reliability("lucky")
    solid = stats.component_reliability("solid")

    # The mean says the lucky one-shot component is better...
    assert lucky.mean > solid.mean
    # ...the lower confidence bound, which is what we rank on, does not.
    assert solid.lcb > lucky.lcb
    assert [name for name, _ in stats.rank_components(["lucky", "solid"])] == ["solid", "lucky"]


def test_rank_components_is_deterministic_under_ties() -> None:
    stats = StatisticsStore()
    # All unknown: identical posteriors, so the tie-break must be by name and
    # must not depend on the order the caller passed them in.
    assert [n for n, _ in stats.rank_components(["zeta", "alpha", "mu"])] == [
        "alpha",
        "mu",
        "zeta",
    ]
    assert [n for n, _ in stats.rank_components(["mu", "zeta", "alpha"])] == [
        "alpha",
        "mu",
        "zeta",
    ]


# ---------------------------------------------------------------------------
# Adversarial case: the unavailable evaluator
# ---------------------------------------------------------------------------


def test_undetermined_observations_do_not_update_the_posterior() -> None:
    stats = stats_with(
        *[component_run(f"r{i}", "unmeasurable", Outcome.UNDETERMINED) for i in range(10)]
    )
    posterior = stats.component_reliability("unmeasurable")
    assert (posterior.alpha, posterior.beta) == (1.0, 1.0)
    counts = stats.component_counts("unmeasurable")
    assert (counts.successes, counts.failures, counts.undetermined) == (0, 0, 10)
    assert counts.n == 0 and counts.n_total == 10


def test_unevaluable_component_does_not_outrank_an_unknown_one() -> None:
    stats = stats_with(
        *[component_run(f"r{i}", "unmeasurable", Outcome.UNDETERMINED) for i in range(10)]
    )
    a = stats.component_reliability("unmeasurable")
    b = stats.component_reliability("brand-new")
    assert a.lcb == b.lcb


def test_component_outcome_from_checks_treats_unavailable_as_undetermined() -> None:
    results = [
        CheckResult(
            check_id="claim_traceable",
            level=CheckLevel.CLAIM,
            status=CheckStatus.UNAVAILABLE,
        ),
        CheckResult(
            check_id="expert_pairwise",
            level=CheckLevel.PREFERENCE,
            status=CheckStatus.UNAVAILABLE,
        ),
    ]
    obs = ComponentOutcome.from_checks(
        node_id="n1", component="geneagent", results=results, self_reported_ok=True
    )
    assert obs.outcome is Outcome.UNDETERMINED
    assert obs.checks_consulted == ["claim_traceable", "expert_pairwise"]


def test_component_outcome_from_checks_succeeds_only_on_a_real_verdict() -> None:
    passing = CheckResult(
        check_id="exit_status", level=CheckLevel.HARD, status=CheckStatus.PASS
    )
    unavailable = CheckResult(
        check_id="claim_traceable", level=CheckLevel.CLAIM, status=CheckStatus.UNAVAILABLE
    )
    obs = ComponentOutcome.from_checks(
        node_id="n1", component="c", results=[passing, unavailable]
    )
    assert obs.outcome is Outcome.SUCCESS


def test_run_hard_validity_is_undetermined_when_no_hard_check_decided() -> None:
    report = CheckReport(
        results=[
            CheckResult(
                check_id="schema_valid", level=CheckLevel.HARD, status=CheckStatus.UNAVAILABLE
            )
        ]
    )
    record = RunRecord(
        run_id="r1", task_id="t", status=RunStatus.COMPLETED, checks=report
    )
    # Ran to completion, no blocking failure recorded -- and still not a success.
    assert record.checks.hard_constraints_satisfied is True
    assert record.hard_validity is Outcome.UNDETERMINED


def test_run_hard_validity_reports_failure_and_success_when_decided() -> None:
    failing = RunRecord(
        run_id="r1",
        task_id="t",
        checks=CheckReport(
            results=[
                CheckResult(
                    check_id="exit_status", level=CheckLevel.HARD, status=CheckStatus.FAIL
                )
            ]
        ),
    )
    passing = RunRecord(
        run_id="r2",
        task_id="t",
        checks=CheckReport(
            results=[
                CheckResult(
                    check_id="exit_status", level=CheckLevel.HARD, status=CheckStatus.PASS
                )
            ]
        ),
    )
    assert failing.hard_validity is Outcome.FAILURE
    assert passing.hard_validity is Outcome.SUCCESS


# ---------------------------------------------------------------------------
# Adversarial case: the silent empty output
# ---------------------------------------------------------------------------


def test_silent_failure_counts_against_the_component() -> None:
    stats = stats_with(
        *[
            component_run(
                f"r{i}",
                "silent-tool",
                Outcome.FAILURE,
                self_reported_ok=True,  # exit code 0
                fault_class=FaultClass.ARTIFACT_CONTRACT,
            )
            for i in range(5)
        ]
    )
    posterior = stats.component_reliability("silent-tool")
    assert (posterior.alpha, posterior.beta) == (1.0, 6.0)
    assert posterior.mean < 0.2


def test_silent_property_distinguishes_quiet_failure_from_a_crash() -> None:
    quiet = ComponentOutcome(
        node_id="n1", component="c", outcome=Outcome.FAILURE, self_reported_ok=True
    )
    crash = ComponentOutcome(
        node_id="n1", component="c", outcome=Outcome.FAILURE, self_reported_ok=False
    )
    healthy = ComponentOutcome(node_id="n1", component="c", outcome=Outcome.SUCCESS)
    assert quiet.silent is True
    assert crash.silent is False
    assert healthy.silent is False


def test_from_checks_marks_a_zero_exit_with_a_failing_check_as_silent() -> None:
    obs = ComponentOutcome.from_checks(
        node_id="n1",
        component="c",
        self_reported_ok=True,
        results=[
            CheckResult(
                check_id="no_empty_result", level=CheckLevel.HARD, status=CheckStatus.FAIL
            )
        ],
    )
    assert obs.outcome is Outcome.FAILURE
    assert obs.silent is True


def test_adapter_error_is_a_failure_even_with_no_checks() -> None:
    obs = ComponentOutcome.from_checks(
        node_id="n1", component="c", results=[], self_reported_ok=False
    )
    assert obs.outcome is Outcome.FAILURE
    assert obs.silent is False


def test_fault_class_cannot_be_attached_to_a_non_failure() -> None:
    with pytest.raises(ValidationError):
        ComponentOutcome(
            node_id="n1",
            component="c",
            outcome=Outcome.SUCCESS,
            fault_class=FaultClass.TOOL_FAILURE,
        )
    with pytest.raises(ValidationError):
        ComponentOutcome(
            node_id="n1",
            component="c",
            outcome=Outcome.UNDETERMINED,
            fault_class=FaultClass.TOOL_FAILURE,
        )


# ---------------------------------------------------------------------------
# Adversarial case: the underspecified facet
# ---------------------------------------------------------------------------


def test_underspecified_edge_cannot_be_recorded_as_a_success() -> None:
    with pytest.raises(ValidationError):
        EdgeOutcome(
            producer="seurat",
            consumer="geneagent",
            artifact_type="GeneSet",
            outcome=Outcome.SUCCESS,
            compatibility=Compatibility.UNDERSPECIFIED,
        )
    with pytest.raises(ValidationError):
        EdgeOutcome(
            producer="seurat",
            consumer="geneagent",
            artifact_type="GeneSet",
            outcome=Outcome.SUCCESS,
            compatibility=Compatibility.INCOMPATIBLE,
        )


def test_underspecified_edge_accumulates_no_support_for_the_edge() -> None:
    stats = stats_with(
        *[
            edge_run(
                f"r{i}",
                "seurat",
                "geneagent",
                "GeneSet",
                Outcome.UNDETERMINED,
                compatibility=Compatibility.UNDERSPECIFIED,
            )
            for i in range(6)
        ]
    )
    posterior = stats.edge_compatibility("seurat", "geneagent", "GeneSet")
    assert (posterior.alpha, posterior.beta) == (1.0, 1.0)
    item = retrieval.edge_compatibility_evidence(stats, "seurat", "geneagent", "GeneSet")
    assert item.strength is EvidenceStrength.ASSERTED
    assert item.load_bearing is False
    assert item.data["undetermined"] == 6


def test_adapter_resolved_edge_may_be_recorded_as_a_success() -> None:
    obs = EdgeOutcome(
        producer="seurat",
        consumer="geneagent",
        artifact_type="GeneSet",
        outcome=Outcome.SUCCESS,
        compatibility=Compatibility.NEEDS_ADAPTER,
    )
    assert obs.outcome is Outcome.SUCCESS


def test_edge_keys_separate_direction_and_artifact_type() -> None:
    stats = stats_with(
        edge_run("r1", "a", "b", "GeneSet", Outcome.SUCCESS),
        edge_run("r2", "a", "b", "GeneSet", Outcome.SUCCESS),
        edge_run("r3", "a", "b", "CountMatrix", Outcome.FAILURE),
        edge_run("r4", "b", "a", "GeneSet", Outcome.FAILURE),
    )
    assert stats.edge_compatibility("a", "b", "GeneSet").alpha == 3.0
    assert stats.edge_compatibility("a", "b", "CountMatrix").beta == 2.0
    assert stats.edge_compatibility("b", "a", "GeneSet").beta == 2.0
    # An unrelated triple stays at the prior rather than inheriting a neighbour's.
    assert stats.edge_compatibility("a", "c", "GeneSet").alpha == 1.0


# ---------------------------------------------------------------------------
# Adversarial case: the patch that fixes the symptom and breaks something else
# ---------------------------------------------------------------------------


def symptom_fixing_patch(idx: int) -> RepairAttempt:
    return RepairAttempt(
        transaction_id=f"tx{idx}",
        fault_class=FaultClass.ARTIFACT_CONTRACT,
        patch_family="insert_adapter",
        committed=True,
        fixed_original_failure=True,
        collateral_regressions=["provenance_complete"],
        verified=True,
    )


def test_patch_with_collateral_regression_is_not_a_repair_success() -> None:
    attempt = symptom_fixing_patch(0)
    assert attempt.outcome is Outcome.FAILURE


def test_repair_posterior_falls_for_a_family_that_keeps_regressing_things() -> None:
    stats = stats_with(*[repair_run(f"r{i}", symptom_fixing_patch(i)) for i in range(4)])
    posterior = stats.repair_success(FaultClass.ARTIFACT_CONTRACT, "insert_adapter")
    assert (posterior.alpha, posterior.beta) == (1.0, 5.0)
    item = retrieval.repair_success_evidence(
        stats, FaultClass.ARTIFACT_CONTRACT, "insert_adapter"
    )
    assert item.strength is EvidenceStrength.STATISTICAL
    assert item.data["successes"] == 0 and item.data["failures"] == 4


def test_clean_repair_is_a_success() -> None:
    attempt = RepairAttempt(
        transaction_id="tx1",
        fault_class=FaultClass.ARTIFACT_CONTRACT,
        patch_family="insert_adapter",
        committed=True,
        fixed_original_failure=True,
        verified=True,
    )
    assert attempt.outcome is Outcome.SUCCESS


def test_rejected_and_unverified_patches_are_distinguished() -> None:
    rejected = RepairAttempt(
        transaction_id="tx1",
        fault_class=FaultClass.TOOL_FAILURE,
        patch_family="retry_node",
        committed=False,
        verified=True,
    )
    unverified = RepairAttempt(
        transaction_id="tx2",
        fault_class=FaultClass.TOOL_FAILURE,
        patch_family="retry_node",
        committed=True,
        verified=False,
    )
    assert rejected.outcome is Outcome.FAILURE
    assert unverified.outcome is Outcome.UNDETERMINED

    stats = stats_with(repair_run("r1", rejected), repair_run("r2", unverified))
    counts = stats.repair_counts(FaultClass.TOOL_FAILURE, "retry_node")
    assert (counts.successes, counts.failures, counts.undetermined) == (0, 1, 1)


def test_unverified_patch_cannot_claim_to_have_fixed_anything() -> None:
    with pytest.raises(ValidationError):
        RepairAttempt(
            transaction_id="tx1",
            fault_class=FaultClass.TOOL_FAILURE,
            patch_family="retry_node",
            committed=True,
            fixed_original_failure=True,
            verified=False,
        )


def test_inadmissible_patch_family_cannot_be_recorded() -> None:
    # "test failed -> raise the temperature" must not even be storable as a
    # response to a namespace mismatch.
    with pytest.raises(ValidationError):
        RepairAttempt(
            transaction_id="tx1",
            fault_class=FaultClass.ARTIFACT_CONTRACT,
            patch_family="retune_config",
            committed=True,
            verified=True,
        )


def test_repair_keys_separate_fault_classes() -> None:
    stats = stats_with(
        repair_run(
            "r1",
            RepairAttempt(
                transaction_id="tx1",
                fault_class=FaultClass.ARTIFACT_CONTRACT,
                patch_family="insert_adapter",
                committed=True,
                fixed_original_failure=True,
            ),
        ),
        repair_run(
            "r2",
            RepairAttempt(
                transaction_id="tx2",
                fault_class=FaultClass.TOOL_FAILURE,
                patch_family="retry_node",
                committed=False,
            ),
        ),
    )
    assert stats.repair_success(FaultClass.ARTIFACT_CONTRACT, "insert_adapter").alpha == 2.0
    assert stats.repair_success(FaultClass.TOOL_FAILURE, "retry_node").beta == 2.0
    # No leakage across fault classes for the same family.
    assert stats.repair_success(FaultClass.TOOL_FAILURE, "insert_adapter").alpha == 1.0


# ---------------------------------------------------------------------------
# failure_distribution
# ---------------------------------------------------------------------------


def test_failure_distribution_is_uniform_without_history() -> None:
    stats = StatisticsStore()
    dist = stats.failure_distribution("unknown")
    assert set(dist) == set(FaultClass)
    assert pytest.approx(sum(dist.values()), abs=1e-12) == 1.0
    assert len(set(round(p, 12) for p in dist.values())) == 1


def test_failure_distribution_is_laplace_smoothed_and_normalized() -> None:
    stats = stats_with(
        component_run("r1", "c", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT),
        component_run("r2", "c", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT),
        component_run("r3", "c", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
    )
    dist = stats.failure_distribution("c")
    k = len(FaultClass)
    denominator = 3 + DEFAULT_LAPLACE_ALPHA * k
    assert dist[FaultClass.ENVIRONMENT] == pytest.approx((2 + 1) / denominator)
    assert dist[FaultClass.TOOL_FAILURE] == pytest.approx((1 + 1) / denominator)
    # A never-observed class keeps positive mass, so diagnosis can still reach it.
    assert dist[FaultClass.COORDINATION] == pytest.approx(1 / denominator)
    assert dist[FaultClass.COORDINATION] > 0.0
    assert pytest.approx(sum(dist.values()), abs=1e-12) == 1.0


def test_failure_distribution_ordering_is_stable() -> None:
    stats = stats_with(
        component_run("r1", "c", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT)
    )
    assert list(stats.failure_distribution("c")) == list(FaultClass)


def test_undiagnosed_failures_do_not_enter_the_distribution() -> None:
    stats = stats_with(
        component_run("r1", "c", Outcome.FAILURE),  # failed, never diagnosed
        component_run("r2", "c", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT),
    )
    assert stats.failure_observations("c") == 1
    assert stats.component_reliability("c").beta == 3.0  # both count against reliability
    dist = stats.failure_distribution("c")
    denominator = 1 + DEFAULT_LAPLACE_ALPHA * len(FaultClass)
    assert dist[FaultClass.ENVIRONMENT] == pytest.approx(2 / denominator)


def test_confirmed_only_distribution_excludes_unconfirmed_diagnoses() -> None:
    stats = stats_with(
        component_run(
            "r1",
            "c",
            Outcome.FAILURE,
            fault_class=FaultClass.CONFIGURATION,
            fault_class_confirmed=False,
        ),
        component_run(
            "r2",
            "c",
            Outcome.FAILURE,
            fault_class=FaultClass.ENVIRONMENT,
            fault_class_confirmed=True,
        ),
    )
    assert stats.failure_observations("c") == 2
    assert stats.failure_observations("c", confirmed_only=True) == 1
    confirmed = stats.failure_distribution("c", confirmed_only=True)
    assert confirmed[FaultClass.ENVIRONMENT] > confirmed[FaultClass.CONFIGURATION]
    all_seen = stats.failure_distribution("c")
    assert all_seen[FaultClass.CONFIGURATION] == pytest.approx(
        all_seen[FaultClass.ENVIRONMENT]
    )


def test_zero_smoothing_is_rejected() -> None:
    # alpha=0 would make an unobserved fault class unreachable forever.
    with pytest.raises(ValueError):
        StatisticsStore().failure_distribution("c", alpha=0.0)


# ---------------------------------------------------------------------------
# retrieval: the n < 3 boundary
# ---------------------------------------------------------------------------


def test_outcome_evidence_threshold_matches_the_ir() -> None:
    # Guards against MIN_STATISTICAL_OBSERVATIONS drifting away from the IR.
    n = retrieval.MIN_STATISTICAL_OBSERVATIONS
    below = outcome_evidence("c", "src", n - 1)
    at = outcome_evidence("c", "src", n)
    assert below.strength is EvidenceStrength.ASSERTED
    assert at.strength is EvidenceStrength.STATISTICAL


@pytest.mark.parametrize(
    ("n_runs", "expected_strength", "expected_load_bearing"),
    [
        (0, EvidenceStrength.ASSERTED, False),
        (1, EvidenceStrength.ASSERTED, False),
        (2, EvidenceStrength.ASSERTED, False),
        (3, EvidenceStrength.STATISTICAL, True),
        (4, EvidenceStrength.STATISTICAL, True),
    ],
)
def test_component_evidence_strength_boundary(
    n_runs: int, expected_strength: EvidenceStrength, expected_load_bearing: bool
) -> None:
    stats = stats_with(
        *[component_run(f"r{i}", "c", Outcome.SUCCESS) for i in range(n_runs)]
    )
    item = retrieval.component_reliability_evidence(stats, "c")
    assert item.kind is EvidenceKind.OUTCOME
    assert item.strength is expected_strength
    assert item.load_bearing is expected_load_bearing
    assert item.data["n_observations"] == n_runs
    assert item.source_ref == "stats:component:c"


def test_undetermined_runs_never_push_evidence_over_the_threshold() -> None:
    stats = stats_with(
        component_run("r1", "c", Outcome.SUCCESS),
        component_run("r2", "c", Outcome.SUCCESS),
        component_run("r3", "c", Outcome.UNDETERMINED),
        component_run("r4", "c", Outcome.UNDETERMINED),
        component_run("r5", "c", Outcome.UNDETERMINED),
    )
    item = retrieval.component_reliability_evidence(stats, "c")
    assert item.data["n_observations"] == 2
    assert item.data["undetermined"] == 3
    assert item.strength is EvidenceStrength.ASSERTED
    assert "could not be evaluated" in item.claim


def test_asserted_outcome_evidence_cannot_justify_a_fallback_decision() -> None:
    """The end-to-end point of the n<3 rule, checked against the IR's own
    admissibility policy rather than restated."""
    two = stats_with(
        component_run("r1", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
        component_run("r2", "flaky", Outcome.SUCCESS),
    )
    three = stats_with(
        component_run("r1", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
        component_run("r2", "flaky", Outcome.SUCCESS),
        component_run("r3", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
    )

    def record_for(stats: StatisticsStore) -> DesignEvidenceRecord:
        return DesignEvidenceRecord(
            decision_id="d1",
            decision_kind=DecisionKind.ADD_FALLBACK,
            decision="add a fallback arm for 'flaky'",
            target="t1",
            evidence=[retrieval.component_reliability_evidence(stats, "flaky")],
        )

    assert record_for(two).admissible is False
    assert EvidenceKind.OUTCOME in record_for(two).missing_evidence_kinds()
    assert record_for(three).admissible is True


def test_failure_profile_evidence_needs_diagnosed_failures_to_be_load_bearing() -> None:
    undiagnosed = stats_with(
        *[component_run(f"r{i}", "c", Outcome.FAILURE) for i in range(5)]
    )
    item = retrieval.failure_profile_evidence(undiagnosed, "c")
    assert item.data["n_observations"] == 0
    assert item.load_bearing is False
    assert "no diagnosed failures" in item.claim

    diagnosed = stats_with(
        *[
            component_run(f"r{i}", "c", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT)
            for i in range(3)
        ]
    )
    item = retrieval.failure_profile_evidence(diagnosed, "c")
    assert item.load_bearing is True
    assert item.data["distribution"][FaultClass.ENVIRONMENT.value] > 0.3


def test_fallback_is_not_warranted_on_two_observed_failures() -> None:
    stats = stats_with(
        component_run("r1", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
        component_run("r2", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE),
    )
    warranted, item = retrieval.fallback_warranted(stats, "flaky")
    assert warranted is False
    assert item.load_bearing is False
    assert "insufficient history" in item.claim


def test_fallback_is_warranted_once_the_failure_rate_is_credible() -> None:
    records = [
        component_run(f"f{i}", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE)
        for i in range(6)
    ]
    records += [component_run(f"s{i}", "flaky", Outcome.SUCCESS) for i in range(6)]
    stats = stats_with(*records)
    warranted, item = retrieval.fallback_warranted(stats, "flaky")
    assert warranted is True
    assert item.load_bearing is True
    assert item.data["failure_rate_lcb"] >= 0.1


def test_fallback_declined_for_a_component_that_never_fails() -> None:
    stats = stats_with(
        *[component_run(f"r{i}", "solid", Outcome.SUCCESS) for i in range(20)]
    )
    warranted, item = retrieval.fallback_warranted(stats, "solid")
    assert warranted is False
    # The system must be able to say *why* it declined the extra structure.
    assert "no fallback warranted" in item.claim
    assert item.load_bearing is True


def test_most_reliable_component_refuses_to_pick_without_history() -> None:
    stats = StatisticsStore()
    best, items = retrieval.most_reliable_component(stats, ["a", "b"])
    assert best is None
    assert all(not item.load_bearing for item in items)


def test_most_reliable_component_picks_the_best_lower_bound() -> None:
    records = [component_run("lucky-1", "lucky", Outcome.SUCCESS)]
    records += [component_run(f"s{i}", "solid", Outcome.SUCCESS) for i in range(8)]
    records += [
        component_run(f"f{i}", "solid", Outcome.FAILURE, fault_class=FaultClass.CONFIGURATION)
        for i in range(4)
    ]
    stats = stats_with(*records)
    best, _ = retrieval.most_reliable_component(stats, ["lucky", "solid"])
    assert best == "solid"


def test_bulk_retrieval_returns_outcome_items_for_every_key() -> None:
    stats = stats_with(
        *[
            component_run(f"r{i}", "seurat", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT)
            for i in range(3)
        ],
        *[
            edge_run(f"e{i}", "seurat", "geneagent", "GeneSet", Outcome.SUCCESS)
            for i in range(3)
        ],
    )
    component_items = retrieval.retrieve_component_evidence(stats, "seurat")
    assert [i.source_ref for i in component_items] == [
        "stats:component:seurat",
        "stats:failure_distribution:seurat",
    ]
    assert all(i.kind is EvidenceKind.OUTCOME for i in component_items)
    assert all(i.load_bearing for i in component_items)

    edge_items = retrieval.retrieve_edge_evidence(
        stats, "seurat", "geneagent", ["GeneSet", "CountMatrix"]
    )
    assert [i.data["n_observations"] for i in edge_items] == [3, 0]
    assert [i.load_bearing for i in edge_items] == [True, False]


def test_evidence_source_refs_are_resolvable_keys() -> None:
    stats = StatisticsStore()
    assert (
        retrieval.edge_compatibility_evidence(stats, "a", "b", "GeneSet").source_ref
        == "stats:edge:a->b:GeneSet"
    )
    assert (
        retrieval.repair_success_evidence(
            stats, FaultClass.TOOL_FAILURE, "retry_node"
        ).source_ref
        == "stats:repair:tool_failure:retry_node"
    )
    assert (
        retrieval.failure_profile_evidence(stats, "c").source_ref
        == "stats:failure_distribution:c"
    )


# ---------------------------------------------------------------------------
# RunStore: append-only JSONL, lossless round trip
# ---------------------------------------------------------------------------


def rich_record(run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id="heart-merfish",
        workflow_id="wf-7",
        structural_key="seq(atomic(seurat,s1),atomic(geneagent,s2))",
        utility=UtilityVector.of(validity=1.0, evidence=0.75, cost=3.5),
        checks=CheckReport(
            results=[
                CheckResult(
                    check_id="exit_status",
                    level=CheckLevel.HARD,
                    status=CheckStatus.PASS,
                    blocking=True,
                    subject="n1",
                    subject_kind="node",
                ),
                CheckResult(
                    check_id="expert_pairwise",
                    level=CheckLevel.PREFERENCE,
                    status=CheckStatus.UNAVAILABLE,
                    summary="no recorded expert judgments",
                ),
            ]
        ),
        diagnoses=[
            Diagnosis(
                hypotheses=[
                    FaultHypothesis(
                        fault_class=FaultClass.ARTIFACT_CONTRACT,
                        probability=0.7,
                        blame_target=BlameTarget.EDGE,
                        subject="n1->n2",
                        rationale="namespace facet conflict at the handoff",
                        supporting_signals=["sig-1"],
                    )
                ],
                localized_to="art-3",
                localized_kind=BlameTarget.ARTIFACT,
                trigger_signals=["sig-1"],
                notes=["backward slice terminated at art-3"],
            )
        ],
        repairs=[
            RepairAttempt(
                transaction_id="tx1",
                fault_class=FaultClass.ARTIFACT_CONTRACT,
                patch_family="insert_adapter",
                committed=True,
                fixed_original_failure=True,
                verified=True,
                detail="ensembl_to_hgnc converter inserted",
            )
        ],
        cost=CostProfile(latency_s=12.5, tokens=4096, usd=0.31),
        status=RunStatus.COMPLETED,
        components=[
            ComponentOutcome(
                node_id="n1",
                component="seurat",
                subgoal_id="s1",
                outcome=Outcome.SUCCESS,
                checks_consulted=["exit_status"],
            ),
            ComponentOutcome(
                node_id="n2",
                component="geneagent",
                subgoal_id="s2",
                outcome=Outcome.FAILURE,
                self_reported_ok=True,
                fault_class=FaultClass.ARTIFACT_CONTRACT,
                fault_class_confirmed=True,
                detail="empty gene set on malformed namespace",
            ),
        ],
        handoffs=[
            EdgeOutcome(
                producer="seurat",
                consumer="geneagent",
                artifact_type="GeneSet",
                outcome=Outcome.FAILURE,
                compatibility=Compatibility.INCOMPATIBLE,
                producer_node="n1",
                consumer_node="n2",
                artifact_id="art-3",
            )
        ],
        notes=["case study 1"],
    )


def test_run_store_round_trip_is_lossless(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    store = RunStore(path)
    original = rich_record()
    store.append(original)

    reloaded = RunStore.load(path)
    assert len(reloaded) == 1
    assert reloaded.all()[0] == original
    # Deep equality reaches the nested models too.
    assert reloaded.all()[0].diagnoses[0].hypotheses[0].probability == 0.7
    assert reloaded.all()[0].utility.unavailable == original.utility.unavailable
    assert reloaded.all()[0].components[1].fault_class is FaultClass.ARTIFACT_CONTRACT


def test_canonicalize_payload_sorts_set_valued_fields() -> None:
    # Deterministic version of the hash-seed hazard: a dumped set arrives in
    # arbitrary order and must leave sorted.
    payload = canonicalize_payload(
        {"utility": {"values": {}, "unavailable": ["risk", "latency", "cost"]}}
    )
    assert payload["utility"]["unavailable"] == ["cost", "latency", "risk"]
    # Records that never measured a utility dimension must survive untouched.
    assert canonicalize_payload({"utility": None}) == {"utility": None}
    assert canonicalize_payload({}) == {}


def test_run_record_serialization_is_canonical_and_stable() -> None:
    record = rich_record()
    first = record.to_json_line()
    second = RunRecord.from_json_line(first).to_json_line()
    assert first == second
    payload = json.loads(first)
    # Sets must be emitted in a stable order or two identical histories would
    # produce different files depending on the interpreter's hash seed.
    unavailable = payload["utility"]["unavailable"]
    assert unavailable == sorted(unavailable)
    assert unavailable == [
        Objective.LATENCY.value,
        Objective.RISK.value,
        Objective.ROBUSTNESS.value,
        Objective.SCIENTIFIC_UTILITY.value,
    ]
    assert sorted(payload) == list(payload)


def test_run_store_is_append_only(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    store = RunStore(path)
    store.append(rich_record("run-1"))
    with pytest.raises(ValueError):
        store.append(rich_record("run-1"))
    # The rejected write left no trace in the file.
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_run_store_preserves_order_and_filters_by_task(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    store = RunStore(path)
    store.append(component_run("r1", "a", Outcome.SUCCESS, task_id="t1"))
    store.append(component_run("r2", "b", Outcome.SUCCESS, task_id="t2"))
    store.append(component_run("r3", "c", Outcome.SUCCESS, task_id="t1"))

    assert [r.run_id for r in store.all()] == ["r1", "r2", "r3"]
    assert [r.run_id for r in store.by_task("t1")] == ["r1", "r3"]
    assert [r.run_id for r in RunStore.load(path).by_task("t1")] == ["r1", "r3"]
    assert store.get("r2") is not None and store.get("nope") is None
    assert "r1" in store and "nope" not in store


def test_run_store_rejects_a_corrupt_line_rather_than_skipping_it(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    store = RunStore(path)
    store.append(component_run("r1", "a", Outcome.SUCCESS))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
    with pytest.raises(ValueError, match="malformed run record"):
        RunStore.load(path)


def test_run_store_requires_identifiers() -> None:
    with pytest.raises(ValidationError):
        RunRecord(run_id="", task_id="t")
    with pytest.raises(ValidationError):
        RunRecord(run_id="r", task_id="")


def test_run_store_groups_by_workflow_and_structural_key(tmp_path) -> None:
    store = RunStore(tmp_path / "runs.jsonl")
    store.extend([rich_record("run-1"), rich_record("run-2")])
    store.append(component_run("run-3", "solo", Outcome.SUCCESS))

    assert [r.run_id for r in store.by_workflow("wf-7")] == ["run-1", "run-2"]
    assert [r.run_id for r in store.by_structural_key("atomic(x,s1)")] == ["run-3"]
    assert [r.run_id for r in store] == ["run-1", "run-2", "run-3"]
    assert store.by_workflow("absent") == []


def test_in_memory_store_writes_nothing(tmp_path) -> None:
    store = RunStore()
    store.append(rich_record())
    assert store.path is None
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# StatisticsStore: idempotence, snapshot round trip, determinism
# ---------------------------------------------------------------------------


def test_observe_run_is_idempotent() -> None:
    stats = StatisticsStore()
    record = rich_record()
    stats.observe_run(record)
    stats.observe_run(record)
    stats.observe_run(record.model_copy(deep=True))
    assert stats.n_runs_observed == 1
    assert stats.component_reliability("seurat").alpha == 2.0
    assert stats.component_reliability("geneagent").beta == 2.0
    assert stats.has_observed("run-1") is True


def test_snapshot_replay_after_load_does_not_double_count(tmp_path) -> None:
    runs = tmp_path / "runs.jsonl"
    snap = tmp_path / "stats.json"
    run_store = RunStore(runs)
    run_store.append(rich_record("run-1"))
    run_store.append(rich_record("run-2"))

    stats = StatisticsStore.from_runs(run_store.all())
    stats.save(snap)

    # The normal recovery path: load the snapshot, then replay the log.
    recovered = StatisticsStore.load(snap)
    recovered.observe_runs(RunStore.load(runs).all())

    assert recovered.component_reliability("seurat").alpha == 3.0
    assert recovered.component_reliability("seurat").alpha == (
        stats.component_reliability("seurat").alpha
    )


def test_statistics_snapshot_round_trip_is_lossless(tmp_path) -> None:
    path = tmp_path / "stats.json"
    stats = StatisticsStore.from_runs(
        [
            rich_record("run-1"),
            component_run("run-2", "seurat", Outcome.UNDETERMINED),
            edge_run("run-3", "seurat", "geneagent", "GeneSet", Outcome.SUCCESS),
            repair_run(
                "run-4",
                RepairAttempt(
                    transaction_id="tx9",
                    fault_class=FaultClass.ENVIRONMENT,
                    patch_family="pin_environment",
                    committed=True,
                    fixed_original_failure=True,
                ),
            ),
        ]
    )
    stats.save(path)
    reloaded = StatisticsStore.load(path)

    assert reloaded.snapshot() == stats.snapshot()
    assert reloaded.to_json() == stats.to_json()
    for name in stats.known_components():
        assert reloaded.component_counts(name) == stats.component_counts(name)
        assert reloaded.failure_distribution(name) == stats.failure_distribution(name)
    for key in stats.known_edges():
        assert reloaded.edge_compatibility(*key) == stats.edge_compatibility(*key)
    for key in stats.known_repairs():
        assert reloaded.repair_success(*key) == stats.repair_success(*key)
    assert reloaded.n_runs_observed == stats.n_runs_observed


def test_snapshot_is_sorted_regardless_of_insertion_order() -> None:
    # Enough entries that a set/dict iteration order coinciding with sorted
    # order by chance is not a plausible explanation for a passing test.
    names = [f"c{i:02d}" for i in range(12)]
    scrambled = names[6:] + names[:6][::-1]
    stats = StatisticsStore.from_runs(
        [
            component_run(f"run-{name}", name, Outcome.SUCCESS)
            for name in scrambled
        ]
        + [
            edge_run(f"edge-{name}", name, "sink", "GeneSet", Outcome.SUCCESS)
            for name in scrambled
        ]
    )
    snapshot = stats.snapshot()
    assert snapshot.runs_observed == sorted(snapshot.runs_observed)
    assert list(snapshot.components) == names
    assert [e.producer for e in snapshot.edges] == names
    assert stats.known_components() == names


def test_snapshot_bytes_are_order_independent() -> None:
    a = component_run("r1", "alpha", Outcome.SUCCESS)
    b = component_run("r2", "beta", Outcome.FAILURE, fault_class=FaultClass.ENVIRONMENT)
    c = edge_run("r3", "alpha", "beta", "GeneSet", Outcome.SUCCESS)
    forward = StatisticsStore.from_runs([a, b, c]).to_json()
    backward = StatisticsStore.from_runs([c, b, a]).to_json()
    assert forward == backward


def test_snapshot_is_parseable_as_the_declared_model(tmp_path) -> None:
    path = tmp_path / "stats.json"
    stats = StatisticsStore.from_runs([rich_record()])
    stats.save(path)
    snapshot = StatisticsSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    assert snapshot.version == 1
    assert snapshot.runs_observed == ["run-1"]
    assert snapshot.components["seurat"].successes == 1
    assert snapshot.edges[0].artifact_type == "GeneSet"
    assert snapshot.repairs[0].patch_family == "insert_adapter"
    assert snapshot.failures[0].confirmed[FaultClass.ARTIFACT_CONTRACT] == 1


def test_missing_snapshot_loads_as_empty_history(tmp_path) -> None:
    stats = StatisticsStore.load(tmp_path / "absent.json")
    assert stats.n_runs_observed == 0
    assert stats.component_reliability("anything") == ReliabilityPosterior()


def test_counts_accessors_return_copies() -> None:
    stats = stats_with(component_run("r1", "c", Outcome.SUCCESS))
    counts = stats.component_counts("c")
    counts.successes = 999
    assert stats.component_reliability("c").alpha == 2.0


def test_statistics_ignore_check_results_and_use_recorded_outcomes() -> None:
    """A green CheckReport must not create reliability on its own.

    The record below reports a passing hard check but no component outcomes,
    which is what a caller that forgot to populate them would produce. The
    correct behaviour is "we learned nothing", not "everything worked".
    """
    record = RunRecord(
        run_id="r1",
        task_id="t",
        checks=CheckReport(
            results=[
                CheckResult(
                    check_id="exit_status", level=CheckLevel.HARD, status=CheckStatus.PASS
                )
            ]
        ),
    )
    stats = stats_with(record)
    assert stats.known_components() == []
    assert stats.component_reliability("anything") == ReliabilityPosterior()


# ---------------------------------------------------------------------------
# Cross-cutting: memory evidence composes with the IR's admissibility rules
# ---------------------------------------------------------------------------


def test_statistical_outcome_evidence_completes_a_fallback_justification() -> None:
    records = [
        component_run(f"f{i}", "flaky", Outcome.FAILURE, fault_class=FaultClass.TOOL_FAILURE)
        for i in range(5)
    ]
    records += [component_run(f"s{i}", "flaky", Outcome.SUCCESS) for i in range(5)]
    stats = stats_with(*records)
    warranted, item = retrieval.fallback_warranted(stats, "flaky")
    assert warranted is True

    record = DesignEvidenceRecord(
        decision_id="d1",
        decision_kind=DecisionKind.ADD_FALLBACK,
        decision="add fallback arm for 'flaky'",
        target="t1",
        evidence=[requirement_evidence("s1", "subgoal s1 must produce GeneSet"), item],
    )
    assert record.admissible is True
    assert record.strongest(EvidenceKind.OUTCOME) is item
