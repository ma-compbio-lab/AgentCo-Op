"""Persistent, auditable preference-evaluation protocol records."""

from __future__ import annotations

from itertools import product

import pytest
from pydantic import ValidationError

from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import Direction, Preference, TaskEvidenceDossier
from agentcoop.ir.preference import (
    JudgeCallRecord,
    JudgeCallStatus,
    MetaCriterion,
    MetaRubric,
    ObservationStatus,
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


def dossier(*, description: str = "Prefer clarity", weight: float | None = 0.9) -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="task",
        goal="Produce an actionable report.",
        preferences=[
            Preference(
                preference_id="clarity",
                description=description,
                dimension="clarity",
                direction=Direction.MAXIMIZE,
                weight_hint=weight,
            )
        ],
    )


def meta() -> MetaRubric:
    return MetaRubric.from_dossier(dossier(), version="v1")


def rubric(*criteria: RubricCriterion) -> PairRubric:
    if not criteria:
        criteria = (
            RubricCriterion(
                criterion_id="clarity-op",
                preference_id="clarity",
                description="Prefer the clearer actionable result.",
                direction=Direction.MAXIMIZE,
            ),
        )
    return PairRubric(
        rubric_id="pair-rubric",
        version="v1",
        meta_rubric=meta().ref(),
        criteria=criteria,
    )


def entry(
    verdict: PairwiseVerdict,
    *,
    criterion_id: str = "clarity-op",
    preference_id: str = "clarity",
    left_score: float | None = 8.0,
    right_score: float | None = 6.0,
    evidence_ref: str = "packet:artifact:answer",
) -> ScorepadEntry:
    return ScorepadEntry(
        criterion_id=criterion_id,
        preference_id=preference_id,
        verdict=verdict,
        left_score=left_score,
        right_score=right_score,
        evidence_refs=(evidence_ref,) if verdict in {PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT} else (),
        confidence=0.8,
        missing_evidence=(),
        rationale=(
            "The preferred result states an executable recommendation."
            if verdict in {PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT}
            else ""
        ),
    )


def judgment(
    verdict: PairwiseVerdict,
    *,
    reverse: bool = False,
    pair_rubric: PairRubric | None = None,
    entries: tuple[ScorepadEntry, ...] | None = None,
) -> OrderedPairJudgment:
    left, right = (("pb", "pa") if reverse else ("pa", "pb"))
    return OrderedPairJudgment(
        judgment_id=f"j:{'r' if reverse else 'f'}:{verdict.value}",
        pair_id="pair",
        case_id="case",
        left_packet_id=left,
        right_packet_id=right,
        judge_id="judge",
        judge_family="family",
        source=SignalSource.LLM_JUDGE,
        rubric=pair_rubric or rubric(),
        scorepad=Scorepad(entries=entries or (entry(verdict, evidence_ref=f"{left}:artifact:answer"),)),
    )


class TestMetaRubric:
    def test_from_dossier_is_frozen_stable_and_ignores_weight_hint(self) -> None:
        first = MetaRubric.from_dossier(dossier(weight=0.1), version="v1")
        second = MetaRubric.from_dossier(dossier(weight=100.0), version="v1")

        assert first == second
        assert first.criteria == (
            MetaCriterion(
                preference_id="clarity",
                description="Prefer clarity",
                dimension="clarity",
                direction=Direction.MAXIMIZE,
            ),
        )
        assert first.ref() == second.ref()
        assert len(first.ref().content_hash) == 64
        with pytest.raises(ValidationError):
            first.version = "v2"  # type: ignore[misc]

    def test_fingerprint_is_content_sensitive(self) -> None:
        first = MetaRubric.from_dossier(dossier(), version="v1")
        changed = MetaRubric.from_dossier(dossier(description="Prefer concise clarity"), version="v1")

        assert first.ref().content_hash != changed.ref().content_hash
        assert first.rubric_id != changed.rubric_id

    def test_fingerprint_normalizes_version_before_hashing(self) -> None:
        assert MetaRubric.from_dossier(
            dossier(), version=" v1 "
        ) == MetaRubric.from_dossier(dossier(), version="v1")

    def test_empty_and_duplicate_identifiers_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetaCriterion(
                preference_id="",
                description="description",
                dimension="clarity",
                direction=Direction.MAXIMIZE,
            )

        duplicate = dossier()
        duplicate.preferences.append(duplicate.preferences[0].model_copy())
        with pytest.raises(ValueError, match="duplicate preference_id"):
            MetaRubric.from_dossier(duplicate, version="v1")

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            MetaCriterion.model_validate(
                {
                    "preference_id": "clarity",
                    "description": "Prefer clarity",
                    "dimension": "clarity",
                    "direction": "maximize",
                    "weight": 9,
                }
            )


