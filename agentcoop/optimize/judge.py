"""Enforced rubric-scorepad protocol and diverse order-swapped judge panels."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Optional, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from agentcoop.components.base import AdapterRegistry, Invocation
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import ResourceLimits
from agentcoop.ir.preference import (
    JudgeCallRecord,
    JudgeCallStatus,
    MetaRubric,
    ObservationStatus,
    OrderedPairJudgment,
    PairRubric,
    PairwisePreferenceObservation,
    PanelAttemptRecord,
    PanelAttemptStatus,
    normalize_order_swaps,
)
from agentcoop.optimize.packets import CandidateView, validate_evidence_refs


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}::{digest[:24]}"


class _ProtocolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JudgeRequest(_ProtocolRecord):
    request_id: NonEmptyStr
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    seed: int
    meta_rubric: MetaRubric
    left: CandidateView
    right: CandidateView
    frozen_rubric: Optional[PairRubric] = None

    @model_validator(mode="after")
    def _validate_request(self) -> "JudgeRequest":
        if self.left.task_id != self.meta_rubric.task_id:
            raise ValueError("left packet task does not match the meta rubric")
        if self.right.task_id != self.meta_rubric.task_id:
            raise ValueError("right packet task does not match the meta rubric")
        if self.left.case_id != self.case_id or self.right.case_id != self.case_id:
            raise ValueError("judge packet case_id must match the request")
        if self.frozen_rubric is not None:
            self.frozen_rubric.validate_against(self.meta_rubric)
        return self


class JudgeResponse(_ProtocolRecord):
    judgment: OrderedPairJudgment
    cost: CostProfile


class ComponentJudgePayload(_ProtocolRecord):
    judgment: OrderedPairJudgment
    reported_cost: Optional[CostProfile] = None


class PreferenceJudge(Protocol):
    judge_id: str
    family: str
    source: SignalSource

    async def compare(self, request: JudgeRequest) -> JudgeResponse: ...


def _judge_id(judge: PreferenceJudge) -> str:
    return judge.judge_id.strip()


def _judge_family(judge: PreferenceJudge) -> str:
    return judge.family.strip()


class PanelResult(_ProtocolRecord):
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    calls: tuple[JudgeCallRecord, ...]
    judgments: tuple[OrderedPairJudgment, ...]
    observations: tuple[PairwisePreferenceObservation, ...]
    attempt: PanelAttemptRecord
    judge_calls: int = Field(ge=0)
    cost: CostProfile
    cost_complete: bool
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_counts(self) -> "PanelResult":
        if self.judge_calls != len(self.calls):
            raise ValueError("judge_calls must equal the number of call records")
        if self.attempt.call_ids != tuple(call.call_id for call in self.calls):
            raise ValueError("panel attempt must reference every call in order")
        return self


class _JudgeFailure(RuntimeError):
    def __init__(
        self,
        status: JudgeCallStatus,
        message: str,
        *,
        cost: Optional[CostProfile],
        cost_complete: bool,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.cost = cost
        self.cost_complete = cost_complete


def _cost_problem(cost: CostProfile) -> Optional[str]:
    values = cost.model_dump()
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"judge cost field {field} is not numeric"
        if not math.isfinite(float(value)) or value < 0:
            return f"judge cost field {field} must be finite and non-negative"
    if not isinstance(cost.tokens, int) or isinstance(cost.tokens, bool):
        return "judge cost tokens must be an integer"
    return None


class ComponentPreferenceJudge:
    """Bridge the preference protocol through the existing adapter boundary."""

    def __init__(
        self,
        registry: AdapterRegistry,
        component: str,
        *,
        judge_id: str,
        family: str,
        source: SignalSource = SignalSource.LLM_JUDGE,
        request_type: str = "agentcoop.preference_judge_request",
        response_type: str = "agentcoop.preference_judge_response",
        limits: Optional[ResourceLimits] = None,
    ) -> None:
        values = (component, judge_id, family, request_type, response_type)
        if any(not value.strip() for value in values):
            raise ValueError("component judge identifiers and types must be non-empty")
        self.registry = registry
        self.component = component
        self.judge_id = judge_id
        self.family = family
        self.source = source
        self.request_type = request_type
        self.response_type = response_type
        self.limits = limits

    def plan(self, request: JudgeRequest) -> Invocation:
        artifact = Artifact(
            artifact_id=_stable_id("judge-request", request.request_id),
            type_name=self.request_type,
            payload=request.model_dump(mode="json"),
        ).finalize()
        return Invocation(
            component=self.component,
            inputs={self.request_type: artifact},
            config={},
            limits=self.limits,
            seed=request.seed,
        )

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        invocation = self.plan(request)
        try:
            result = await self.registry.get(self.component).invoke(invocation)
        except Exception as exc:
            raise _JudgeFailure(
                JudgeCallStatus.UNAVAILABLE,
                f"adapter exception: {exc}",
                cost=None,
                cost_complete=False,
            ) from exc

        cost_problem = _cost_problem(result.cost)
        if cost_problem is not None:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                cost_problem,
                cost=None,
                cost_complete=False,
            )
        if not result.ok:
            raise _JudgeFailure(
                JudgeCallStatus.UNAVAILABLE,
                "judge component reported unavailable",
                cost=result.cost,
                cost_complete=True,
            )
        if self.response_type not in result.outputs:
            status = (
                JudgeCallStatus.UNAVAILABLE
                if not result.outputs
                else JudgeCallStatus.INVALID
            )
            raise _JudgeFailure(
                status,
                (
                    "judge component omitted its response artifact"
                    if not result.outputs
                    else "judge component returned the wrong response artifact key"
                ),
                cost=result.cost,
                cost_complete=True,
            )
        if len(result.outputs) != 1:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge component returned unexpected extra artifacts",
                cost=result.cost,
                cost_complete=True,
            )
        artifact = result.outputs[self.response_type]
        if artifact.type_name != self.response_type:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge response artifact has the wrong type",
                cost=result.cost,
                cost_complete=True,
            )
        if artifact.path is not None or artifact.payload is None:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge response must be an in-memory payload",
                cost=result.cost,
                cost_complete=True,
            )

        payload: Any
        if isinstance(artifact.payload, Mapping):
            payload = dict(artifact.payload)
        elif isinstance(artifact.payload, str):
            try:
                payload = json.loads(artifact.payload)
            except (TypeError, ValueError) as exc:
                raise _JudgeFailure(
                    JudgeCallStatus.INVALID,
                    "judge response is malformed JSON",
                    cost=result.cost,
                    cost_complete=True,
                ) from exc
        else:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge response must be a mapping or JSON string",
                cost=result.cost,
                cost_complete=True,
            )
        if not isinstance(payload, Mapping):
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge response JSON must contain an object",
                cost=result.cost,
                cost_complete=True,
            )
        try:
            parsed = ComponentJudgePayload.model_validate(payload)
        except Exception as exc:
            raise _JudgeFailure(
                JudgeCallStatus.INVALID,
                "judge response violates the response schema",
                cost=result.cost,
                cost_complete=True,
            ) from exc
        return JudgeResponse(judgment=parsed.judgment, cost=result.cost)


def _request_id(
    *,
    pair_id: str,
    case_id: str,
    judge: PreferenceJudge,
    order: str,
    seed: int,
    left: CandidateView,
    right: CandidateView,
) -> str:
    return _stable_id(
        "judge-request",
        {
            "pair_id": pair_id,
            "case_id": case_id,
            "judge_id": _judge_id(judge),
            "family": _judge_family(judge),
            "order": order,
            "seed": seed,
            "left": left.packet_id,
            "right": right.packet_id,
        },
    )


def _stamp_judgment(
    response: JudgeResponse,
    *,
    request: JudgeRequest,
    judge: PreferenceJudge,
    left_packet_id: str,
    right_packet_id: str,
) -> OrderedPairJudgment:
    rubric = response.judgment.rubric
    rubric.validate_against(request.meta_rubric)
    if request.frozen_rubric is not None and rubric != request.frozen_rubric:
        raise ValueError("reverse judge call changed the frozen rubric")

    data = response.judgment.model_dump()
    data.update(
        {
            "judgment_id": _stable_id(
                "judgment",
                {
                    "request_id": request.request_id,
                    "judge_id": _judge_id(judge),
                    "rubric": rubric.ref().content_hash,
                    "scorepad": response.judgment.scorepad.model_dump(mode="json"),
                },
            ),
            "pair_id": request.pair_id,
            "case_id": request.case_id,
            "left_packet_id": left_packet_id,
            "right_packet_id": right_packet_id,
            "judge_id": _judge_id(judge),
            "judge_family": _judge_family(judge),
            "source": judge.source,
        }
    )
    stamped = OrderedPairJudgment.model_validate(data)
    unresolved = validate_evidence_refs(
        tuple(
            ref
            for entry in stamped.scorepad.entries
            for ref in entry.evidence_refs
        ),
        (request.left, request.right),
    )
    if unresolved:
        raise ValueError(
            "judge scorepad contains unresolved evidence references: "
            + ", ".join(unresolved)
        )
    return stamped


async def _call_judge(
    judge: PreferenceJudge,
    request: JudgeRequest,
    *,
    left_packet_id: str,
    right_packet_id: str,
) -> JudgeCallRecord:
    call_id = _stable_id(
        "judge-call", {"request_id": request.request_id, "judge_id": _judge_id(judge)}
    )
    try:
        transport_request = JudgeRequest.model_validate_json(request.model_dump_json())
        response = await judge.compare(transport_request)
    except _JudgeFailure as exc:
        return JudgeCallRecord(
            call_id=call_id,
            status=exc.status,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=exc.cost,
            cost_complete=exc.cost_complete,
            error=str(exc),
        )
    except Exception as exc:
        return JudgeCallRecord(
            call_id=call_id,
            status=JudgeCallStatus.UNAVAILABLE,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=None,
            cost_complete=False,
            error=f"judge exception: {exc}",
        )

    if not isinstance(response, JudgeResponse):
        return JudgeCallRecord(
            call_id=call_id,
            status=JudgeCallStatus.INVALID,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=None,
            cost_complete=False,
            error="native judge returned a malformed response type",
        )
    try:
        response = JudgeResponse.model_validate(response.model_dump())
    except Exception as exc:
        return JudgeCallRecord(
            call_id=call_id,
            status=JudgeCallStatus.INVALID,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=None,
            cost_complete=False,
            error=f"native judge response failed validation: {exc}",
        )

    problem = _cost_problem(response.cost)
    if problem is not None:
        return JudgeCallRecord(
            call_id=call_id,
            status=JudgeCallStatus.INVALID,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=None,
            cost_complete=False,
            error=problem,
        )
    try:
        stamped = _stamp_judgment(
            response,
            request=request,
            judge=judge,
            left_packet_id=left_packet_id,
            right_packet_id=right_packet_id,
        )
    except Exception as exc:
        return JudgeCallRecord(
            call_id=call_id,
            status=JudgeCallStatus.INVALID,
            request_id=request.request_id,
            judge_id=_judge_id(judge),
            judge_family=_judge_family(judge),
            source=judge.source,
            cost=response.cost,
            cost_complete=True,
            error=f"judge protocol violation: {exc}",
        )
    return JudgeCallRecord(
        call_id=call_id,
        status=JudgeCallStatus.AVAILABLE,
        request_id=request.request_id,
        judge_id=_judge_id(judge),
        judge_family=_judge_family(judge),
        source=judge.source,
        judgment=stamped,
        cost=response.cost,
        cost_complete=True,
    )


def _usable(observations: Sequence[PairwisePreferenceObservation]) -> bool:
    return bool(observations) and all(
        observation.status in {ObservationStatus.DIRECTIONAL, ObservationStatus.TIE}
        for observation in observations
    )


def _conflict(
    left: Sequence[PairwisePreferenceObservation],
    right: Sequence[PairwisePreferenceObservation],
) -> bool:
    left_by_preference = {
        observation.preference_id: (
            observation.status,
            observation.preferred_candidate_id,
        )
        for observation in left
    }
    right_by_preference = {
        observation.preference_id: (
            observation.status,
            observation.preferred_candidate_id,
        )
        for observation in right
    }
    return left_by_preference != right_by_preference


class JudgePanel:
    def __init__(self, judges: Sequence[PreferenceJudge]) -> None:
        self.judges = tuple(judges)
        for judge in self.judges:
            if not _judge_id(judge) or not _judge_family(judge):
                raise ValueError("panel judges require non-empty ID and family")

    async def compare(
        self,
        *,
        pair_id: str,
        case_id: str,
        candidate_a_id: str,
        candidate_b_id: str,
        view_a: CandidateView,
        view_b: CandidateView,
        meta_rubric: MetaRubric,
        seed: int,
        remaining_calls: int,
    ) -> PanelResult:
        if candidate_a_id == candidate_b_id:
            raise ValueError("panel candidates must be distinct")
        if remaining_calls < 2:
            raise ValueError(
                "panel comparison requires two calls for an order-swapped family"
            )

        configured: list[PreferenceJudge] = []
        seen_families: set[str] = set()
        for judge in self.judges:
            family = _judge_family(judge)
            if family in seen_families:
                continue
            configured.append(judge)
            seen_families.add(family)
            if len(configured) == 3:
                break

        calls: list[JudgeCallRecord] = []
        judgments: list[OrderedPairJudgment] = []
        observations: list[PairwisePreferenceObservation] = []
        family_observations: dict[str, tuple[PairwisePreferenceObservation, ...]] = {}
        calls_left = remaining_calls
        presentation_a_id = view_a.packet_id
        presentation_b_id = view_b.packet_id
        if presentation_a_id == presentation_b_id:
            presentation_a_id = _stable_id(
                "presentation",
                {"pair_id": pair_id, "case_id": case_id, "side": "a"},
            )
            presentation_b_id = _stable_id(
                "presentation",
                {"pair_id": pair_id, "case_id": case_id, "side": "b"},
            )

        async def run_family(
            judge: PreferenceJudge,
        ) -> tuple[PairwisePreferenceObservation, ...]:
            nonlocal calls_left
            if calls_left < 2:
                return ()
            forward_request = JudgeRequest(
                request_id=_request_id(
                    pair_id=pair_id,
                    case_id=case_id,
                    judge=judge,
                    order="forward",
                    seed=seed,
                    left=view_a,
                    right=view_b,
                ),
                pair_id=pair_id,
                case_id=case_id,
                seed=seed,
                meta_rubric=meta_rubric,
                left=view_a,
                right=view_b,
            )
            forward_call = await _call_judge(
                judge,
                forward_request,
                left_packet_id=presentation_a_id,
                right_packet_id=presentation_b_id,
            )
            calls.append(forward_call)
            calls_left -= 1
            if forward_call.judgment is None:
                return ()
            judgments.append(forward_call.judgment)
            if calls_left <= 0:
                return ()

            reverse_request = JudgeRequest(
                request_id=_request_id(
                    pair_id=pair_id,
                    case_id=case_id,
                    judge=judge,
                    order="reverse",
                    seed=seed,
                    left=view_b,
                    right=view_a,
                ),
                pair_id=pair_id,
                case_id=case_id,
                seed=seed,
                meta_rubric=meta_rubric,
                left=view_b,
                right=view_a,
                frozen_rubric=forward_call.judgment.rubric,
            )
            reverse_call = await _call_judge(
                judge,
                reverse_request,
                left_packet_id=presentation_b_id,
                right_packet_id=presentation_a_id,
            )
            calls.append(reverse_call)
            calls_left -= 1
            if reverse_call.judgment is None:
                return ()
            judgments.append(reverse_call.judgment)
            normalized = normalize_order_swaps(
                forward_call.judgment,
                reverse_call.judgment,
                packet_to_candidate={
                    presentation_a_id: candidate_a_id,
                    presentation_b_id: candidate_b_id,
                },
            )
            observations.extend(normalized)
            return normalized

        primary = configured[:2]
        for judge in primary:
            family_observations[_judge_family(judge)] = await run_family(judge)

        need_third = len(primary) < 2 or any(
            not _usable(family_observations.get(_judge_family(judge), ()))
            for judge in primary
        )
        if len(primary) == 2 and not need_third:
            need_third = _conflict(
                family_observations[_judge_family(primary[0])],
                family_observations[_judge_family(primary[1])],
            )
        if need_third and len(configured) >= 3 and calls_left >= 2:
            third = configured[2]
            family_observations[_judge_family(third)] = await run_family(third)

        usable_families: dict[str, set[str]] = {
            criterion.preference_id: set() for criterion in meta_rubric.criteria
        }
        for family, normalized in family_observations.items():
            for observation in normalized:
                if observation.status in {
                    ObservationStatus.DIRECTIONAL,
                    ObservationStatus.TIE,
                }:
                    usable_families[observation.preference_id].add(family)
        diversity_satisfied = bool(usable_families) and all(
            len(families) >= 2 for families in usable_families.values()
        )

        cost = CostProfile()
        aggregation_complete = True
        for call in calls:
            if call.cost is not None:
                aggregated = cost + call.cost
                if _cost_problem(aggregated) is not None:
                    aggregation_complete = False
                else:
                    cost = aggregated
        cost_complete = (
            aggregation_complete and all(call.cost_complete for call in calls)
        )
        notes: list[str] = []
        if not diversity_satisfied:
            notes.append("insufficient usable judge-family diversity")
        if not cost_complete:
            notes.append("judge resource accounting incomplete")

        if not cost_complete:
            status = PanelAttemptStatus.INVALID
        elif diversity_satisfied:
            status = PanelAttemptStatus.COMPLETE
        elif any(call.status is JudgeCallStatus.INVALID for call in calls):
            status = PanelAttemptStatus.INVALID
        else:
            status = PanelAttemptStatus.UNAVAILABLE
        attempt = PanelAttemptRecord(
            attempt_id=_stable_id(
                "panel-attempt",
                {
                    "pair_id": pair_id,
                    "case_id": case_id,
                    "candidate_a_id": candidate_a_id,
                    "candidate_b_id": candidate_b_id,
                    "call_ids": [call.call_id for call in calls],
                },
            ),
            pair_id=pair_id,
            case_id=case_id,
            candidate_a_id=candidate_a_id,
            candidate_b_id=candidate_b_id,
            call_ids=tuple(call.call_id for call in calls),
            status=status,
        )
        return PanelResult(
            pair_id=pair_id,
            case_id=case_id,
            calls=tuple(calls),
            judgments=tuple(judgments),
            observations=tuple(observations),
            attempt=attempt,
            judge_calls=len(calls),
            cost=cost,
            cost_complete=cost_complete,
            notes=tuple(notes),
        )


__all__ = [
    "ComponentJudgePayload",
    "ComponentPreferenceJudge",
    "JudgePanel",
    "JudgeRequest",
    "JudgeResponse",
    "PanelResult",
    "PreferenceJudge",
]
