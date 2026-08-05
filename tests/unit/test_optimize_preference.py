"""Criterion-wise Bradley--Terry inference and finite active acquisition."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Mapping

import pytest

from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import Direction, Preference, TaskEvidenceDossier
from agentcoop.ir.preference import (
    JudgeCallRecord,
    JudgeCallStatus,
    MetaRubric,
    OrderedPairJudgment,
    PairRubric,
    PairwisePreferenceObservation,
    PairwiseVerdict,
    PanelAttemptRecord,
    PanelAttemptStatus,
    PolicyObservationStatus,
    PolicyPreferenceObservation,
    PreferenceArchive,
    RubricCriterion,
    Scorepad,
    ScorepadEntry,
    VerbosityMode,
    VerbosityPolicy,
    normalize_order_swaps,
)
from agentcoop.optimize.preference import (
    CriterionModel,
    choose_next_comparison,
    fit_preference_models,
    pair_probability,
    preference_dominates,
    preference_front,
    stable_selected_candidate,
)


def meta(preference_ids: tuple[str, ...]) -> MetaRubric:
    return MetaRubric.from_dossier(
        TaskEvidenceDossier(
            task_id="task",
            goal="Choose the best report.",
            preferences=[
                Preference(
                    preference_id=preference_id,
                    description=f"Prefer better {preference_id}.",
                    dimension=preference_id,
                    direction=Direction.MAXIMIZE,
                )
                for preference_id in preference_ids
            ],
        ),
        version="v1",
    )


def pair_observations(
    left_candidate: str,
    right_candidate: str,
    *,
    winners: Mapping[str, str | None],
    family: str,
    case_id: str,
    confidence: float = 0.8,
    reverse_override: Mapping[str, PairwiseVerdict] | None = None,
) -> tuple[PairwisePreferenceObservation, ...]:
    preference_ids = tuple(winners)
    parent = meta(preference_ids)
    rubric = PairRubric(
        rubric_id=f"rubric::{family}::{case_id}",
        version="v1",
        meta_rubric=parent.ref(),
        criteria=tuple(
            RubricCriterion(
                criterion_id=f"criterion::{preference_id}",
                preference_id=preference_id,
                description=f"Compare {preference_id} symmetrically.",
                direction=Direction.MAXIMIZE,
            )
            for preference_id in preference_ids
        ),
    )
    packet_left = f"packet::{left_candidate}"
    packet_right = f"packet::{right_candidate}"

    def verdict(preference_id: str, *, reverse: bool) -> PairwiseVerdict:
        if reverse and reverse_override and preference_id in reverse_override:
            return reverse_override[preference_id]
        winner = winners[preference_id]
        if winner is None:
            return PairwiseVerdict.TIE
        presented_left = right_candidate if reverse else left_candidate
        return (
            PairwiseVerdict.LEFT
            if winner == presented_left
            else PairwiseVerdict.RIGHT
        )

    def ordered(*, reverse: bool) -> OrderedPairJudgment:
        left_packet, right_packet = (
            (packet_right, packet_left) if reverse else (packet_left, packet_right)
        )
        entries: list[ScorepadEntry] = []
        for preference_id in preference_ids:
            result = verdict(preference_id, reverse=reverse)
            directional = result in {PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT}
            preferred_ref = left_packet if result is PairwiseVerdict.LEFT else right_packet
            entries.append(
                ScorepadEntry(
                    preference_id=preference_id,
                    criterion_id=f"criterion::{preference_id}",
                    verdict=result,
                    left_score=8.0 if directional else 7.0,
                    right_score=6.0 if directional else 7.0,
                    evidence_refs=(f"{preferred_ref}:artifact:0",) if directional else (),
                    confidence=confidence,
                    rationale="Preferred by the criterion." if directional else "",
                )
            )
        return OrderedPairJudgment(
            judgment_id=(
                f"judgment::{family}::{case_id}::"
                f"{left_candidate}::{right_candidate}::{'r' if reverse else 'f'}::"
                + ":".join(result.verdict.value for result in entries)
            ),
            pair_id=f"pair::{'::'.join(sorted((left_candidate, right_candidate)))}",
            case_id=case_id,
            left_packet_id=left_packet,
            right_packet_id=right_packet,
            judge_id=f"judge::{family}",
            judge_family=family,
            source=SignalSource.LLM_JUDGE,
            rubric=rubric,
            scorepad=Scorepad(entries=tuple(entries)),
        )

    return normalize_order_swaps(
        ordered(reverse=False),
        ordered(reverse=True),
        packet_to_candidate={
            packet_left: left_candidate,
            packet_right: right_candidate,
        },
    )


def archive_from_observations(
    observations: tuple[PairwisePreferenceObservation, ...],
    *,
    policy_observations: tuple[PolicyPreferenceObservation, ...] = (),
) -> PreferenceArchive:
    judgments_by_id: dict[str, OrderedPairJudgment] = {}
    target_calls: dict[
        tuple[str, str, str], list[str]
    ] = defaultdict(list)
    calls: list[JudgeCallRecord] = []
    for observation in observations:
        target = (
            observation.case_id,
            *sorted((observation.candidate_a_id, observation.candidate_b_id)),
        )
        for judgment in (observation.forward, observation.reverse):
            if judgment.judgment_id in judgments_by_id:
                continue
            judgments_by_id[judgment.judgment_id] = judgment
            call_id = f"call::{judgment.judgment_id}"
            target_calls[target].append(call_id)
            calls.append(
                JudgeCallRecord(
                    call_id=call_id,
                    status=JudgeCallStatus.AVAILABLE,
                    request_id=f"request::{judgment.judgment_id}",
                    judge_id=judgment.judge_id,
                    judge_family=judgment.judge_family,
                    source=judgment.source,
                    judgment=judgment,
                    cost=CostProfile(),
                )
            )
    attempts = tuple(
        PanelAttemptRecord(
            attempt_id=f"attempt::{case_id}::{candidate_a}::{candidate_b}",
            pair_id=f"pair::{candidate_a}::{candidate_b}",
            case_id=case_id,
            candidate_a_id=candidate_a,
            candidate_b_id=candidate_b,
            call_ids=tuple(call_ids),
            status=PanelAttemptStatus.COMPLETE,
        )
        for (case_id, candidate_a, candidate_b), call_ids in sorted(
            target_calls.items()
        )
    )
    return PreferenceArchive(
        calls=tuple(calls),
        judgments=tuple(judgments_by_id.values()),
        observations=observations,
        policy_observations=policy_observations,
        attempts=attempts,
    )


def unavailable_archive(
    targets: tuple[tuple[str, str, str], ...]
) -> PreferenceArchive:
    calls: list[JudgeCallRecord] = []
    attempts: list[PanelAttemptRecord] = []
    for index, (candidate_a, candidate_b, case_id) in enumerate(targets):
        call_id = f"unavailable-call::{index}"
        calls.append(
            JudgeCallRecord(
                call_id=call_id,
                status=JudgeCallStatus.UNAVAILABLE,
                request_id=f"request::{index}",
                judge_id="judge",
                judge_family="family",
                source=SignalSource.LLM_JUDGE,
                cost=CostProfile(),
                error="unavailable",
            )
        )
        attempts.append(
            PanelAttemptRecord(
                attempt_id=f"unavailable-attempt::{index}",
                pair_id=f"pair::{candidate_a}::{candidate_b}",
                case_id=case_id,
                candidate_a_id=candidate_a,
                candidate_b_id=candidate_b,
                call_ids=(call_id,),
                status=PanelAttemptStatus.UNAVAILABLE,
            )
        )
    return PreferenceArchive(calls=tuple(calls), attempts=tuple(attempts))


def criterion(models, preference_id: str) -> CriterionModel:
    return next(model for model in models.models if model.preference_id == preference_id)


def theta(model: CriterionModel, candidate_id: str) -> float:
    return next(
        estimate.theta
        for estimate in model.estimates
        if estimate.candidate_id == candidate_id
    )


class TestBradleyTerryFit:
    def test_unanimous_fit_is_canonical_deterministic_and_ignores_confidence(self) -> None:
        low_confidence = tuple(
            observation
            for index in range(8)
            for observation in pair_observations(
                "b",
                "a",
                winners={"clarity": "a"},
                family=f"family-{index % 2}",
                case_id=f"case-{index}",
                confidence=0.1,
            )
        )
        high_confidence = tuple(
            observation
            for index in range(8)
            for observation in pair_observations(
                "b",
                "a",
                winners={"clarity": "a"},
                family=f"family-{index % 2}",
                case_id=f"case-{index}",
                confidence=0.99,
            )
        )

        first = fit_preference_models(
            ("b", "a"), ("clarity",), low_confidence, ridge=1.0
        )
        reordered = fit_preference_models(
            ("a", "b"), ("clarity",), tuple(reversed(low_confidence)), ridge=1.0
        )
        confidence_changed = fit_preference_models(
            ("a", "b"), ("clarity",), high_confidence, ridge=1.0
        )

        assert first == reordered == confidence_changed
        assert first.candidate_ids == ("a", "b")
        model = criterion(first, "clarity")
        assert model.connected and model.converged and not model.cycle_detected
        assert theta(model, "a") > theta(model, "b")
        assert math.isclose(sum(estimate.theta for estimate in model.estimates), 0.0)
        assert first.model_dump_json() == fit_preference_models(
            ("b", "a"), ("clarity",), low_confidence, ridge=1.0
        ).model_dump_json()

    def test_one_fractional_tie_row_has_exact_reduced_covariance(self) -> None:
        observations = pair_observations(
            "a",
            "b",
            winners={"clarity": None},
            family="family",
            case_id="case",
        )
        fitted = fit_preference_models(
            ("a", "b"), ("clarity",), observations, ridge=1.0
        )
        model = criterion(fitted, "clarity")

        assert theta(model, "a") == pytest.approx(0.0, abs=1e-12)
        assert theta(model, "b") == pytest.approx(0.0, abs=1e-12)
        assert model.covariance[0][0] == pytest.approx(1.0 / 3.0)
        assert model.covariance[0][1] == pytest.approx(-1.0 / 3.0)
        pair_variance = (
            model.covariance[0][0]
            + model.covariance[1][1]
            - 2.0 * model.covariance[0][1]
        )
        assert pair_variance == pytest.approx(4.0 / 3.0)
        assert pair_probability(model, "a", "b") == pytest.approx(0.5)

    def test_inconsistent_duplicate_abstain_and_unresolved_rows_are_excluded(self) -> None:
        a_wins = pair_observations(
            "a",
            "b",
            winners={"clarity": "a"},
            family="same-family",
            case_id="same-case",
        )
        b_wins = pair_observations(
            "a",
            "b",
            winners={"clarity": "b"},
            family="same-family",
            case_id="same-case",
        )
        abstain = pair_observations(
            "a",
            "b",
            winners={"clarity": None},
            family="abstain-family",
            case_id="abstain-case",
            reverse_override={"clarity": PairwiseVerdict.ABSTAIN},
        )
        unresolved = pair_observations(
            "a",
            "b",
            winners={"clarity": "a"},
            family="biased-family",
            case_id="biased-case",
            reverse_override={"clarity": PairwiseVerdict.LEFT},
        )

        fitted = fit_preference_models(
            ("a", "b"),
            ("clarity",),
            (*a_wins, *b_wins, *abstain, *unresolved),
        )

        model = criterion(fitted, "clarity")
        assert model.connected is False
        assert theta(model, "a") == pytest.approx(theta(model, "b"))
        assert any("inconsistent" in note for note in fitted.notes)

    def test_connectivity_and_cycles_are_criterion_wise(self) -> None:
        observations = (
            *pair_observations(
                "a", "b", winners={"clarity": "a"}, family="f1", case_id="c1"
            ),
            *pair_observations(
                "b", "c", winners={"coverage": "b"}, family="f2", case_id="c2"
            ),
        )
        fitted = fit_preference_models(
            ("a", "b", "c"), ("clarity", "coverage"), observations
        )

        clarity = criterion(fitted, "clarity")
        coverage = criterion(fitted, "coverage")
        assert clarity.connected is False
        assert clarity.components == (("a", "b"), ("c",))
        assert coverage.components == (("a",), ("b", "c"))

        cycle_observations = (
            *pair_observations(
                "a", "b", winners={"clarity": "a"}, family="f1", case_id="ab"
            ),
            *pair_observations(
                "b", "c", winners={"clarity": "b"}, family="f2", case_id="bc"
            ),
            *pair_observations(
                "a", "c", winners={"clarity": "c"}, family="f3", case_id="ca"
            ),
        )
        cyclic = fit_preference_models(
            ("a", "b", "c"), ("clarity",), cycle_observations
        )
        assert criterion(cyclic, "clarity").cycle_detected is True
        assert preference_front(cyclic, epsilon={"clarity": 0.0}, delta=0.1) == (
            "a",
            "b",
            "c",
        )

    def test_explicit_iteration_limit_reports_non_convergence(self) -> None:
        observations = pair_observations(
            "a", "b", winners={"clarity": "a"}, family="family", case_id="case"
        )
        fitted = fit_preference_models(
            ("a", "b"),
            ("clarity",),
            observations,
            max_iterations=0,
        )

        assert criterion(fitted, "clarity").converged is False
        assert preference_dominates(
            fitted, "a", "b", epsilon={"clarity": 0.0}, delta=0.1
        ) is False

    def test_convergence_uses_projected_theta_gradient(self) -> None:
        outcomes = (
            ("a", "b", "b", 2),
            ("a", "c", "c", 2),
            ("a", "d", "d", 2),
            ("b", "c", "c", 1),
            ("b", "d", "b", 2),
            ("c", "d", "c", 1),
        )
        observations = tuple(
            observation
            for candidate_a, candidate_b, winner, count in outcomes
            for index in range(count)
            for observation in pair_observations(
                candidate_a,
                candidate_b,
                winners={"clarity": winner},
                family="family",
                case_id=f"{candidate_a}-{candidate_b}-{index}",
            )
        )

        fitted = fit_preference_models(
            ("a", "b", "c", "d"),
            ("clarity",),
            observations,
            max_iterations=0,
            tolerance=2.75,
        )

        assert criterion(fitted, "clarity").converged is False

    def test_singular_unregularized_fit_cannot_converge_or_select(self) -> None:
        fitted = fit_preference_models(
            ("a", "b"), ("clarity",), (), ridge=0.0
        )

        model = criterion(fitted, "clarity")
        assert model.converged is False
        assert model.covariance == ()
        assert preference_front(
            fitted, epsilon={"clarity": 0.0}, delta=0.1
        ) == ("a", "b")


class TestDominanceAndStability:
    def strong_observations(
        self,
        *,
        families: tuple[str, ...] = ("family-1", "family-2"),
        cases_per_family: int = 80,
        preference_ids: tuple[str, ...] = ("clarity",),
    ) -> tuple[PairwisePreferenceObservation, ...]:
        return tuple(
            observation
            for family in families
            for index in range(cases_per_family)
            for observation in pair_observations(
                "a",
                "b",
                winners={preference_id: "a" for preference_id in preference_ids},
                family=family,
                case_id=f"{family}-case-{index}",
            )
        )

    def test_bonferroni_lower_bounds_define_dominance_and_front(self) -> None:
        observations = self.strong_observations(cases_per_family=40)
        fitted = fit_preference_models(
            ("a", "b"), ("clarity",), observations, ridge=1.0
        )

        assert preference_dominates(
            fitted, "a", "b", epsilon={"clarity": 0.0}, delta=0.1
        )
        assert not preference_dominates(
            fitted, "b", "a", epsilon={"clarity": 0.0}, delta=0.1
        )
        assert preference_front(
            fitted, epsilon={"clarity": 0.0}, delta=0.1
        ) == ("a",)
        with pytest.raises(ValueError, match="epsilon"):
            preference_front(fitted, epsilon={}, delta=0.1)

    def test_underflowed_bonferroni_alpha_conservatively_refuses_dominance(self) -> None:
        fitted = fit_preference_models(
            ("a", "b"),
            ("clarity",),
            self.strong_observations(cases_per_family=40),
        )

        assert not preference_dominates(
            fitted,
            "a",
            "b",
            epsilon={"clarity": 0.0},
            delta=5e-324,
        )

    def test_exactly_two_agreeing_families_can_survive_leave_one_out(self) -> None:
        observations = self.strong_observations()
        archive = archive_from_observations(observations)

        assert stable_selected_candidate(
            ("a", "b"),
            ("clarity",),
            archive,
            epsilon={"clarity": 0.0},
            delta=0.1,
            min_judge_families=2,
            ridge=1.0,
            max_iterations=100,
            tolerance=1e-8,
        ) == "a"

        weak_second_family = (
            *self.strong_observations(families=("family-1",)),
            *self.strong_observations(
                families=("family-2",), cases_per_family=1
            ),
        )
        assert stable_selected_candidate(
            ("a", "b"),
            ("clarity",),
            archive_from_observations(weak_second_family),
            epsilon={"clarity": 0.0},
            delta=0.1,
            min_judge_families=2,
            ridge=1.0,
            max_iterations=100,
            tolerance=1e-8,
        ) is None

    def test_family_coverage_is_required_for_every_criterion_and_policy_is_ignored(self) -> None:
        both = self.strong_observations(
            preference_ids=("clarity",), cases_per_family=30
        )
        coverage_one_family = tuple(
            observation
            for index in range(30)
            for observation in pair_observations(
                "a",
                "b",
                winners={"coverage": "a"},
                family="family-1",
                case_id=f"coverage-{index}",
            )
        )
        policy = PolicyPreferenceObservation(
            observation_id="policy",
            pair_id="pair::a::b",
            case_id="policy-case",
            preference_ids=("clarity", "coverage"),
            candidate_a_id="a",
            candidate_b_id="b",
            status=PolicyObservationStatus.DIRECTIONAL,
            preferred_candidate_id="a",
            policy=VerbosityPolicy(
                mode=VerbosityMode.AUTO_LOSS,
                baseline_candidate_id="a",
                sigma=1.5,
            ),
            baseline_candidate_id="a",
            candidate_a_length=100,
            candidate_b_length=151,
            baseline_length=100,
            threshold=150.0,
            reason="b exceeds the verbosity budget",
        )
        archive = archive_from_observations(
            (*both, *coverage_one_family), policy_observations=(policy,)
        )

        assert stable_selected_candidate(
            ("a", "b"),
            ("clarity", "coverage"),
            archive,
            epsilon={"clarity": 0.0, "coverage": 0.0},
            delta=0.1,
            min_judge_families=2,
            ridge=1.0,
            max_iterations=100,
            tolerance=1e-8,
        ) is None


class TestAcquisition:
    def test_disconnected_graph_uses_lexicographic_anchor_and_never_retries_attempt(self) -> None:
        observations = pair_observations(
            "a", "b", winners={"clarity": "a"}, family="family", case_id="seen"
        )
        models = fit_preference_models(
            ("a", "b", "c"), ("clarity",), observations
        )
        archive = unavailable_archive((("a", "c", "dev-0"),))

        target = choose_next_comparison(
            ("a", "b", "c"),
            ("dev-0", "dev-1"),
            archive,
            models,
        )

        assert target is not None
        assert (
            target.candidate_a_id,
            target.candidate_b_id,
            target.case_id,
        ) == ("a", "c", "dev-1")

    def test_connected_acquisition_maximizes_information_with_lexicographic_ties(self) -> None:
        observations = (
            *pair_observations(
                "a", "b", winners={"clarity": "a"}, family="f1", case_id="ab"
            ),
            *pair_observations(
                "b", "c", winners={"clarity": "b"}, family="f2", case_id="bc"
            ),
        )
        models = fit_preference_models(
            ("a", "b", "c"), ("clarity",), observations
        )
        target = choose_next_comparison(
            ("a", "b", "c"),
            ("dev-1", "dev-0"),
            PreferenceArchive(),
            models,
        )

        assert target is not None
        model = criterion(models, "clarity")
        candidates = models.candidate_ids
        index = {candidate: i for i, candidate in enumerate(candidates)}
        expected_values: dict[tuple[str, str], float] = {}
        for candidate_a, candidate_b in (("a", "b"), ("a", "c"), ("b", "c")):
            ia, ib = index[candidate_a], index[candidate_b]
            variance = (
                model.covariance[ia][ia]
                + model.covariance[ib][ib]
                - 2.0 * model.covariance[ia][ib]
            )
            probability = pair_probability(model, candidate_a, candidate_b)
            expected_values[(candidate_a, candidate_b)] = (
                probability * (1.0 - probability) * variance / 4.0
            )
        expected_pair = min(
            pair
            for pair, value in expected_values.items()
            if value == max(expected_values.values())
        )
        assert (target.candidate_a_id, target.candidate_b_id) == expected_pair
        assert target.case_id == "dev-0"
        assert target.acquisition == pytest.approx(expected_values[expected_pair])

    def test_exhausted_finite_target_set_returns_none(self) -> None:
        candidates = ("a", "b")
        cases = ("dev-0", "dev-1")
        archive = unavailable_archive(
            (("a", "b", "dev-0"), ("b", "a", "dev-1"))
        )
        models = fit_preference_models(candidates, ("clarity",), ())

        assert choose_next_comparison(candidates, cases, archive, models) is None

    def test_empty_case_set_is_an_empty_finite_target_set(self) -> None:
        models = fit_preference_models(("a", "b"), ("clarity",), ())

        assert choose_next_comparison(
            ("a", "b"), (), PreferenceArchive(), models
        ) is None
