"""Anonymous, deterministic candidate packets at the judge trust boundary."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from agentcoop.execute.engine import ExecutionResult
from agentcoop.execute.state import RunState
from agentcoop.execute.trace import EventKind, Trace, TraceEvent
from agentcoop.ir.artifacts import Artifact, ArtifactType
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import Subgoal, TaskEvidenceDossier
from agentcoop.optimize.packets import (
    CandidateView,
    build_candidate_view,
    validate_evidence_refs,
)


def packet_dossier(*, required_outputs: tuple[str, ...] = ("report",)) -> TaskEvidenceDossier:
    artifact_types = [
        ArtifactType(
            name=name,
            facets={"format": "markdown"},
            required_facets=["format"],
        )
        for name in required_outputs
    ]
    return TaskEvidenceDossier(
        task_id="task",
        goal="Produce an actionable report.",
        context="Use the supplied evidence.",
        required_outputs=list(required_outputs),
        artifact_types=artifact_types,
        subgoals=[
            Subgoal(
                subgoal_id=f"write-{name}",
                description=f"Write {name}",
                required_capability="write",
                produces=[name],
                required_output_facets={name: {"format": "markdown"}},
            )
            for name in required_outputs
        ],
    )


def execution(
    *,
    identity: str = "PRIVATE_IDENTITY_ALPHA",
    payload: Any = None,
    output_type: str = "report",
    extra_outputs: dict[str, Artifact] | None = None,
    score: float | None = 0.8,
) -> ExecutionResult:
    if payload is None:
        payload = {"answer": "CANDIDATE_SELF_IDENTIFIES", "steps": ["one", "two"]}
    ancestor_id = f"{identity}:ancestor"
    output = Artifact(
        artifact_id=f"{identity}:artifact",
        type_name=output_type,
        facets={"format": "markdown", "provider": identity},
        payload=payload,
        path=f"/private/{identity}/report.json",
        content_hash=f"raw-hash-{identity}",
        producer=f"component-{identity}",
        derived_from=[ancestor_id],
        notes=[f"note-{identity}"],
    )
    ancestor = Artifact(
        artifact_id=ancestor_id,
        type_name="source",
        payload={"source": "stable"},
        producer=f"loader-{identity}",
    )
    objective = CheckResult(
        check_id=f"check-{identity}",
        level=CheckLevel.CLAIM,
        status=CheckStatus.PASS,
        source=SignalSource.TOOL,
        summary=f"summary-{identity}",
        score=score,
        subject=f"subject-{identity}",
        subject_kind="artifact",
        evidence={"secret": identity},
        blocking=False,
    )
    subjective = CheckResult(
        check_id=f"subjective-{identity}",
        level=CheckLevel.PREFERENCE,
        status=CheckStatus.FAIL,
        source=SignalSource.LLM_JUDGE,
        summary=f"judge-{identity}",
        subject=f"subject-{identity}",
        blocking=True,
    )
    trace = Trace(
        run_id=f"run-{identity}",
        events=[
            TraceEvent(
                event_id=f"event-{identity}",
                step=1,
                kind=EventKind.NODE_END,
                node_id=f"node-{identity}",
                artifact_id=f"artifact-{identity}",
                detail=f"detail-{identity}",
                payload={"provider": identity},
            ),
            TraceEvent(
                event_id=f"check-event-{identity}",
                step=2,
                kind=EventKind.CHECK,
                detail=f"check-{identity}",
            ),
        ],
        artifacts={ancestor_id: ancestor, output.artifact_id: output},
        checks=[objective, subjective],
        step=2,
    )
    state = RunState(
        run_id=f"run-{identity}",
        workflow_id=f"workflow-{identity}",
        task_id="task",
        executed=[f"node-{identity}"],
    )
    outputs = {output_type: output, **(extra_outputs or {})}
    return ExecutionResult(
        ok=True,
        trace=trace,
        state=state,
        outputs=outputs,
        cost=CostProfile(
            latency_s=1.5,
            tokens=20,
            usd=0.02,
            cpu_seconds=0.5,
            peak_memory_gb=0.1,
        ),
        report=CheckReport(results=[objective, subjective]),
    )


class ExplosiveObject:
    def __repr__(self) -> str:
        raise AssertionError("arbitrary repr must never be evaluated")


class HostileMapping(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("custom mapping must not be traversed")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("custom mapping must not be traversed")

    def __len__(self) -> int:
        raise RuntimeError("custom mapping must not be traversed")


class HostileString(str):
    def encode(self, *args: Any, **kwargs: Any) -> bytes:
        raise RuntimeError("string subclass methods must not be invoked")


class TestCandidatePacketIdentity:
    def test_equivalent_runs_are_byte_equivalent_and_identity_free(self) -> None:
        first = build_candidate_view(packet_dossier(), execution(identity="PRIVATE_ALPHA"), case_id="dev-0")
        second = build_candidate_view(packet_dossier(), execution(identity="PRIVATE_BETA"), case_id="dev-0")

        assert first == second
        assert first.model_dump_json() == second.model_dump_json()
        serialized = first.model_dump_json()
        assert "PRIVATE_ALPHA" not in serialized
        assert "PRIVATE_BETA" not in serialized
        assert "workflow-" not in serialized
        assert "component-" not in serialized
        assert "provider" not in serialized
        assert first.outputs[0].snapshot["answer"] == "CANDIDATE_SELF_IDENTIFIES"
        assert first.outputs[0].facets == {"format": "markdown"}
        assert first.outputs[0].lineage_refs
        assert all(ref in first.reference_ids for ref in first.outputs[0].lineage_refs)
        assert validate_evidence_refs(first.reference_ids, (first,)) == []

    def test_changed_candidate_content_changes_packet_hash(self) -> None:
        first = build_candidate_view(packet_dossier(), execution(payload={"answer": "A"}), case_id="dev-0")
        changed = build_candidate_view(packet_dossier(), execution(payload={"answer": "B"}), case_id="dev-0")

        assert first.packet_id != changed.packet_id
        assert first.outputs[0].content_fingerprint != changed.outputs[0].content_fingerprint

    def test_reference_validation_reports_only_unresolved_public_refs(self) -> None:
        view = build_candidate_view(packet_dossier(), execution(), case_id="dev-0")
        refs = (view.trace.reference_id, "missing:z", "missing:a", "missing:z")

        assert validate_evidence_refs(refs, (view,)) == ["missing:a", "missing:z"]

    def test_models_are_strict(self) -> None:
        view = build_candidate_view(packet_dossier(), execution(), case_id="dev-0")
        with pytest.raises(ValidationError):
            CandidateView.model_validate({**view.model_dump(), "candidate_id": "forbidden"})

        forged = view.model_dump()
        forged["reference_ids"] = (*forged["reference_ids"], "PRIVATE_FORGED_REF")
        with pytest.raises(ValidationError, match="exactly"):
            CandidateView.model_validate(forged)


class TestSafePayloadPolicy:
    def test_json_tuples_and_bytes_are_canonicalized_without_rewriting_text(self) -> None:
        view = build_candidate_view(
            packet_dossier(),
            execution(payload={"tuple": (1, "CANDIDATE_TEXT"), "blob": b"abc"}),
            case_id="dev-0",
        )

        snapshot = view.outputs[0].snapshot
        assert snapshot["tuple"] == [1, "CANDIDATE_TEXT"]
        assert snapshot["blob"] == {
            "__agentcoop_type__": "bytes",
            "base64": "YWJj",
        }
        assert view.output_length is not None

    @pytest.mark.parametrize(
        "payload,reason",
        [
            ({"bad": float("nan")}, "non-finite"),
            ({1: "non-string-key"}, "non-string mapping key"),
            (ExplosiveObject(), "unsupported payload type"),
            (range(3), "unsupported payload type"),
            (HostileMapping(), "unsupported payload type"),
            ("\ud800", "invalid unicode"),
        ],
    )
    def test_unsafe_payloads_use_symmetric_unavailable_markers(
        self, payload: Any, reason: str
    ) -> None:
        view = build_candidate_view(
            packet_dossier(), execution(payload=payload), case_id="dev-0"
        )

        output = view.outputs[0]
        assert output.snapshot is None
        assert output.content_available is False
        assert output.content_unavailable_reason == reason
        assert view.output_length is None
        assert "ExplosiveObject" not in view.model_dump_json()

    def test_scalar_subclasses_are_not_treated_as_json_native(self) -> None:
        view = build_candidate_view(
            packet_dossier(),
            execution(payload=HostileString("text")),
            case_id="dev-0",
        )

        assert view.outputs[0].snapshot is None
        assert view.outputs[0].content_unavailable_reason == (
            "unsupported payload type"
        )

    def test_deep_nesting_is_marked_unavailable_without_recursion_failure(self) -> None:
        payload: Any = "leaf"
        for _ in range(200):
            payload = [payload]

        view = build_candidate_view(
            packet_dossier(), execution(payload=payload), case_id="dev-0"
        )

        assert view.outputs[0].snapshot is None
        assert view.outputs[0].content_unavailable_reason == (
            "payload nesting exceeds limit"
        )

    @pytest.mark.parametrize("kind", ["list", "mapping"])
    def test_cyclic_payloads_are_deterministically_unavailable(self, kind: str) -> None:
        payload: Any
        if kind == "list":
            payload = []
            payload.append(payload)
        else:
            payload = {}
            payload["self"] = payload

        view = build_candidate_view(
            packet_dossier(), execution(payload=payload), case_id="dev-0"
        )

        assert view.outputs[0].snapshot is None
        assert view.outputs[0].content_unavailable_reason == "cyclic payload"
        assert view.output_length is None

    def test_path_only_artifact_is_not_dereferenced_or_disclosed(self) -> None:
        result = execution(payload={"answer": "placeholder"})
        output = result.outputs["report"]
        output.payload = None
        output.path = "/must/not/be/read/PRIVATE_PATH"
        view = build_candidate_view(packet_dossier(), result, case_id="dev-0")

        assert view.outputs[0].snapshot is None
        assert view.outputs[0].content_unavailable_reason == (
            "path-backed artifact not snapshotted"
        )
        assert "PRIVATE_PATH" not in view.model_dump_json()
        assert view.output_length is None

    def test_oversized_payload_is_fingerprinted_not_truncated(self) -> None:
        result = execution(payload="x" * 101)
        first = build_candidate_view(
            packet_dossier(), result, case_id="dev-0", max_payload_chars=100
        )
        second = build_candidate_view(
            packet_dossier(), result, case_id="dev-0", max_payload_chars=100
        )

        assert first == second
        assert first.outputs[0].snapshot is None
        assert first.outputs[0].content_unavailable_reason == "payload exceeds limit"
        assert first.outputs[0].content_fingerprint
        assert "x" * 20 not in first.model_dump_json()
        assert first.output_length is None

    def test_one_unavailable_required_output_disables_verbosity_length(self) -> None:
        unavailable = Artifact(
            artifact_id="private-second",
            type_name="appendix",
            facets={"format": "markdown"},
            payload=None,
            path="/not/read",
        )
        result = execution(extra_outputs={"appendix": unavailable})
        view = build_candidate_view(
            packet_dossier(required_outputs=("report", "appendix")),
            result,
            case_id="dev-0",
        )

        assert len(view.outputs) == 2
        assert view.output_length is None


class TestChecksAndTrace:
    def test_subjective_checks_are_filtered_and_safe_checks_are_coalesced(self) -> None:
        view = build_candidate_view(packet_dossier(), execution(), case_id="dev-0")

        assert len(view.checks) == 1
        check = view.checks[0]
        assert check.level is CheckLevel.CLAIM
        assert check.source is SignalSource.TOOL
        assert check.occurrences == 2
        assert view.trace.event_counts == {EventKind.NODE_END.value: 1}
        assert view.trace.check_count == 2
        assert view.trace.failure_count == 0

    def test_non_finite_check_scores_are_not_serialized(self) -> None:
        view = build_candidate_view(
            packet_dossier(), execution(score=float("inf")), case_id="dev-0"
        )

        assert view.checks[0].score is None
        assert "Infinity" not in view.model_dump_json()

    def test_all_public_references_are_mechanically_resolvable(self) -> None:
        view = build_candidate_view(packet_dossier(), execution(), case_id="dev-0")
        expected = {
            view.trace.reference_id,
            view.resources_reference_id,
            *(output.reference_id for output in view.outputs),
            *(output.lineage_refs[0] for output in view.outputs),
            *(check.reference_id for check in view.checks),
        }

        assert expected <= set(view.reference_ids)
        assert validate_evidence_refs(tuple(expected), (view,)) == []
        parsed = json.loads(view.model_dump_json())
        assert parsed["packet_id"] == view.packet_id