class TestRubricAndScorepad:
    def test_pair_rubric_has_stable_content_reference(self) -> None:
        first = rubric()
        second = PairRubric.model_validate_json(first.model_dump_json())

        assert first.ref() == second.ref()
        assert first.ref().rubric_id == "pair-rubric"

    def test_duplicate_rubric_criteria_or_preferences_are_rejected(self) -> None:
        criterion = rubric().criteria[0]
        with pytest.raises(ValidationError, match="duplicate criterion_id"):
            rubric(criterion, criterion)

        with pytest.raises(ValidationError, match="duplicate preference_id"):
            rubric(
                criterion,
                RubricCriterion(
                    criterion_id="other",
                    preference_id="clarity",
                    description="Same parent twice",
                    direction=Direction.MAXIMIZE,
                ),
            )

    def test_pair_rubric_validates_complete_meta_mapping(self) -> None:
        complete = rubric()
        assert complete.validate_against(meta()) is complete

        expanded_dossier = dossier()
        expanded_dossier.preferences.append(
            Preference(
                preference_id="cost",
                description="Prefer lower cost",
                dimension="cost",
                direction=Direction.MINIMIZE,
            )
        )
        expanded_meta = MetaRubric.from_dossier(expanded_dossier, version="v1")
        incomplete = PairRubric(
            rubric_id="incomplete",
            version="v1",
            meta_rubric=expanded_meta.ref(),
            criteria=rubric().criteria,
        )
        with pytest.raises(ValueError, match="exactly"):
            incomplete.validate_against(expanded_meta)

        wrong_direction = PairRubric(
            rubric_id="wrong-direction",
            version="v1",
            meta_rubric=meta().ref(),
            criteria=(
                RubricCriterion(
                    criterion_id="clarity-op",
                    preference_id="clarity",
                    description="Prefer the clearer actionable result.",
                    direction=Direction.MINIMIZE,
                ),
            ),
        )
        with pytest.raises(ValueError, match="direction"):
            wrong_direction.validate_against(meta())

    @pytest.mark.parametrize("field,value", [("left_score", -0.1), ("right_score", 10.1)])
    def test_scores_must_be_finite_and_in_range(self, field: str, value: float) -> None:
        values = {"left_score": 8.0, "right_score": 6.0, field: value}
        with pytest.raises(ValidationError):
            entry(PairwiseVerdict.LEFT, **values)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_confidence_must_be_finite(self, value: float) -> None:
        data = entry(PairwiseVerdict.LEFT).model_dump()
        data["confidence"] = value
        with pytest.raises(ValidationError):
            ScorepadEntry.model_validate(data)

    def test_directional_entry_requires_reference_and_rationale(self) -> None:
        data = entry(PairwiseVerdict.LEFT).model_dump()
        data["evidence_refs"] = ()
        with pytest.raises(ValidationError, match="evidence reference"):
            ScorepadEntry.model_validate(data)

        data = entry(PairwiseVerdict.LEFT).model_dump()
        data["rationale"] = ""
        with pytest.raises(ValidationError, match="rationale"):
            ScorepadEntry.model_validate(data)

    def test_scorepad_must_cover_rubric_exactly(self) -> None:
        with pytest.raises(ValidationError, match="scorepad criteria"):
            OrderedPairJudgment(
                **judgment(PairwiseVerdict.LEFT).model_dump(exclude={"scorepad"}),
                scorepad=Scorepad(
                    entries=(entry(PairwiseVerdict.LEFT, criterion_id="unknown"),)
                ),
            )

        with pytest.raises(ValidationError, match="preference_id"):
            OrderedPairJudgment(
                **judgment(PairwiseVerdict.LEFT).model_dump(exclude={"scorepad"}),
                scorepad=Scorepad(
                    entries=(entry(PairwiseVerdict.LEFT, preference_id="coverage"),)
                ),
            )

    def test_scorepad_rejects_duplicate_criteria(self) -> None:
        duplicate = entry(PairwiseVerdict.LEFT)
        with pytest.raises(ValidationError, match="duplicate criterion_id"):
            Scorepad(entries=(duplicate, duplicate))

    @pytest.mark.parametrize(
        ("verdicts", "expected"),
        [
            ((PairwiseVerdict.LEFT, PairwiseVerdict.TIE), PairwiseVerdict.LEFT),
            ((PairwiseVerdict.RIGHT, PairwiseVerdict.TIE), PairwiseVerdict.RIGHT),
            ((PairwiseVerdict.TIE, PairwiseVerdict.TIE), PairwiseVerdict.TIE),
            ((PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT), PairwiseVerdict.ABSTAIN),
            ((PairwiseVerdict.LEFT, PairwiseVerdict.ABSTAIN), PairwiseVerdict.ABSTAIN),
        ],
    )
    def test_final_verdict_uses_criterion_pareto_logic(
        self, verdicts: tuple[PairwiseVerdict, PairwiseVerdict], expected: PairwiseVerdict
    ) -> None:
        pair_rubric = rubric(
            RubricCriterion(
                criterion_id="clarity-op",
                preference_id="clarity",
                description="Prefer clarity.",
                direction=Direction.MAXIMIZE,
            ),
            RubricCriterion(
                criterion_id="cost-op",
                preference_id="cost",
                description="Prefer lower cost.",
                direction=Direction.MINIMIZE,
            ),
        )
        entries = (
            entry(verdicts[0]),
            entry(verdicts[1], criterion_id="cost-op", preference_id="cost"),
        )
        result = judgment(verdicts[0], pair_rubric=pair_rubric, entries=entries)

        assert result.final_verdict() is expected


