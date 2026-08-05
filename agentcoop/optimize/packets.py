"""Anonymous, allowlisted views of executions for preference judges.

Packets expose task-relevant outcome content and mechanically resolvable
evidence references.  They deliberately omit workflow, component, provider,
artifact, run, and check identities so a judge cannot reward an incumbent or
a familiar producer instead of the observed result.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentcoop.execute.engine import ExecutionResult
from agentcoop.execute.trace import EventKind
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import CheckLevel, CheckStatus, SignalSource
from agentcoop.ir.dossier import TaskEvidenceDossier


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_SUBJECTIVE_SOURCES = {SignalSource.LLM_JUDGE, SignalSource.HUMAN}
_MAX_PAYLOAD_DEPTH = 64


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


class _PacketRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OutputView(_PacketRecord):
    reference_id: NonEmptyStr
    type_name: NonEmptyStr
    facets: dict[str, str] = Field(default_factory=dict)
    snapshot: Any = None
    content_fingerprint: NonEmptyStr
    content_available: bool
    content_unavailable_reason: Optional[NonEmptyStr] = None
    lineage_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _validate_availability(self) -> "OutputView":
        if self.content_available:
            if self.snapshot is None:
                raise ValueError("available output requires a snapshot")
            if self.content_unavailable_reason is not None:
                raise ValueError("available output cannot have an unavailable reason")
        elif self.content_unavailable_reason is None:
            raise ValueError("unavailable output requires a reason")
        if len(self.lineage_refs) != len(set(self.lineage_refs)):
            raise ValueError("lineage references must be unique")
        return self


class PacketCheck(_PacketRecord):
    reference_id: NonEmptyStr
    level: CheckLevel
    status: CheckStatus
    source: SignalSource
    score: Optional[float] = Field(default=None, allow_inf_nan=False)
    blocking: bool
    occurrences: int = Field(ge=1)


class TraceDigest(_PacketRecord):
    reference_id: NonEmptyStr
    event_counts: dict[str, int] = Field(default_factory=dict)
    check_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    digest: NonEmptyStr

    @field_validator("event_counts")
    @classmethod
    def _validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key or count < 0 for key, count in value.items()):
            raise ValueError("trace event counts must be non-negative")
        return value


class CandidateView(_PacketRecord):
    packet_id: NonEmptyStr
    task_id: NonEmptyStr
    case_id: NonEmptyStr
    goal: NonEmptyStr
    context: str = ""
    outputs: tuple[OutputView, ...]
    checks: tuple[PacketCheck, ...]
    trace: TraceDigest
    resources: CostProfile
    resources_reference_id: NonEmptyStr
    output_length: Optional[int] = Field(default=None, ge=0)
    reference_ids: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _validate_references(self) -> "CandidateView":
        if not self.outputs:
            raise ValueError("candidate view requires at least one output")
        if len(self.reference_ids) != len(set(self.reference_ids)):
            raise ValueError("candidate reference IDs must be unique")
        expected = {
            self.trace.reference_id,
            self.resources_reference_id,
            *(output.reference_id for output in self.outputs),
            *(ref for output in self.outputs for ref in output.lineage_refs),
            *(check.reference_id for check in self.checks),
        }
        if expected != set(self.reference_ids):
            raise ValueError(
                "candidate reference IDs must exactly match derived public references"
            )
        return self


class _UnsafePayload(ValueError):
    pass


def _normalize_payload(value: Any, *, seen: set[int], depth: int = 0) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise _UnsafePayload("payload nesting exceeds limit")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _UnsafePayload("invalid unicode") from exc
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _UnsafePayload("non-finite")
        return value
    if type(value) is bytes:
        return {
            "__agentcoop_type__": "bytes",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    if type(value) is dict:
        identity = id(value)
        if identity in seen:
            raise _UnsafePayload("cyclic payload")
        if any(type(key) is not str for key in value):
            raise _UnsafePayload("non-string mapping key")
        for key in value:
            try:
                key.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise _UnsafePayload("invalid unicode") from exc
        seen.add(identity)
        try:
            return {
                key: _normalize_payload(value[key], seen=seen, depth=depth + 1)
                for key in sorted(value)
            }
        finally:
            seen.remove(identity)
    if type(value) in (list, tuple):
        identity = id(value)
        if identity in seen:
            raise _UnsafePayload("cyclic payload")
        seen.add(identity)
        try:
            return [
                _normalize_payload(item, seen=seen, depth=depth + 1)
                for item in value
            ]
        finally:
            seen.remove(identity)
    raise _UnsafePayload("unsupported payload type")


def _is_substantive(value: Any, *, seen: set[int]) -> bool:
    if value is None:
        return False
    if type(value) is str:
        return bool(value.strip())
    if type(value) is bytes:
        return bool(value)
    if type(value) in (bool, int, float):
        return True
    if type(value) is dict:
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            return any(_is_substantive(item, seen=seen) for item in value.values())
        finally:
            seen.remove(identity)
    if type(value) in (list, tuple):
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            return any(_is_substantive(item, seen=seen) for item in value)
        finally:
            seen.remove(identity)
    return False


def _stored_fingerprint(artifact: Artifact, reason: str) -> str:
    private_hash = _sha256(artifact.content_hash) if artifact.content_hash else ""
    return _sha256(_canonical_json({"reason": reason, "stored": private_hash}))


def _safe_snapshot(
    artifact: Artifact, *, max_payload_chars: int
) -> tuple[Any, str, Optional[str], Optional[int]]:
    if artifact.payload is None and artifact.path:
        reason = "path-backed artifact not snapshotted"
        return None, _stored_fingerprint(artifact, reason), reason, None
    try:
        normalized = _normalize_payload(artifact.payload, seen=set())
        canonical = _canonical_json(normalized)
    except _UnsafePayload as exc:
        reason = str(exc)
        return None, _stored_fingerprint(artifact, reason), reason, None
    except (RecursionError, UnicodeError, TypeError, ValueError, OverflowError):
        reason = "payload serialization failed"
        return None, _stored_fingerprint(artifact, reason), reason, None
    fingerprint = _sha256(canonical)
    if not _is_substantive(artifact.payload, seen=set()):
        return None, fingerprint, "empty payload", None
    if len(canonical) > max_payload_chars:
        return None, fingerprint, "payload exceeds limit", None
    return normalized, fingerprint, None, len(canonical)


def _allowed_facets(dossier: TaskEvidenceDossier, type_name: str) -> set[str]:
    allowed: set[str] = set()
    artifact_type = dossier.artifact_type(type_name)
    if artifact_type is not None:
        allowed.update(artifact_type.required_facets)
        allowed.update(artifact_type.facets)
    for subgoal in dossier.subgoals:
        allowed.update(subgoal.required_output_facets.get(type_name, {}))
    return allowed


def _public_lineage_fingerprints(
    dossier: TaskEvidenceDossier,
    execution: ExecutionResult,
    artifact: Artifact,
    *,
    max_payload_chars: int,
) -> tuple[str, ...]:
    fingerprints: list[str] = []
    for private_id in artifact.derived_from:
        ancestor = execution.trace.artifacts.get(private_id)
        if ancestor is None:
            fingerprints.append(_sha256("unavailable-lineage-artifact"))
            continue
        _, content_fingerprint, reason, _ = _safe_snapshot(
            ancestor, max_payload_chars=max_payload_chars
        )
        facets = {
            key: ancestor.facets[key]
            for key in sorted(_allowed_facets(dossier, ancestor.type_name))
            if key in ancestor.facets
        }
        fingerprints.append(
            _sha256(
                _canonical_json(
                    {
                        "type_name": ancestor.type_name,
                        "facets": facets,
                        "content_fingerprint": content_fingerprint,
                        "unavailable_reason": reason,
                    }
                )
            )
        )
    return tuple(sorted(fingerprints))


def _safe_cost(cost: CostProfile) -> dict[str, float | int]:
    values = cost.model_dump()
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"resource field {field} must be numeric")
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"resource field {field} must be finite and non-negative")
    if not isinstance(cost.tokens, int) or isinstance(cost.tokens, bool):
        raise ValueError("resource tokens must be an integer")
    return values


def _safe_checks(execution: ExecutionResult) -> list[dict[str, Any]]:
    grouped: Counter[str] = Counter()
    bodies: dict[str, dict[str, Any]] = {}
    for result in (*execution.report.results, *execution.trace.checks):
        if result.level is CheckLevel.PREFERENCE or result.source in _SUBJECTIVE_SOURCES:
            continue
        score = result.score
        if score is not None and not math.isfinite(score):
            score = None
        body = {
            "level": result.level.value,
            "status": result.status.value,
            "source": result.source.value,
            "score": score,
            "blocking": result.blocking,
        }
        key = _canonical_json(body)
        grouped[key] += 1
        bodies[key] = body
    return [
        {**bodies[key], "occurrences": grouped[key]}
        for key in sorted(grouped)
    ]


def build_candidate_view(
    dossier: TaskEvidenceDossier,
    execution: ExecutionResult,
    *,
    case_id: str,
    max_payload_chars: int = 20_000,
) -> CandidateView:
    """Build a deterministic positive-allowlist view of one execution."""
    if not case_id.strip():
        raise ValueError("case_id must be non-empty")
    if max_payload_chars <= 0:
        raise ValueError("max_payload_chars must be positive")

    output_types = sorted(dossier.required_outputs or execution.outputs)
    output_bodies: list[dict[str, Any]] = []
    output_lengths: list[Optional[int]] = []
    for type_name in output_types:
        artifact = execution.outputs.get(type_name)
        if artifact is None:
            missing_reason = "required output unavailable"
            output_bodies.append(
                {
                    "type_name": type_name,
                    "facets": {},
                    "snapshot": None,
                    "content_fingerprint": _sha256(missing_reason),
                    "content_available": False,
                    "content_unavailable_reason": missing_reason,
                    "lineage_fingerprints": (),
                }
            )
            output_lengths.append(None)
            continue
        snapshot, fingerprint, unavailable_reason, length = _safe_snapshot(
            artifact, max_payload_chars=max_payload_chars
        )
        facets = {
            key: artifact.facets[key]
            for key in sorted(_allowed_facets(dossier, type_name))
            if key in artifact.facets
        }
        output_bodies.append(
            {
                "type_name": type_name,
                "facets": facets,
                "snapshot": snapshot,
                "content_fingerprint": fingerprint,
                "content_available": unavailable_reason is None,
                "content_unavailable_reason": unavailable_reason,
                "lineage_fingerprints": _public_lineage_fingerprints(
                    dossier,
                    execution,
                    artifact,
                    max_payload_chars=max_payload_chars,
                ),
            }
        )
        output_lengths.append(length)

    checks = _safe_checks(execution)
    event_counts = dict(
        sorted(
            Counter(
                event.kind.value
                for event in execution.trace.events
                if event.kind is not EventKind.CHECK
            ).items()
        )
    )
    check_count = sum(check["occurrences"] for check in checks)
    failure_count = len(execution.state.failed) + sum(
        check["occurrences"]
        for check in checks
        if check["status"] == CheckStatus.FAIL.value
    )
    trace_body = {
        "event_counts": event_counts,
        "check_count": check_count,
        "failure_count": failure_count,
    }
    trace_digest = _sha256(_canonical_json(trace_body))
    resources = _safe_cost(execution.cost)
    output_length = (
        sum(length for length in output_lengths if length is not None)
        if output_lengths and all(length is not None for length in output_lengths)
        else None
    )
    body = {
        "task_id": dossier.task_id,
        "case_id": case_id.strip(),
        "goal": dossier.goal,
        "context": dossier.context,
        "outputs": output_bodies,
        "checks": checks,
        "trace": {**trace_body, "digest": trace_digest},
        "resources": resources,
        "output_length": output_length,
    }
    packet_id = f"packet::{_sha256(_canonical_json(body))}"

    output_views: list[OutputView] = []
    references: list[str] = []
    for index, output in enumerate(output_bodies):
        reference_id = f"{packet_id}:artifact:{index}"
        lineage_refs = tuple(
            f"{packet_id}:lineage:{index}:{position}:{fingerprint[:20]}"
            for position, fingerprint in enumerate(output.pop("lineage_fingerprints"))
        )
        output_views.append(
            OutputView(
                reference_id=reference_id,
                lineage_refs=lineage_refs,
                **output,
            )
        )
        references.extend((reference_id, *lineage_refs))

    check_views: list[PacketCheck] = []
    for index, check in enumerate(checks):
        reference_id = f"{packet_id}:check:{index}"
        check_views.append(PacketCheck(reference_id=reference_id, **check))
        references.append(reference_id)

    trace_reference = f"{packet_id}:trace"
    resources_reference = f"{packet_id}:resources"
    references.extend((trace_reference, resources_reference))
    return CandidateView(
        packet_id=packet_id,
        task_id=dossier.task_id,
        case_id=case_id.strip(),
        goal=dossier.goal,
        context=dossier.context,
        outputs=tuple(output_views),
        checks=tuple(check_views),
        trace=TraceDigest(
            reference_id=trace_reference,
            event_counts=event_counts,
            check_count=check_count,
            failure_count=failure_count,
            digest=trace_digest,
        ),
        resources=CostProfile.model_validate(resources),
        resources_reference_id=resources_reference,
        output_length=output_length,
        reference_ids=tuple(sorted(references)),
    )


def validate_evidence_refs(
    evidence_refs: Sequence[str], views: Sequence[CandidateView]
) -> list[str]:
    """Return sorted distinct references not exposed by either packet."""
    available = {ref for view in views for ref in view.reference_ids}
    return sorted({ref for ref in evidence_refs if ref not in available})


__all__ = [
    "CandidateView",
    "OutputView",
    "PacketCheck",
    "TraceDigest",
    "build_candidate_view",
    "validate_evidence_refs",
]
