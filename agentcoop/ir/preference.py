"""Auditable pairwise-preference protocol records.

These records deliberately remain separate from scientific evidence, runtime
checks, and objective utility.  They describe what a judge or an explicit
policy preferred; they never turn that preference into an observed fact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from typing import Annotated, Any, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import Direction, TaskEvidenceDossier


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    """Canonical JSON used for persistent protocol identities."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PairwiseVerdict(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TIE = "tie"
    ABSTAIN = "abstain"


class ObservationStatus(str, Enum):
    DIRECTIONAL = "directional"
    TIE = "tie"
    ABSTAIN = "abstain"
    UNRESOLVED = "unresolved"


def _normalize_verdicts(
    forward: PairwiseVerdict,
    reverse: PairwiseVerdict,
    *,
    candidate_a_id: str,
    candidate_b_id: str,
) -> tuple[ObservationStatus, Optional[str]]:
    if forward is PairwiseVerdict.LEFT and reverse is PairwiseVerdict.RIGHT:
        return ObservationStatus.DIRECTIONAL, candidate_a_id
    if forward is PairwiseVerdict.RIGHT and reverse is PairwiseVerdict.LEFT:
        return ObservationStatus.DIRECTIONAL, candidate_b_id
    if forward is PairwiseVerdict.TIE and reverse is PairwiseVerdict.TIE:
        return ObservationStatus.TIE, None
    if forward is PairwiseVerdict.ABSTAIN and reverse is PairwiseVerdict.ABSTAIN:
        return ObservationStatus.ABSTAIN, None
    return ObservationStatus.UNRESOLVED, None


class VerbosityMode(str, Enum):
    NONE = "none"
    AUTO_LOSS = "auto_loss"


class VerbosityPolicy(_FrozenRecord):
    mode: VerbosityMode = VerbosityMode.NONE
    baseline_candidate_id: Optional[NonEmptyStr] = None
    sigma: Optional[float] = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_mode(self) -> "VerbosityPolicy":
        if self.mode is VerbosityMode.NONE:
            if self.baseline_candidate_id is not None or self.sigma is not None:
                raise ValueError("NONE verbosity policy cannot name a baseline or sigma")
            return self
        if self.baseline_candidate_id is None:
            raise ValueError("AUTO_LOSS requires a baseline_candidate_id")
        if self.sigma is None or self.sigma <= 1.0:
            raise ValueError("AUTO_LOSS sigma must be finite and greater than 1")
        return self


class RubricRef(_FrozenRecord):
    rubric_id: NonEmptyStr
    version: NonEmptyStr
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return value


class MetaCriterion(_FrozenRecord):
    preference_id: NonEmptyStr
    description: NonEmptyStr
    dimension: NonEmptyStr
    direction: Direction