class TestOrderSwapNormalization:
    @pytest.mark.parametrize(
        ("forward_verdict", "reverse_verdict", "status", "preferred"),
        [
            (PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT, ObservationStatus.DIRECTIONAL, "a"),
            (PairwiseVerdict.RIGHT, PairwiseVerdict.LEFT, ObservationStatus.DIRECTIONAL, "b"),
            (PairwiseVerdict.TIE, PairwiseVerdict.TIE, ObservationStatus.TIE, None),
            (PairwiseVerdict.ABSTAIN, PairwiseVerdict.ABSTAIN, ObservationStatus.ABSTAIN, None),
        ],
    )
    def test_consistent_truth_table_rows(
        self,
        forward_verdict: PairwiseVerdict,
        reverse_verdict: PairwiseVerdict,
        status: ObservationStatus,
        preferred: str | None,
    ) -> None:
        observations = normalize_order_swaps(
            judgment(forward_verdict),
            judgment(reverse_verdict, reverse=True),
            packet_to_candidate={"pa": "a", "pb": "b"},
        )

        assert len(observations) == 1
        assert observations[0].status is status
        assert observations[0].preferred_candidate_id == preferred
        assert observations[0].candidate_a_id == "a"
        assert observations[0].candidate_b_id == "b"

    @pytest.mark.parametrize(
        ("forward_verdict", "reverse_verdict"),
        [
            row
            for row in product(PairwiseVerdict, repeat=2)
            if row
            not in {
                (PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT),
                (PairwiseVerdict.RIGHT, PairwiseVerdict.LEFT),
                (PairwiseVerdict.TIE, PairwiseVerdict.TIE),
                (PairwiseVerdict.ABSTAIN, PairwiseVerdict.ABSTAIN),
            }
        ],
    )
    def test_every_other_truth_table_row_is_unresolved(
        self, forward_verdict: PairwiseVerdict, reverse_verdict: PairwiseVerdict
    ) -> None:
        observation = normalize_order_swaps(
            judgment(forward_verdict),
            judgment(reverse_verdict, reverse=True),
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0]

        assert observation.status is ObservationStatus.UNRESOLVED
        assert observation.preferred_candidate_id is None

    @pytest.mark.parametrize("changed", ["pair", "case", "judge", "family", "source", "order"])
    def test_protocol_mismatch_is_rejected(self, changed: str) -> None:
        forward = judgment(PairwiseVerdict.LEFT)
        data = judgment(PairwiseVerdict.RIGHT, reverse=True).model_dump()
        if changed == "pair":
            data["pair_id"] = "other"
        elif changed == "case":
            data["case_id"] = "other"
        elif changed == "judge":
            data["judge_id"] = "other"
        elif changed == "family":
            data["judge_family"] = "other"
        elif changed == "source":
            data["source"] = SignalSource.HUMAN
        else:
            data["left_packet_id"], data["right_packet_id"] = "pa", "pb"
        reverse = OrderedPairJudgment.model_validate(data)

        with pytest.raises(ValueError, match="order-swapped"):
            normalize_order_swaps(
                forward,
                reverse,
                packet_to_candidate={"pa": "a", "pb": "b"},
            )

    def test_changed_frozen_rubric_is_rejected(self) -> None:
        reverse_data = judgment(PairwiseVerdict.RIGHT, reverse=True).model_dump()
        reverse_data["rubric"]["criteria"][0]["description"] = "Changed after seeing the order."
        reverse = OrderedPairJudgment.model_validate(reverse_data)

        with pytest.raises(ValueError, match="rubric"):
            normalize_order_swaps(
                judgment(PairwiseVerdict.LEFT),
                reverse,
                packet_to_candidate={"pa": "a", "pb": "b"},
            )

    def test_missing_or_aliased_packet_mapping_is_rejected(self) -> None:
        forward = judgment(PairwiseVerdict.LEFT)
        reverse = judgment(PairwiseVerdict.RIGHT, reverse=True)
        with pytest.raises(ValueError, match="packet"):
            normalize_order_swaps(forward, reverse, packet_to_candidate={"pa": "a"})
        with pytest.raises(ValueError, match="distinct"):
            normalize_order_swaps(
                forward,
                reverse,
                packet_to_candidate={"pa": "a", "pb": "a"},
            )


