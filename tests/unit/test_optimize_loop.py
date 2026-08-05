"""Typed ECPS state, objective gating, and optimization-loop behavior."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from agentcoop.ir.capability import CostProfile
from agentcoop.ir.dossier import ResourceLimits
from agentcoop.ir.preference import VerbosityMode, VerbosityPolicy
from agentcoop.optimize.state import (
    CostUnit,
    EvaluationCase,
    OptimizationBudget,
    OptimizationEvent,
    OptimizationLedger,
    OptimizationOutcome,
    OptimizationPolicy,
    OptimizationState,
    OptimizationStopReason,
)


def evaluation_case(case_id: str = "case-0", *, seed: int = 7) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        input_fingerprint="input::abc",
        seed=seed,
        limits=ResourceLimits(max_usd=2.0, max_tokens=1000),
        evaluator_ids=("hard-schema", "silent-empty"),
        tool_snapshot_id="tools::v1",
    )


def policy() -> OptimizationPolicy:
    return OptimizationPolicy(
        budget=OptimizationBudget(
            max_candidate_executions=20,
            max_judge_calls=40,
            max_candidates=8,
            max_generations=2,
        ),
        ridge=1.0,
        delta=0.1,
        epsilon={"clarity": 0.0},
        max_iterations=100,
        tolerance=1e-8,
        min_judge_families=2,
        max_payload_chars=50_000,
        verbosity=VerbosityPolicy(mode=VerbosityMode.NONE),
    )


def stopped_event(index: int = 0) -> OptimizationEvent:
    return OptimizationEvent(
        index=index,
        state=OptimizationState.STOPPED,
        detail="terminal",
    )


class TestOptimizationStateRecords:
    def test_case_fingerprint_is_canonical_and_sensitive_to_contract(self) -> None:
        first = evaluation_case()
        reordered = first.model_copy(
            update={"evaluator_ids": tuple(reversed(first.evaluator_ids))}
        )
        changed_seed = evaluation_case(seed=8)

        assert first.fingerprint() == evaluation_case().fingerprint()
        assert first.fingerprint() == reordered.fingerprint()
        assert first.fingerprint().startswith("case::")
        assert first.fingerprint() != changed_seed.fingerprint()

    def test_case_rejects_extra_fields_and_invalid_limits(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationCase.model_validate(
                {**evaluation_case().model_dump(), "unexpected": True}
            )
        with pytest.raises(ValidationError, match="limits"):
            EvaluationCase.model_validate(
                {
                    **evaluation_case().model_dump(),
                    "limits": ResourceLimits(max_usd=-1.0),
                }
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_candidate_executions", -1),
            ("max_judge_calls", True),
            ("max_candidates", -1),
            ("max_generations", -1),
            ("soft_max_usd", math.nan),
            ("soft_max_usd", True),
            ("soft_max_tokens", -1),
            ("soft_max_execution_latency_s", math.inf),
        ],
    )
    def test_budget_rejects_invalid_values(self, field: str, value: object) -> None:
        values = OptimizationBudget().model_dump()
        values[field] = value
        with pytest.raises(ValidationError):
            OptimizationBudget.model_validate(values)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("ridge", 0.0),
            ("ridge", math.inf),
            ("ridge", True),
            ("delta", 0.0),
            ("delta", 1.0),
            ("max_iterations", 0),
            ("tolerance", math.nan),
            ("tolerance", True),
            ("min_judge_families", 1),
            ("max_payload_chars", 0),
        ],
    )
    def test_policy_rejects_invalid_values(self, field: str, value: object) -> None:
        values = policy().model_dump()
        values[field] = value
        with pytest.raises(ValidationError):
            OptimizationPolicy.model_validate(values)

    def test_policy_epsilon_is_finite_non_negative(self) -> None:
        for epsilon in ({"clarity": -0.1}, {"clarity": math.nan}, {"": 0.0}):
            with pytest.raises(ValidationError, match="epsilon"):
                OptimizationPolicy.model_validate(
                    {**policy().model_dump(), "epsilon": epsilon}
                )

    def test_ledger_accounts_execution_and_judge_resources(self) -> None:
        ledger = OptimizationLedger(
            candidate_executions=3,
            judge_calls=4,
            execution_cost=CostProfile(
                usd=1.25, tokens=100, latency_s=3.0, cpu_seconds=2.0
            ),
            judge_cost=CostProfile(usd=0.75, tokens=50, latency_s=1.0),
            cost_accounting_complete=True,
            soft_limits_exceeded=("soft_max_usd",),
        )

        assert ledger.total_cost().usd == pytest.approx(2.0)
        assert ledger.total_cost().tokens == 150
        assert ledger.execution_latency_s == pytest.approx(3.0)
        assert ledger.soft_limits_exceeded == ("soft_max_usd",)

    def test_invalid_cost_requires_incomplete_accounting_stamp(self) -> None:
        with pytest.raises(ValidationError, match="accounting"):
            OptimizationLedger(
                execution_cost=CostProfile(usd=-1.0),
                cost_accounting_complete=True,
            )

        incomplete = OptimizationLedger(
            execution_cost=CostProfile(usd=-1.0),
            cost_accounting_complete=False,
        )
        assert incomplete.cost_accounting_complete is False

    def test_outcome_rejects_duplicate_cases_and_non_contiguous_events(self) -> None:
        base = dict(
            dossier_fingerprint="dossier::abc",
            cases=(evaluation_case("duplicate"), evaluation_case("duplicate")),
            policy=policy(),
            events=(stopped_event(),),
            ledger=OptimizationLedger(),
            state=OptimizationState.STOPPED,
            stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
        )
        with pytest.raises(ValidationError, match="duplicate case"):
            OptimizationOutcome(**base)

        with pytest.raises(ValidationError, match="event indices"):
            OptimizationOutcome(
                **{
                    **base,
                    "cases": (evaluation_case(),),
                    "events": (stopped_event(index=1),),
                }
            )

    def test_outcome_round_trips_schema_and_algorithm_versions(self) -> None:
        outcome = OptimizationOutcome(
            schema_version="schema-test-v3",
            algorithm_version="algorithm-test-v7",
            dossier_fingerprint="dossier::abc",
            cases=(evaluation_case(),),
            policy=policy(),
            events=(stopped_event(),),
            ledger=OptimizationLedger(),
            state=OptimizationState.STOPPED,
            stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
        )

        replayed = OptimizationOutcome.model_validate_json(outcome.model_dump_json())

        assert replayed == outcome
        assert replayed.schema_version == "schema-test-v3"
        assert replayed.algorithm_version == "algorithm-test-v7"

    def test_all_states_and_stop_reasons_are_typed_and_stable(self) -> None:
        assert {state.value for state in OptimizationState} == {
            "initializing",
            "executing",
            "objective_gating",
            "comparing",
            "modeling",
            "mutating",
            "stopped",
        }
        assert {reason.value for reason in OptimizationStopReason} == {
            "objective_singleton",
            "confident_preference",
            "verbosity_policy_singleton",
            "no_admissible_candidates",
            "no_preferences",
            "no_cases",
            "ineligible_evaluator",
            "no_judges",
            "insufficient_judge_diversity",
            "budget_exhausted",
            "resource_accounting_invalid",
            "evaluator_unstable",
            "verbosity_baseline_unavailable",
            "no_mutations",
            "front_stable_unresolved",
        }
        assert CostUnit("usd") is CostUnit.USD
        assert CostUnit("tokens") is CostUnit.TOKENS