class MetaRubric(_FrozenRecord):
    rubric_id: NonEmptyStr
    task_id: NonEmptyStr
    version: NonEmptyStr
    criteria: tuple[MetaCriterion, ...]

    @model_validator(mode="after")
    def _validate_criteria(self) -> "MetaRubric":
        if not self.criteria:
            raise ValueError("meta rubric must contain at least one criterion")
        ids = [criterion.preference_id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate preference_id in meta rubric")
        return self

    @classmethod
    def from_dossier(
        cls, dossier: TaskEvidenceDossier, *, version: str
    ) -> "MetaRubric":
        task_id = dossier.task_id.strip()
        normalized_version = version.strip()
        if not task_id:
            raise ValueError("meta rubric task_id must be non-empty")
        if not normalized_version:
            raise ValueError("meta rubric version must be non-empty")
        criteria = tuple(
            MetaCriterion(
                preference_id=preference.preference_id,
                description=preference.description,
                dimension=preference.dimension,
                direction=preference.direction,
            )
            for preference in dossier.preferences
        )
        ids = [criterion.preference_id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate preference_id in dossier preferences")
        seed = {
            "task_id": task_id,
            "version": normalized_version,
            "criteria": [criterion.model_dump(mode="json") for criterion in criteria],
        }
        digest = _content_hash(seed)
        return cls(
            rubric_id=f"meta::{task_id}::{digest[:16]}",
            task_id=task_id,
            version=normalized_version,
            criteria=criteria,
        )

    def ref(self) -> RubricRef:
        return RubricRef(
            rubric_id=self.rubric_id,
            version=self.version,
            content_hash=_content_hash(self.model_dump(mode="json")),
        )


class RubricCriterion(_FrozenRecord):
    criterion_id: NonEmptyStr
    preference_id: NonEmptyStr
    description: NonEmptyStr
    direction: Direction


class PairRubric(_FrozenRecord):
    rubric_id: NonEmptyStr
    version: NonEmptyStr
    meta_rubric: RubricRef
    criteria: tuple[RubricCriterion, ...]

    @model_validator(mode="after")
    def _validate_criteria(self) -> "PairRubric":
        if not self.criteria:
            raise ValueError("pair rubric must contain at least one criterion")
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("duplicate criterion_id in pair rubric")
        preference_ids = [criterion.preference_id for criterion in self.criteria]
        if len(preference_ids) != len(set(preference_ids)):
            raise ValueError("duplicate preference_id in pair rubric")
        if self.version != self.meta_rubric.version:
            raise ValueError("pair rubric version must match its meta-rubric reference")
        return self

    def ref(self) -> RubricRef:
        return RubricRef(
            rubric_id=self.rubric_id,
            version=self.version,
            content_hash=_content_hash(self.model_dump(mode="json")),
        )

    def validate_against(self, meta_rubric: MetaRubric) -> "PairRubric":
        """Validate the generated operational rubric against its frozen parent."""
        if self.meta_rubric != meta_rubric.ref():
            raise ValueError("pair rubric does not reference the supplied meta rubric")
        expected = {
            criterion.preference_id: criterion.direction
            for criterion in meta_rubric.criteria
        }
        actual = {
            criterion.preference_id: criterion.direction
            for criterion in self.criteria
        }
        if set(actual) != set(expected):
            raise ValueError(
                "pair rubric must contain exactly one criterion per meta criterion"
            )
        mismatched = sorted(
            preference_id
            for preference_id, direction in expected.items()
            if actual[preference_id] is not direction
        )
        if mismatched:
            raise ValueError(
                "pair rubric direction does not match meta rubric for "
                + ", ".join(mismatched)
            )
        return self


class ScorepadEntry(_FrozenRecord):
    preference_id: NonEmptyStr
    criterion_id: NonEmptyStr
    verdict: PairwiseVerdict
    left_score: Optional[float] = Field(
        default=None, ge=0.0, le=10.0, allow_inf_nan=False
    )
    right_score: Optional[float] = Field(
        default=None, ge=0.0, le=10.0, allow_inf_nan=False
    )
    evidence_refs: tuple[NonEmptyStr, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    missing_evidence: tuple[NonEmptyStr, ...] = ()
    rationale: str = ""

    @model_validator(mode="after")
    def _validate_directional_entry(self) -> "ScorepadEntry":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence references must be unique")
        if self.verdict in (PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT):
            if not self.evidence_refs:
                raise ValueError("directional verdict requires an evidence reference")
            if not self.rationale.strip():
                raise ValueError("directional verdict requires a rationale")
            if self.left_score is None or self.right_score is None:
                raise ValueError("directional verdict requires both diagnostic scores")
        return self


class Scorepad(_FrozenRecord):
    entries: tuple[ScorepadEntry, ...]

    @model_validator(mode="after")
    def _validate_entries(self) -> "Scorepad":
        if not self.entries:
            raise ValueError("scorepad must contain at least one entry")
        ids = [entry.criterion_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate criterion_id in scorepad")
        return self


class OrderedPairJudgment(_FrozenRecord):
    judgment_id: NonEmptyStr
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    left_packet_id: NonEmptyStr
    right_packet_id: NonEmptyStr
    judge_id: NonEmptyStr
    judge_family: NonEmptyStr
    source: SignalSource
    rubric: PairRubric
    scorepad: Scorepad
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_protocol(self) -> "OrderedPairJudgment":
        if self.left_packet_id == self.right_packet_id:
            raise ValueError("ordered pair packets must be distinct")
        expected = {
            criterion.criterion_id: criterion.preference_id
            for criterion in self.rubric.criteria
        }
        actual = {entry.criterion_id: entry.preference_id for entry in self.scorepad.entries}
        if set(actual) != set(expected):
            raise ValueError("scorepad criteria must cover the pair rubric exactly")
        mismatched = [
            criterion_id
            for criterion_id, preference_id in expected.items()
            if actual[criterion_id] != preference_id
        ]
        if mismatched:
            raise ValueError(
                "scorepad preference_id does not match rubric for "
                + ", ".join(sorted(mismatched))
            )
        return self

    def final_verdict(self) -> PairwiseVerdict:
        verdicts = {entry.verdict for entry in self.scorepad.entries}
        if PairwiseVerdict.ABSTAIN in verdicts:
            return PairwiseVerdict.ABSTAIN
        has_left = PairwiseVerdict.LEFT in verdicts
        has_right = PairwiseVerdict.RIGHT in verdicts
        if has_left and has_right:
            return PairwiseVerdict.ABSTAIN
        if has_left:
            return PairwiseVerdict.LEFT
        if has_right:
            return PairwiseVerdict.RIGHT
        return PairwiseVerdict.TIE


class PairwisePreferenceObservation(_FrozenRecord):
    observation_id: NonEmptyStr
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    preference_id: NonEmptyStr
    candidate_a_id: NonEmptyStr
    candidate_b_id: NonEmptyStr
    status: ObservationStatus
    preferred_candidate_id: Optional[NonEmptyStr] = None
    judge_id: NonEmptyStr
    judge_family: NonEmptyStr
    source: SignalSource
    rubric: RubricRef
    forward: OrderedPairJudgment
    reverse: OrderedPairJudgment
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_observation(self) -> "PairwisePreferenceObservation":
        if self.candidate_a_id == self.candidate_b_id:
            raise ValueError("observation candidates must be distinct")
        if self.status is ObservationStatus.DIRECTIONAL:
            if self.preferred_candidate_id not in {
                self.candidate_a_id,
                self.candidate_b_id,
            }:
                raise ValueError(
                    "directional observation requires preferred_candidate_id from pair"
                )
        elif self.preferred_candidate_id is not None:
            raise ValueError(
                "preferred_candidate_id is only valid for a directional observation"
            )
        if self.pair_id != self.forward.pair_id or self.pair_id != self.reverse.pair_id:
            raise ValueError("observation pair_id must match both judgments")
        if self.case_id != self.forward.case_id or self.case_id != self.reverse.case_id:
            raise ValueError("observation case_id must match both judgments")
        if self.judge_id != self.forward.judge_id or self.judge_id != self.reverse.judge_id:
            raise ValueError("observation judge_id must match both judgments")
        if (
            self.judge_family != self.forward.judge_family
            or self.judge_family != self.reverse.judge_family
        ):
            raise ValueError("observation judge_family must match both judgments")
        if self.source != self.forward.source or self.source != self.reverse.source:
            raise ValueError("observation source must match both judgments")
        if self.rubric != self.forward.rubric.ref() or self.rubric != self.reverse.rubric.ref():
            raise ValueError("observation rubric must match both judgments")
        if (
            self.forward.left_packet_id != self.reverse.right_packet_id
            or self.forward.right_packet_id != self.reverse.left_packet_id
        ):
            raise ValueError("observation judgments must be strictly order-swapped")
        forward_entries = {
            entry.preference_id: entry for entry in self.forward.scorepad.entries
        }
        reverse_entries = {
            entry.preference_id: entry for entry in self.reverse.scorepad.entries
        }
        if (
            self.preference_id not in forward_entries
            or self.preference_id not in reverse_entries
        ):
            raise ValueError("observation preference_id must exist in both scorepads")
        forward_entry = forward_entries[self.preference_id]
        reverse_entry = reverse_entries[self.preference_id]
        expected_status, expected_preferred = _normalize_verdicts(
            forward_entry.verdict,
            reverse_entry.verdict,
            candidate_a_id=self.candidate_a_id,
            candidate_b_id=self.candidate_b_id,
        )
        if self.status is not expected_status or self.preferred_candidate_id != expected_preferred:
            raise ValueError("observation does not match the normalized verdict")
        expected_confidence = min(forward_entry.confidence, reverse_entry.confidence)
        if not math.isclose(
            self.confidence, expected_confidence, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("observation confidence must match its scorepad entries")
        return self


class PolicyObservationStatus(str, Enum):
    DIRECTIONAL = "directional"
    ABSTAIN = "abstain"


class PolicyPreferenceObservation(_FrozenRecord):
    observation_id: NonEmptyStr
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    preference_ids: tuple[NonEmptyStr, ...]
    candidate_a_id: NonEmptyStr
    candidate_b_id: NonEmptyStr
    status: PolicyObservationStatus
    preferred_candidate_id: Optional[NonEmptyStr] = None
    source: SignalSource = SignalSource.DETERMINISTIC
    policy: VerbosityPolicy
    baseline_candidate_id: NonEmptyStr
    candidate_a_length: Optional[int] = Field(default=None, ge=0)
    candidate_b_length: Optional[int] = Field(default=None, ge=0)
    baseline_length: Optional[int] = Field(default=None, ge=0)
    threshold: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _validate_policy_observation(self) -> "PolicyPreferenceObservation":
        if self.source is not SignalSource.DETERMINISTIC:
            raise ValueError("policy observations must use the deterministic source")
        if self.policy.mode is not VerbosityMode.AUTO_LOSS:
            raise ValueError("policy observation requires AUTO_LOSS")
        if self.policy.baseline_candidate_id != self.baseline_candidate_id:
            raise ValueError("policy and observation baseline_candidate_id must match")
        if self.candidate_a_id == self.candidate_b_id:
            raise ValueError("policy observation candidates must be distinct")
        if self.baseline_candidate_id not in {
            self.candidate_a_id,
            self.candidate_b_id,
        }:
            raise ValueError("policy comparison must include the baseline candidate")
        if not self.preference_ids or len(self.preference_ids) != len(set(self.preference_ids)):
            raise ValueError("preference_ids must be non-empty and unique")

        baseline_candidate_length = (
            self.candidate_a_length
            if self.baseline_candidate_id == self.candidate_a_id
            else self.candidate_b_length
        )
        if (
            self.baseline_length is None
            or baseline_candidate_length is None
            or self.baseline_length != baseline_candidate_length
        ):
            raise ValueError(
                "baseline_length must equal the recorded baseline candidate length"
            )
        if self.baseline_length <= 0:
            raise ValueError("verbosity policy requires a substantive baseline length")
        if self.policy.sigma is not None:
            expected = self.policy.sigma * self.baseline_length
            if self.threshold is None or not math.isclose(
                self.threshold, expected, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ValueError("threshold must equal sigma times baseline length")

        other_id = (
            self.candidate_b_id
            if self.baseline_candidate_id == self.candidate_a_id
            else self.candidate_a_id
        )
        other_length = (
            self.candidate_b_length
            if other_id == self.candidate_b_id
            else self.candidate_a_length
        )
        if self.status is PolicyObservationStatus.DIRECTIONAL:
            if self.preferred_candidate_id != self.baseline_candidate_id:
                raise ValueError("verbosity auto-loss must prefer the baseline")
            if self.threshold is None or other_length is None or other_length <= self.threshold:
                raise ValueError("directional auto-loss requires candidate to exceed threshold")
        elif self.preferred_candidate_id is not None:
            raise ValueError("policy abstention cannot name a preferred candidate")
        elif (
            self.threshold is not None
            and other_length is not None
            and other_length > self.threshold
        ):
            raise ValueError("policy cannot abstain when candidate exceeds threshold")
        return self


class JudgeCallStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class JudgeCallRecord(_FrozenRecord):
    call_id: NonEmptyStr
    status: JudgeCallStatus
    request_id: NonEmptyStr
    judge_id: NonEmptyStr
    judge_family: NonEmptyStr
    source: SignalSource
    judgment: Optional[OrderedPairJudgment] = None
    cost: Optional[CostProfile] = None
    cost_complete: bool = True
    error: Optional[str] = None

    @model_validator(mode="after")
    def _validate_call(self) -> "JudgeCallRecord":
        if self.status is JudgeCallStatus.AVAILABLE and self.judgment is None:
            raise ValueError("available judge call requires a judgment")
        if self.status is not JudgeCallStatus.AVAILABLE and self.judgment is not None:
            raise ValueError("unavailable or invalid judge call cannot contain a judgment")
        if self.judgment is not None and (
            self.judge_id != self.judgment.judge_id
            or self.judge_family != self.judgment.judge_family
            or self.source is not self.judgment.source
        ):
            raise ValueError("judge call and judgment identity must match")
        if self.cost is not None:
            for field, value in self.cost.model_dump().items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value < 0
                ):
                    raise ValueError(
                        f"judge call cost field {field} must be finite and non-negative"
                    )
            if not isinstance(self.cost.tokens, int) or isinstance(
                self.cost.tokens, bool
            ):
                raise ValueError("judge call cost tokens must be an integer")
        if self.cost_complete and self.cost is None:
            raise ValueError("complete cost accounting requires a cost profile")
        return self


class PanelAttemptStatus(str, Enum):
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class PanelAttemptRecord(_FrozenRecord):
    attempt_id: NonEmptyStr
    pair_id: NonEmptyStr
    case_id: NonEmptyStr
    candidate_a_id: NonEmptyStr
    candidate_b_id: NonEmptyStr
    call_ids: tuple[NonEmptyStr, ...]
    status: PanelAttemptStatus

    @model_validator(mode="after")
    def _validate_attempt(self) -> "PanelAttemptRecord":
        if self.candidate_a_id == self.candidate_b_id:
            raise ValueError("panel attempt candidates must be distinct")
        if not self.call_ids or len(self.call_ids) != len(set(self.call_ids)):
            raise ValueError("panel attempt call_ids must be non-empty and unique")
        return self


class PreferenceArchive(_FrozenRecord):
    calls: tuple[JudgeCallRecord, ...] = ()
    judgments: tuple[OrderedPairJudgment, ...] = ()
    observations: tuple[PairwisePreferenceObservation, ...] = ()
    policy_observations: tuple[PolicyPreferenceObservation, ...] = ()
    attempts: tuple[PanelAttemptRecord, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_archive(self) -> "PreferenceArchive":
        call_ids = [call.call_id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("duplicate call_id in preference archive")
        judgment_ids = [judgment.judgment_id for judgment in self.judgments]
        if len(judgment_ids) != len(set(judgment_ids)):
            raise ValueError("duplicate judgment_id in preference archive")
        observation_ids = [
            observation.observation_id for observation in self.observations
        ] + [
            observation.observation_id for observation in self.policy_observations
        ]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("duplicate observation_id in preference archive")
        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("duplicate attempt_id in preference archive")
        targets = [
            (
                attempt.case_id,
                tuple(sorted((attempt.candidate_a_id, attempt.candidate_b_id))),
            )
            for attempt in self.attempts
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate panel target in preference archive")

        calls_by_id = {call.call_id: call for call in self.calls}
        known_calls = set(calls_by_id)
        unresolved = sorted(
            call_id
            for attempt in self.attempts
            for call_id in attempt.call_ids
            if call_id not in known_calls
        )
        if unresolved:
            raise ValueError("panel attempt references unknown calls: " + ", ".join(unresolved))

        archived_judgments = {
            judgment.judgment_id: judgment for judgment in self.judgments
        }
        call_judgments: list[OrderedPairJudgment] = []
        for call in self.calls:
            if call.judgment is not None:
                call_judgments.append(call.judgment)
        call_judgment_ids = [judgment.judgment_id for judgment in call_judgments]
        if len(call_judgment_ids) != len(set(call_judgment_ids)):
            raise ValueError("duplicate judgment_id across judge calls")
        if set(call_judgment_ids) != set(archived_judgments):
            raise ValueError("judge-call judgments must equal archived judgments")
        for judgment in call_judgments:
            if archived_judgments[judgment.judgment_id] != judgment:
                raise ValueError("judge-call judgment differs from archived judgment")

        judgment_owner: dict[str, PanelAttemptRecord] = {}
        referenced_calls: set[str] = set()
        for attempt in self.attempts:
            for call_id in attempt.call_ids:
                if call_id in referenced_calls:
                    raise ValueError("judge call cannot belong to multiple panel attempts")
                referenced_calls.add(call_id)
                call_judgment = calls_by_id[call_id].judgment
                if call_judgment is None:
                    continue
                if (
                    call_judgment.pair_id != attempt.pair_id
                    or call_judgment.case_id != attempt.case_id
                ):
                    raise ValueError("panel attempt and judgment pair/case must match")
                judgment_owner[call_judgment.judgment_id] = attempt
        if referenced_calls != known_calls:
            raise ValueError("every judge call must belong to a panel attempt")

        observations_per_attempt = {attempt.attempt_id: 0 for attempt in self.attempts}
        for observation in self.observations:
            for embedded in (observation.forward, observation.reverse):
                archived = archived_judgments.get(embedded.judgment_id)
                if archived is None or archived != embedded:
                    raise ValueError(
                        "observation judgments must be present unchanged in the archive"
                    )
            forward_owner = judgment_owner.get(observation.forward.judgment_id)
            reverse_owner = judgment_owner.get(observation.reverse.judgment_id)
            if forward_owner is None or forward_owner != reverse_owner:
                raise ValueError(
                    "observation judgments must belong to the same panel attempt"
                )
            if (
                observation.pair_id != forward_owner.pair_id
                or observation.case_id != forward_owner.case_id
                or observation.candidate_a_id != forward_owner.candidate_a_id
                or observation.candidate_b_id != forward_owner.candidate_b_id
            ):
                raise ValueError("panel attempt and observation target must match")
            observations_per_attempt[forward_owner.attempt_id] += 1

        incomplete = sorted(
            attempt.attempt_id
            for attempt in self.attempts
            if attempt.status is PanelAttemptStatus.COMPLETE
            and observations_per_attempt[attempt.attempt_id] == 0
        )
        if incomplete:
            raise ValueError(
                "complete panel attempt requires a normalized observation: "
                + ", ".join(incomplete)
            )
        return self


def normalize_order_swaps(
    forward: OrderedPairJudgment,
    reverse: OrderedPairJudgment,
    *,
    packet_to_candidate: Mapping[str, str],
) -> tuple[PairwisePreferenceObservation, ...]:
    """Normalize two strictly reversed presentations criterion by criterion."""
    protocol_fields = (
        forward.pair_id == reverse.pair_id,
        forward.case_id == reverse.case_id,
        forward.judge_id == reverse.judge_id,
        forward.judge_family == reverse.judge_family,
        forward.source == reverse.source,
        forward.left_packet_id == reverse.right_packet_id,
        forward.right_packet_id == reverse.left_packet_id,
    )
    if not all(protocol_fields):
        raise ValueError("judgments do not form a valid order-swapped pair")
    if forward.rubric.ref() != reverse.rubric.ref():
        raise ValueError("order-swapped judgments changed the frozen rubric")

    packet_ids = (forward.left_packet_id, forward.right_packet_id)
    if any(packet_id not in packet_to_candidate for packet_id in packet_ids):
        raise ValueError("packet mapping is missing an ordered packet")
    candidate_a = packet_to_candidate[forward.left_packet_id]
    candidate_b = packet_to_candidate[forward.right_packet_id]
    if not candidate_a or not candidate_b or candidate_a == candidate_b:
        raise ValueError("packet mapping must resolve to distinct candidates")

    reverse_entries = {entry.criterion_id: entry for entry in reverse.scorepad.entries}
    observations: list[PairwisePreferenceObservation] = []
    for forward_entry in forward.scorepad.entries:
        reverse_entry = reverse_entries[forward_entry.criterion_id]
        status, preferred = _normalize_verdicts(
            forward_entry.verdict,
            reverse_entry.verdict,
            candidate_a_id=candidate_a,
            candidate_b_id=candidate_b,
        )

        identity = {
            "pair_id": forward.pair_id,
            "case_id": forward.case_id,
            "preference_id": forward_entry.preference_id,
            "candidate_a_id": candidate_a,
            "candidate_b_id": candidate_b,
            "judge_id": forward.judge_id,
            "judge_family": forward.judge_family,
            "forward": forward.judgment_id,
            "reverse": reverse.judgment_id,
        }
        observations.append(
            PairwisePreferenceObservation(
                observation_id=f"obs::{_content_hash(identity)[:20]}",
                pair_id=forward.pair_id,
                case_id=forward.case_id,
                preference_id=forward_entry.preference_id,
                candidate_a_id=candidate_a,
                candidate_b_id=candidate_b,
                status=status,
                preferred_candidate_id=preferred,
                judge_id=forward.judge_id,
                judge_family=forward.judge_family,
                source=forward.source,
                rubric=forward.rubric.ref(),
                forward=forward,
                reverse=reverse,
                confidence=min(forward_entry.confidence, reverse_entry.confidence),
            )
        )
    return tuple(observations)


__all__ = [
    "JudgeCallRecord",
    "JudgeCallStatus",
    "MetaCriterion",
    "MetaRubric",
    "ObservationStatus",
    "OrderedPairJudgment",
    "PairRubric",
    "PairwisePreferenceObservation",
    "PairwiseVerdict",
    "PanelAttemptRecord",
    "PanelAttemptStatus",
    "PolicyObservationStatus",
    "PolicyPreferenceObservation",
    "PreferenceArchive",
    "RubricCriterion",
    "RubricRef",
    "Scorepad",
    "ScorepadEntry",
    "VerbosityMode",
    "VerbosityPolicy",
    "normalize_order_swaps",
]