class TestObservationValidation:
    def test_judge_observation_requires_both_swapped_judgments(self) -> None:
        forward = judgment(PairwiseVerdict.LEFT)
        data = normalize_order_swaps(
            forward,
            judgment(PairwiseVerdict.RIGHT, reverse=True),
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0].model_dump()
        data["reverse"] = None
        with pytest.raises(ValidationError):
            PairwisePreferenceObservation.model_validate(data)

    def test_judge_observation_rejects_non_swapped_packet_order(self) -> None:
        observation = normalize_order_swaps(
            judgment(PairwiseVerdict.LEFT),
            judgment(PairwiseVerdict.RIGHT, reverse=True),
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0]
        data = observation.model_dump()
        data["reverse"] = judgment(PairwiseVerdict.RIGHT).model_dump()

        with pytest.raises(ValidationError, match="order-swapped"):
            PairwisePreferenceObservation.model_validate(data)

    def test_status_and_preferred_candidate_must_agree(self) -> None:
        observation = normalize_order_swaps(
            judgment(PairwiseVerdict.LEFT),
            judgment(PairwiseVerdict.RIGHT, reverse=True),
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0]
        data = observation.model_dump()
        data["preferred_candidate_id"] = None
        with pytest.raises(ValidationError, match="preferred_candidate_id"):
            PairwisePreferenceObservation.model_validate(data)

        data = observation.model_dump()
        data["preferred_candidate_id"] = "b"
        with pytest.raises(ValidationError, match="normalized verdict"):
            PairwisePreferenceObservation.model_validate(data)

        data = observation.model_dump()
        data["preference_id"] = "unknown"
        with pytest.raises(ValidationError, match="preference_id"):
            PairwisePreferenceObservation.model_validate(data)

        data = observation.model_dump()
        data["confidence"] = 0.1
        with pytest.raises(ValidationError, match="confidence"):
            PairwisePreferenceObservation.model_validate(data)

    def test_verbosity_policy_is_independent_and_deterministic(self) -> None:
        policy = VerbosityPolicy(
            mode=VerbosityMode.AUTO_LOSS,
            baseline_candidate_id="a",
            sigma=1.5,
        )
        observation = PolicyPreferenceObservation(
            observation_id="policy:1",
            pair_id="pair",
            case_id="case",
            preference_ids=("clarity",),
            candidate_a_id="a",
            candidate_b_id="b",
            status=PolicyObservationStatus.DIRECTIONAL,
            preferred_candidate_id="a",
            source=SignalSource.DETERMINISTIC,
            policy=policy,
            baseline_candidate_id="a",
            candidate_a_length=100,
            candidate_b_length=151,
            baseline_length=100,
            threshold=150.0,
            reason="candidate b exceeds the verbosity budget",
        )

        assert observation.preferred_candidate_id == "a"
        with pytest.raises(ValidationError, match="deterministic"):
            PolicyPreferenceObservation.model_validate(
                {**observation.model_dump(), "source": SignalSource.LLM_JUDGE}
            )

    def test_invalid_verbosity_configuration_and_false_auto_loss_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sigma"):
            VerbosityPolicy(
                mode=VerbosityMode.AUTO_LOSS,
                baseline_candidate_id="a",
                sigma=1.0,
            )

        valid = PolicyPreferenceObservation(
            observation_id="policy:1",
            pair_id="pair",
            case_id="case",
            preference_ids=("clarity",),
            candidate_a_id="a",
            candidate_b_id="b",
            status=PolicyObservationStatus.ABSTAIN,
            preferred_candidate_id=None,
            source=SignalSource.DETERMINISTIC,
            policy=VerbosityPolicy(
                mode=VerbosityMode.AUTO_LOSS,
                baseline_candidate_id="a",
                sigma=1.5,
            ),
            baseline_candidate_id="a",
            candidate_a_length=100,
            candidate_b_length=149,
            baseline_length=100,
            threshold=150.0,
            reason="within budget",
        )
        data = valid.model_dump()
        data["status"] = PolicyObservationStatus.DIRECTIONAL
        data["preferred_candidate_id"] = "a"
        with pytest.raises(ValidationError, match="exceed"):
            PolicyPreferenceObservation.model_validate(data)

        over_budget = valid.model_dump()
        over_budget["candidate_b_length"] = 151
        with pytest.raises(ValidationError, match="abstain"):
            PolicyPreferenceObservation.model_validate(over_budget)

        contradictory_baseline = valid.model_dump()
        contradictory_baseline["candidate_a_length"] = 999
        with pytest.raises(ValidationError, match="baseline candidate length"):
            PolicyPreferenceObservation.model_validate(contradictory_baseline)

        empty_baseline = valid.model_dump()
        empty_baseline["candidate_a_length"] = 0
        empty_baseline["baseline_length"] = 0
        empty_baseline["threshold"] = 0.0
        with pytest.raises(ValidationError, match="substantive"):
            PolicyPreferenceObservation.model_validate(empty_baseline)

        unavailable = valid.model_dump()
        unavailable["candidate_b_length"] = None
        assert (
            PolicyPreferenceObservation.model_validate(unavailable).status
            is PolicyObservationStatus.ABSTAIN
        )


