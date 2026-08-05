"""Mandatory rubric-scorepad judge protocol and diverse panel behavior."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest

from agentcoop.components.base import AdapterRegistry, Invocation, InvocationResult
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import Direction, Preference, ResourceLimits, TaskEvidenceDossier
from agentcoop.ir.preference import (
    JudgeCallStatus,
    MetaRubric,
    ObservationStatus,
    OrderedPairJudgment,
    PairRubric,
    PairwiseVerdict,
    PanelAttemptStatus,
    PreferenceArchive,
    RubricCriterion,
    Scorepad,
    ScorepadEntry,
)
from agentcoop.optimize.judge import (
    ComponentJudgePayload,
    ComponentPreferenceJudge,
    JudgePanel,
    JudgeRequest,
    JudgeResponse,
)
from agentcoop.optimize.packets import CandidateView, OutputView, TraceDigest


def candidate_view(label: str, *, case_id: str = "case") -> CandidateView:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    packet_id = f"packet::{digest}"
    output_ref = f"{packet_id}:artifact:0"
    trace_ref = f"{packet_id}:trace"
    resource_ref = f"{packet_id}:resources"
    return CandidateView(
        packet_id=packet_id,
        task_id="task",
        case_id=case_id,
        goal="Choose the clearer report.",
        context="",
        outputs=(
            OutputView(
                reference_id=output_ref,
                type_name="report",
                facets={"format": "markdown"},
                snapshot={"answer": label},
                content_fingerprint=digest,
                content_available=True,
            ),
        ),
        checks=(),
        trace=TraceDigest(
            reference_id=trace_ref,
            event_counts={},
            check_count=0,
            failure_count=0,
            digest=digest,
        ),
        resources=CostProfile(),
        resources_reference_id=resource_ref,
        output_length=len(label),
        reference_ids=(output_ref, resource_ref, trace_ref),
    )


def meta_rubric(*, include_cost: bool = False) -> MetaRubric:
    preferences = [
        Preference(
            preference_id="clarity",
            description="Prefer the clearer actionable report.",
            dimension="clarity",
            direction=Direction.MAXIMIZE,
        )
    ]
    if include_cost:
        preferences.append(
            Preference(
                preference_id="cost",
                description="Prefer lower resource cost.",
                dimension="cost",
                direction=Direction.MINIMIZE,
            )
        )
    dossier = TaskEvidenceDossier(
        task_id="task",
        goal="Choose the clearer report.",
        preferences=preferences,
    )
    return MetaRubric.from_dossier(dossier, version="v1")


def operational_rubric(
    meta: MetaRubric, *, family: str, incomplete: bool = False, changed: bool = False
) -> PairRubric:
    criteria = tuple(
        RubricCriterion(
            criterion_id=f"{criterion.preference_id}-operational",
            preference_id=criterion.preference_id,
            description=(
                "Changed after observing presentation order."
                if changed
                else f"Compare {criterion.preference_id} using packet evidence."
            ),
            direction=criterion.direction,
        )
        for criterion in meta.criteria[:1] if incomplete
    ) if incomplete else tuple(
        RubricCriterion(
            criterion_id=f"{criterion.preference_id}-operational",
            preference_id=criterion.preference_id,
            description=(
                "Changed after observing presentation order."
                if changed
                else f"Compare {criterion.preference_id} using packet evidence."
            ),
            direction=criterion.direction,
        )
        for criterion in meta.criteria
    )
    return PairRubric(
        rubric_id=f"rubric::{family}",
        version=meta.version,
        meta_rubric=meta.ref(),
        criteria=criteria,
    )


def judgment_for(
    request: JudgeRequest,
    *,
    verdict: PairwiseVerdict,
    family: str,
    rubric: PairRubric,
    invalid_ref: bool = False,
) -> OrderedPairJudgment:
    entries: list[ScorepadEntry] = []
    for criterion in rubric.criteria:
        directional = verdict in {PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT}
        preferred_view = request.left if verdict is PairwiseVerdict.LEFT else request.right
        entries.append(
            ScorepadEntry(
                criterion_id=criterion.criterion_id,
                preference_id=criterion.preference_id,
                verdict=verdict,
                left_score=8.0 if directional else 7.0,
                right_score=6.0 if directional else 7.0,
                evidence_refs=(
                    ("not-a-public-reference",)
                    if invalid_ref
                    else (preferred_view.outputs[0].reference_id,)
                ) if directional else (),
                confidence=0.8,
                rationale="The preferred packet is more actionable." if directional else "",
            )
        )
    return OrderedPairJudgment(
        judgment_id="untrusted-judgment-id",
        pair_id="untrusted-pair",
        case_id="untrusted-case",
        left_packet_id="untrusted-left",
        right_packet_id="untrusted-right",
        judge_id="untrusted-judge",
        judge_family="untrusted-family",
        source=SignalSource.HUMAN,
        rubric=rubric,
        scorepad=Scorepad(entries=tuple(entries)),
        notes=(f"generated-by-{family}",),
    )


class ScriptedJudge:
    source = SignalSource.LLM_JUDGE

    def __init__(
        self,
        judge_id: str,
        family: str,
        *,
        preferred_packet_id: str | None = None,
        always_left: bool = False,
        tie: bool = False,
        invalid_ref: bool = False,
        mutate_reverse_rubric: bool = False,
        incomplete_rubric: bool = False,
        cost: CostProfile | None = None,
    ) -> None:
        self.judge_id = judge_id
        self.family = family
        self.preferred_packet_id = preferred_packet_id
        self.always_left = always_left
        self.tie = tie
        self.invalid_ref = invalid_ref
        self.mutate_reverse_rubric = mutate_reverse_rubric
        self.incomplete_rubric = incomplete_rubric
        self.cost = cost or CostProfile(tokens=1, usd=0.01)
        self.requests: list[JudgeRequest] = []

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        self.requests.append(request)
        if request.frozen_rubric is None:
            rubric = operational_rubric(
                request.meta_rubric,
                family=self.family,
                incomplete=self.incomplete_rubric,
            )
        elif self.mutate_reverse_rubric:
            rubric = operational_rubric(
                request.meta_rubric,
                family=self.family,
                changed=True,
            )
        else:
            rubric = request.frozen_rubric

        if self.tie:
            verdict = PairwiseVerdict.TIE
        elif self.always_left:
            verdict = PairwiseVerdict.LEFT
        elif request.left.packet_id == self.preferred_packet_id:
            verdict = PairwiseVerdict.LEFT
        else:
            verdict = PairwiseVerdict.RIGHT
        return JudgeResponse(
            judgment=judgment_for(
                request,
                verdict=verdict,
                family=self.family,
                rubric=rubric,
                invalid_ref=self.invalid_ref,
            ),
            cost=self.cost,
        )


class UnavailableJudge:
    source = SignalSource.LLM_JUDGE

    def __init__(self, judge_id: str, family: str) -> None:
        self.judge_id = judge_id
        self.family = family
        self.calls = 0

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        self.calls += 1
        raise RuntimeError("provider unavailable")


class MutatingJudge(ScriptedJudge):
    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        request.left.outputs[0].snapshot["answer"] = "MUTATED"
        request.left.resources.usd = 999.0
        return await super().compare(request)


class MalformedNativeJudge:
    source = SignalSource.LLM_JUDGE

    def __init__(self, payload: Any) -> None:
        self.judge_id = "malformed-native"
        self.family = "malformed-family"
        self.payload = payload

    async def compare(self, request: JudgeRequest) -> Any:
        return self.payload


async def compare_panel(
    panel: JudgePanel,
    view_a: CandidateView,
    view_b: CandidateView,
    *,
    meta: MetaRubric | None = None,
    remaining_calls: int = 10,
):
    return await panel.compare(
        pair_id="pair",
        case_id="case",
        candidate_a_id="candidate-a",
        candidate_b_id="candidate-b",
        view_a=view_a,
        view_b=view_b,
        meta_rubric=meta or meta_rubric(),
        seed=73,
        remaining_calls=remaining_calls,
    )


class TestJudgePanelProtocol:
    async def test_two_families_use_both_orders_freeze_rubric_and_stamp_identity(
        self,
    ) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        first = ScriptedJudge("judge-1", "family-1", preferred_packet_id=view_a.packet_id)
        second = ScriptedJudge("judge-2", "family-2", preferred_packet_id=view_a.packet_id)
        third = ScriptedJudge("judge-3", "family-3", preferred_packet_id=view_b.packet_id)

        result = await compare_panel(JudgePanel((first, second, third)), view_a, view_b)

        assert result.judge_calls == 4
        assert result.attempt.status is PanelAttemptStatus.COMPLETE
        assert result.cost == CostProfile(tokens=4, usd=0.04)
        assert result.cost_complete is True
        assert third.requests == []
        for judge in (first, second):
            assert len(judge.requests) == 2
            forward, reverse = judge.requests
            assert forward.seed == reverse.seed == 73
            assert forward.left == view_a and forward.right == view_b
            assert reverse.left == view_b and reverse.right == view_a
            assert reverse.frozen_rubric == next(
                judgment.rubric
                for judgment in result.judgments
                if judgment.judge_id == judge.judge_id
                and judgment.left_packet_id == view_a.packet_id
            )
        assert {observation.judge_family for observation in result.observations} == {
            "family-1",
            "family-2",
        }
        assert all(
            observation.status is ObservationStatus.DIRECTIONAL
            and observation.preferred_candidate_id == "candidate-a"
            for observation in result.observations
        )
        assert all(
            judgment.pair_id == "pair"
            and judgment.case_id == "case"
            and judgment.judge_id in {"judge-1", "judge-2"}
            and judgment.judge_family in {"family-1", "family-2"}
            and judgment.source is SignalSource.LLM_JUDGE
            for judgment in result.judgments
        )
        PreferenceArchive(
            calls=result.calls,
            judgments=result.judgments,
            observations=result.observations,
            attempts=(result.attempt,),
        )

    async def test_third_family_runs_for_position_bias_or_disagreement(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        biased = ScriptedJudge("biased", "family-1", always_left=True)
        supports_a = ScriptedJudge("a", "family-2", preferred_packet_id=view_a.packet_id)
        tiebreaker = ScriptedJudge("third", "family-3", preferred_packet_id=view_a.packet_id)

        biased_result = await compare_panel(
            JudgePanel((biased, supports_a, tiebreaker)), view_a, view_b
        )
        assert biased_result.judge_calls == 6
        assert len(tiebreaker.requests) == 2
        assert any(
            observation.judge_family == "family-1"
            and observation.status is ObservationStatus.UNRESOLVED
            for observation in biased_result.observations
        )

        supports_b = ScriptedJudge("b", "family-4", preferred_packet_id=view_b.packet_id)
        conflict_third = ScriptedJudge(
            "third-2", "family-5", preferred_packet_id=view_a.packet_id
        )
        conflict = await compare_panel(
            JudgePanel((supports_a, supports_b, conflict_third)), view_a, view_b
        )
        assert conflict.judge_calls == 6
        assert len(conflict_third.requests) == 2

    async def test_unavailable_is_audited_and_never_coerced_to_tie_or_winner(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        unavailable = UnavailableJudge("down", "family-1")
        second = ScriptedJudge("second", "family-2", preferred_packet_id=view_a.packet_id)
        third = ScriptedJudge("third", "family-3", preferred_packet_id=view_a.packet_id)

        result = await compare_panel(
            JudgePanel((unavailable, second, third)), view_a, view_b
        )

        assert result.judge_calls == 5
        assert result.calls[0].status is JudgeCallStatus.UNAVAILABLE
        assert result.calls[0].judgment is None
        assert result.cost_complete is False
        assert result.attempt.status is PanelAttemptStatus.INVALID
        assert all(observation.judge_family != "family-1" for observation in result.observations)
        assert len(third.requests) == 2

    async def test_insufficient_usable_family_diversity_is_not_complete(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        biased = ScriptedJudge("biased", "family-1", always_left=True)
        usable = ScriptedJudge("usable", "family-2", preferred_packet_id=view_a.packet_id)

        result = await compare_panel(JudgePanel((biased, usable)), view_a, view_b)

        assert result.judge_calls == 4
        assert result.attempt.status is PanelAttemptStatus.UNAVAILABLE
        assert any("diversity" in note for note in result.notes)

    async def test_family_diversity_uses_normalized_persisted_identity(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        first = ScriptedJudge(
            "first", "family", preferred_packet_id=view_a.packet_id
        )
        alias = ScriptedJudge(
            "second", " family ", preferred_packet_id=view_a.packet_id
        )

        result = await compare_panel(JudgePanel((first, alias)), view_a, view_b)

        assert result.attempt.status is PanelAttemptStatus.UNAVAILABLE
        assert alias.requests == []
        assert {call.judge_family for call in result.calls} == {"family"}

    async def test_malformed_refs_and_changed_reverse_rubric_are_invalid(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        bad_ref = ScriptedJudge(
            "bad-ref",
            "family-1",
            preferred_packet_id=view_a.packet_id,
            invalid_ref=True,
        )
        changed = ScriptedJudge(
            "changed",
            "family-2",
            preferred_packet_id=view_a.packet_id,
            mutate_reverse_rubric=True,
        )
        fallback = ScriptedJudge(
            "fallback", "family-3", preferred_packet_id=view_a.packet_id
        )

        result = await compare_panel(
            JudgePanel((bad_ref, changed, fallback)), view_a, view_b
        )

        assert [call.status for call in result.calls[:3]] == [
            JudgeCallStatus.INVALID,
            JudgeCallStatus.AVAILABLE,
            JudgeCallStatus.INVALID,
        ]
        assert len(fallback.requests) == 2
        assert all(
            judgment.judge_id not in {"bad-ref", "changed"}
            or judgment.judge_id == "changed" and judgment.left_packet_id == view_a.packet_id
            for judgment in result.judgments
        )

    async def test_incomplete_pair_rubric_is_invalid_against_meta_rubric(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        incomplete = ScriptedJudge(
            "incomplete",
            "family-1",
            preferred_packet_id=view_a.packet_id,
            incomplete_rubric=True,
        )

        result = await compare_panel(
            JudgePanel((incomplete,)),
            view_a,
            view_b,
            meta=meta_rubric(include_cost=True),
        )

        assert result.calls[0].status is JudgeCallStatus.INVALID
        assert result.judgments == ()
        assert result.observations == ()

    async def test_call_budget_is_never_overshot(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        first = ScriptedJudge("first", "family-1", preferred_packet_id=view_a.packet_id)
        second = ScriptedJudge("second", "family-2", preferred_packet_id=view_a.packet_id)

        result = await compare_panel(
            JudgePanel((first, second)), view_a, view_b, remaining_calls=3
        )

        assert result.judge_calls == 2
        assert len(first.requests) == 2
        assert len(second.requests) == 0
        assert result.attempt.status is PanelAttemptStatus.UNAVAILABLE

    async def test_third_family_is_not_started_without_two_reserved_calls(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        biased = ScriptedJudge("biased", "family-1", always_left=True)
        second = ScriptedJudge(
            "second", "family-2", preferred_packet_id=view_a.packet_id
        )
        third = ScriptedJudge(
            "third", "family-3", preferred_packet_id=view_a.packet_id
        )

        result = await compare_panel(
            JudgePanel((biased, second, third)),
            view_a,
            view_b,
            remaining_calls=5,
        )

        assert result.judge_calls == 4
        assert third.requests == []

    @pytest.mark.parametrize("payload", [None, {"judgment": "not-a-model"}, 7])
    async def test_malformed_native_response_is_audited_invalid(
        self, payload: Any
    ) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")

        result = await compare_panel(
            JudgePanel((MalformedNativeJudge(payload),)), view_a, view_b
        )

        assert result.judge_calls == 1
        assert result.calls[0].status is JudgeCallStatus.INVALID
        assert result.calls[0].judgment is None
        assert result.attempt.status is PanelAttemptStatus.INVALID

    async def test_identical_packets_can_be_compared_and_normalize_to_tie(self) -> None:
        identical = candidate_view("same-content")
        first = ScriptedJudge("first", "family-1", tie=True)
        second = ScriptedJudge("second", "family-2", tie=True)

        result = await compare_panel(
            JudgePanel((first, second)), identical, identical
        )

        assert result.attempt.status is PanelAttemptStatus.COMPLETE
        assert all(
            observation.status is ObservationStatus.TIE
            for observation in result.observations
        )
        assert all(
            judgment.left_packet_id != judgment.right_packet_id
            for judgment in result.judgments
        )
        assert result.judgments[0].left_packet_id == result.judgments[1].right_packet_id

    async def test_native_judge_cannot_mutate_trusted_candidate_views(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        first = MutatingJudge(
            "mutator", "family-1", preferred_packet_id=view_a.packet_id
        )
        second = ScriptedJudge(
            "second", "family-2", preferred_packet_id=view_a.packet_id
        )

        await compare_panel(JudgePanel((first, second)), view_a, view_b)

        assert view_a.outputs[0].snapshot == {"answer": "A"}
        assert view_b.outputs[0].snapshot == {"answer": "B"}
        assert view_a.resources.usd == 0.0
        assert view_b.resources.usd == 0.0

    async def test_cost_aggregation_overflow_marks_accounting_incomplete(self) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        huge = CostProfile(usd=1e308)
        first = ScriptedJudge(
            "first", "family-1", preferred_packet_id=view_a.packet_id, cost=huge
        )
        second = ScriptedJudge(
            "second", "family-2", preferred_packet_id=view_a.packet_id, cost=huge
        )

        result = await compare_panel(JudgePanel((first, second)), view_a, view_b)

        assert result.cost_complete is False
        assert result.attempt.status is PanelAttemptStatus.INVALID
        assert all(
            math.isfinite(float(value))
            for value in result.cost.model_dump().values()
        )

    @pytest.mark.parametrize(
        "cost",
        [
            CostProfile(tokens=-1),
            CostProfile(latency_s=float("nan")),
            CostProfile(usd=float("inf")),
            CostProfile(cpu_seconds=-1.0),
            CostProfile(gpu_seconds=float("nan")),
            CostProfile(peak_memory_gb=-1.0),
        ],
    )
    async def test_invalid_authoritative_cost_invalidates_accounting(
        self, cost: CostProfile
    ) -> None:
        view_a, view_b = candidate_view("A"), candidate_view("B")
        judge = ScriptedJudge(
            "bad-cost",
            "family-1",
            preferred_packet_id=view_a.packet_id,
            cost=cost,
        )

        result = await compare_panel(JudgePanel((judge,)), view_a, view_b)

        assert result.judge_calls == 1
        assert result.calls[0].status is JudgeCallStatus.INVALID
        assert result.calls[0].cost_complete is False
        assert result.cost_complete is False
        assert result.attempt.status is PanelAttemptStatus.INVALID


def bridge_request() -> JudgeRequest:
    return JudgeRequest(
        request_id="request",
        pair_id="pair",
        case_id="case",
        seed=91,
        meta_rubric=meta_rubric(),
        left=candidate_view("A"),
        right=candidate_view("B"),
    )


class JudgeAdapter:
    name = "judge-component"

    def __init__(self, mode: str = "mapping") -> None:
        self.mode = mode
        self.invocations: list[Invocation] = []

    async def invoke(self, invocation: Invocation) -> InvocationResult:
        self.invocations.append(invocation)
        if self.mode == "exception":
            raise RuntimeError("adapter exception")
        request_artifact = invocation.inputs["custom.request"]
        request = JudgeRequest.model_validate(request_artifact.payload)
        rubric = operational_rubric(request.meta_rubric, family="component-family")
        judgment = judgment_for(
            request,
            verdict=PairwiseVerdict.LEFT,
            family="component-family",
            rubric=rubric,
        )
        payload: Any = ComponentJudgePayload(
            judgment=judgment,
            reported_cost=CostProfile(tokens=99_999, usd=99_999.0),
        ).model_dump(mode="json")
        output_type = "custom.response"
        path = None
        if self.mode == "json":
            payload = ComponentJudgePayload(
                judgment=judgment,
                reported_cost=CostProfile(tokens=99_999),
            ).model_dump_json()
        elif self.mode == "missing":
            return InvocationResult(ok=True, outputs={}, cost=CostProfile(tokens=7))
        elif self.mode == "failed":
            return InvocationResult(ok=False, outputs={}, cost=CostProfile(tokens=7))
        elif self.mode == "wrong-type":
            output_type = "wrong.response"
        elif self.mode == "wrong-key":
            return InvocationResult(
                ok=True,
                outputs={
                    "wrong.response": Artifact(
                        artifact_id="wrong",
                        type_name="wrong.response",
                        payload=payload,
                    )
                },
                cost=CostProfile(tokens=7),
            )
        elif self.mode == "malformed":
            payload = "{not-json"
        elif self.mode == "path":
            payload = None
            path = "/never/dereference/response.json"
        elif self.mode == "payload-path":
            path = "/never/dereference/even-with-payload.json"
        return InvocationResult(
            ok=True,
            outputs={
                "custom.response": Artifact(
                    artifact_id="response",
                    type_name=output_type,
                    payload=payload,
                    path=path,
                )
            },
            cost=CostProfile(tokens=7, usd=0.25),
        )


def component_judge(adapter: JudgeAdapter) -> ComponentPreferenceJudge:
    return ComponentPreferenceJudge(
        AdapterRegistry((adapter,)),
        "judge-component",
        judge_id="component-judge",
        family="component-family",
        request_type="custom.request",
        response_type="custom.response",
        limits=ResourceLimits(max_tokens=100),
    )


class TestComponentPreferenceJudge:
    @pytest.mark.parametrize("mode", ["mapping", "json"])
    async def test_plan_propagates_seed_and_authoritative_adapter_cost(
        self, mode: str
    ) -> None:
        adapter = JudgeAdapter(mode)
        judge = component_judge(adapter)
        request = bridge_request()

        invocation = judge.plan(request)
        response = await judge.compare(request)

        assert invocation.component == "judge-component"
        assert invocation.seed == 91
        assert invocation.limits == ResourceLimits(max_tokens=100)
        assert set(invocation.inputs) == {"custom.request"}
        assert invocation.inputs["custom.request"].type_name == "custom.request"
        assert invocation.inputs["custom.request"].path is None
        assert invocation.config == {}
        assert "candidate-a" not in invocation.model_dump_json()
        assert response.cost == CostProfile(tokens=7, usd=0.25)
        assert response.cost != CostProfile(tokens=99_999, usd=99_999.0)
        assert adapter.invocations[0].seed == 91

    @pytest.mark.parametrize(
        "mode,status",
        [
            ("exception", JudgeCallStatus.UNAVAILABLE),
            ("missing", JudgeCallStatus.UNAVAILABLE),
            ("failed", JudgeCallStatus.UNAVAILABLE),
            ("wrong-type", JudgeCallStatus.INVALID),
            ("wrong-key", JudgeCallStatus.INVALID),
            ("malformed", JudgeCallStatus.INVALID),
            ("path", JudgeCallStatus.INVALID),
            ("payload-path", JudgeCallStatus.INVALID),
        ],
    )
    async def test_transport_failures_are_audited_without_default_winner(
        self, mode: str, status: JudgeCallStatus
    ) -> None:
        adapter = JudgeAdapter(mode)
        judge = component_judge(adapter)
        view_a, view_b = candidate_view("A"), candidate_view("B")

        result = await compare_panel(JudgePanel((judge,)), view_a, view_b)

        assert result.calls[0].status is status
        assert result.calls[0].judgment is None
        assert result.judgments == ()
        assert result.observations == ()


def test_request_is_strict_and_contains_no_candidate_identity_fields() -> None:
    request = bridge_request()
    payload = request.model_dump(mode="json")

    assert "candidate_a_id" not in payload
    assert "candidate_b_id" not in payload
    assert payload["left"]["packet_id"] == request.left.packet_id
    assert math.isfinite(request.left.resources.usd)