class TestAuditArchive:
    def test_judge_call_status_and_stamped_identity_are_consistent(self) -> None:
        call = JudgeCallRecord(
            call_id="call",
            status=JudgeCallStatus.AVAILABLE,
            request_id="request",
            judge_id="judge",
            judge_family="family",
            source=SignalSource.LLM_JUDGE,
            judgment=judgment(PairwiseVerdict.LEFT),
            cost=CostProfile(tokens=10),
        )

        unavailable = call.model_dump()
        unavailable["status"] = JudgeCallStatus.UNAVAILABLE
        with pytest.raises(ValidationError, match="cannot contain a judgment"):
            JudgeCallRecord.model_validate(unavailable)

        for field, value in (
            ("judge_id", "other"),
            ("judge_family", "other"),
            ("source", SignalSource.HUMAN),
        ):
            inconsistent = call.model_dump()
            inconsistent[field] = value
            with pytest.raises(ValidationError, match="judgment identity"):
                JudgeCallRecord.model_validate(inconsistent)

    @pytest.mark.parametrize(
        "cost",
        [
            CostProfile(tokens=-1),
            CostProfile(usd=float("nan")),
            CostProfile(latency_s=float("inf")),
        ],
    )
    def test_judge_call_rejects_invalid_persisted_cost(
        self, cost: CostProfile
    ) -> None:
        with pytest.raises(ValidationError, match="cost"):
            JudgeCallRecord(
                call_id="call",
                status=JudgeCallStatus.UNAVAILABLE,
                request_id="request",
                judge_id="judge",
                judge_family="family",
                source=SignalSource.LLM_JUDGE,
                cost=cost,
                cost_complete=True,
                error="unavailable",
            )

    def test_call_attempt_and_archive_round_trip(self) -> None:
        forward = judgment(PairwiseVerdict.LEFT)
        reverse = judgment(PairwiseVerdict.RIGHT, reverse=True)
        observation = normalize_order_swaps(
            forward,
            reverse,
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0]
        forward_call = JudgeCallRecord(
            call_id="call:f",
            status=JudgeCallStatus.AVAILABLE,
            request_id="request:f",
            judge_id="judge",
            judge_family="family",
            source=SignalSource.LLM_JUDGE,
            judgment=forward,
            cost=CostProfile(tokens=10),
            cost_complete=True,
            error=None,
        )
        reverse_call = JudgeCallRecord(
            call_id="call:r",
            status=JudgeCallStatus.AVAILABLE,
            request_id="request:r",
            judge_id="judge",
            judge_family="family",
            source=SignalSource.LLM_JUDGE,
            judgment=reverse,
            cost=CostProfile(tokens=10),
            cost_complete=True,
            error=None,
        )
        attempt = PanelAttemptRecord(
            attempt_id="attempt",
            pair_id="pair",
            case_id="case",
            candidate_a_id="a",
            candidate_b_id="b",
            call_ids=("call:f", "call:r"),
            status=PanelAttemptStatus.COMPLETE,
        )
        archive = PreferenceArchive(
            calls=(forward_call, reverse_call),
            judgments=(forward, reverse),
            observations=(observation,),
            policy_observations=(),
            attempts=(attempt,),
            notes=("audited",),
        )

        restored = PreferenceArchive.model_validate_json(archive.model_dump_json())
        assert restored == archive
        assert restored.attempts == (attempt,)

    def test_archive_rejects_contradictory_or_incomplete_records(self) -> None:
        forward = judgment(PairwiseVerdict.LEFT)
        reverse = judgment(PairwiseVerdict.RIGHT, reverse=True)
        observation = normalize_order_swaps(
            forward,
            reverse,
            packet_to_candidate={"pa": "a", "pb": "b"},
        )[0]
        calls = tuple(
            JudgeCallRecord(
                call_id=f"call:{suffix}",
                status=JudgeCallStatus.AVAILABLE,
                request_id=f"request:{suffix}",
                judge_id="judge",
                judge_family="family",
                source=SignalSource.LLM_JUDGE,
                judgment=ordered,
                cost=CostProfile(),
            )
            for suffix, ordered in (("f", forward), ("r", reverse))
        )
        attempt = PanelAttemptRecord(
            attempt_id="attempt",
            pair_id="pair",
            case_id="case",
            candidate_a_id="a",
            candidate_b_id="b",
            call_ids=("call:f", "call:r"),
            status=PanelAttemptStatus.COMPLETE,
        )

        with pytest.raises(ValidationError, match="duplicate judgment_id"):
            PreferenceArchive(
                calls=calls,
                judgments=(forward, forward),
                observations=(observation,),
                attempts=(attempt,),
            )
        with pytest.raises(ValidationError, match="duplicate observation_id"):
            PreferenceArchive(
                calls=calls,
                judgments=(forward, reverse),
                observations=(observation, observation),
                attempts=(attempt,),
            )
        with pytest.raises(ValidationError, match="archived judgments"):
            PreferenceArchive(calls=calls, attempts=(attempt,))

        mismatched_data = attempt.model_dump()
        mismatched_data["pair_id"] = "other"
        mismatched_attempt = PanelAttemptRecord.model_validate(mismatched_data)
        with pytest.raises(ValidationError, match="pair/case"):
            PreferenceArchive(
                calls=calls,
                judgments=(forward, reverse),
                observations=(observation,),
                attempts=(mismatched_attempt,),
            )

    def test_complete_attempt_requires_a_normalized_observation(self) -> None:
        call = JudgeCallRecord(
            call_id="call",
            status=JudgeCallStatus.UNAVAILABLE,
            request_id="request",
            judge_id="judge",
            judge_family="family",
            source=SignalSource.LLM_JUDGE,
            cost=CostProfile(),
            error="provider unavailable",
        )
        attempt = PanelAttemptRecord(
            attempt_id="attempt",
            pair_id="pair",
            case_id="case",
            candidate_a_id="a",
            candidate_b_id="b",
            call_ids=("call",),
            status=PanelAttemptStatus.COMPLETE,
        )
        with pytest.raises(ValidationError, match="complete panel attempt"):
            PreferenceArchive(calls=(call,), attempts=(attempt,))

    def test_unavailable_attempt_survives_without_judgment(self) -> None:
        call = JudgeCallRecord(
            call_id="call",
            status=JudgeCallStatus.UNAVAILABLE,
            request_id="request",
            judge_id="judge",
            judge_family="family",
            source=SignalSource.LLM_JUDGE,
            judgment=None,
            cost=CostProfile(),
            cost_complete=True,
            error="provider unavailable",
        )
        attempt = PanelAttemptRecord(
            attempt_id="attempt",
            pair_id="pair",
            case_id="case",
            candidate_a_id="a",
            candidate_b_id="b",
            call_ids=("call",),
            status=PanelAttemptStatus.UNAVAILABLE,
        )

        archive = PreferenceArchive(calls=(call,), attempts=(attempt,))
        assert archive.judgments == ()
        assert archive.observations == ()
        assert archive.attempts[0].status is PanelAttemptStatus.UNAVAILABLE
