"""Detection, backward-slice localization, and hypothesis ranking.

The property under test throughout: a symptom is not a cause, and a downstream
component is never blamed for consuming a corrupted upstream artifact.
"""

from __future__ import annotations

import pytest

from agentcoop.components.base import (
    EMPTY_FIELD_PREFIX,
    EMPTY_PAYLOAD_NOTE,
    InvocationResult,
    error_line,
    make_artifact,
)
from agentcoop.diagnose.detectors import detect_all
from agentcoop.diagnose.diagnose import Diagnoser
from agentcoop.diagnose.localize import localize, validate_artifact
from agentcoop.diagnose.signals import Severity, Signal, SignalKind, SignalSet
from agentcoop.execute.trace import EventKind, Trace
from agentcoop.ir.artifacts import Artifact, ArtifactType
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
)
from agentcoop.ir.dossier import Subgoal, TaskEvidenceDossier
from agentcoop.ir.faults import BlameTarget, Diagnosis, FaultClass, FaultHypothesis
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, Sequence


def gene_type() -> ArtifactType:
    return ArtifactType(
        name="gene_set",
        json_schema={
            "type": "object",
            "required": ["genes"],
            "properties": {"genes": {"type": "array"}},
        },
        required_facets=["namespace"],
    )


def dossier() -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="t",
        goal="g",
        artifact_types=[gene_type(), ArtifactType(name="report", json_schema={"type": "object"})],
        subgoals=[
            Subgoal(
                subgoal_id="s1",
                description="de",
                required_capability="differential_expression",
                produces=["gene_set"],
            ),
            Subgoal(
                subgoal_id="s2",
                description="interpret",
                required_capability="gene_set_interpretation",
                consumes=["gene_set"],
                produces=["report"],
            ),
        ],
    )


def workflow() -> CompiledWorkflow:
    return CompiledWorkflow(
        workflow_id="w",
        task_id="t",
        term=Sequence(
            children_terms=[
                Atomic(component="de", subgoal_id="s1"),
                Atomic(component="interp", subgoal_id="s2"),
            ]
        ).ensure_ids(),
    )


def corrupted_upstream_trace() -> Trace:
    """The canonical shape: upstream emits a bad artifact, downstream fails.

    The gene set is missing its required ``namespace`` facet. The interpreter
    consumed it and produced a report, then a check on the report failed. A
    naive system blames the interpreter.
    """
    trace = Trace(run_id="r")
    bad = trace.record_artifact(
        make_artifact(
            type_name="gene_set",
            payload={"genes": ["A", "B"]},
            facets={},  # namespace never declared
            producer="s1__de",
        )
    )
    report = trace.record_artifact(
        Artifact(
            artifact_id="report::x",
            type_name="report",
            payload={"summary": "nonsense"},
            producer="s2__interp",
            derived_from=[bad.artifact_id],
        )
    )
    trace.record_result("s1__de", InvocationResult(ok=True, outputs={"gene_set": bad}))
    trace.record_result(
        "s2__interp", InvocationResult(ok=False, errors=[error_line(FaultClass.TOOL_FAILURE, "bad input")])
    )
    return trace


class TestDetectors:
    def test_silent_empty_output_is_critical(self) -> None:
        """Exit code 0 with an empty result is worse than a crash."""
        trace = Trace(run_id="r")
        art = trace.record_artifact(
            Artifact(
                artifact_id="a1",
                type_name="gene_set",
                payload={"genes": []},
                producer="n1",
                notes=[f"{EMPTY_FIELD_PREFIX}genes"],
            )
        )
        trace.record_result("n1", InvocationResult(ok=True, outputs={"gene_set": art}))
        signals = detect_all(trace, CheckReport())
        empty = signals.of_kind(SignalKind.EMPTY_OUTPUT)
        assert len(empty) == 1
        assert empty[0].severity is Severity.CRITICAL
        assert empty[0].evidence["silent"] is True
        assert "reported success" in empty[0].detail

    def test_empty_output_after_a_failure_is_only_a_warning(self) -> None:
        trace = Trace(run_id="r")
        art = trace.record_artifact(
            Artifact(
                artifact_id="a1",
                type_name="gene_set",
                payload={},
                producer="n1",
                notes=[EMPTY_PAYLOAD_NOTE],
            )
        )
        trace.record_result("n1", InvocationResult(ok=False, outputs={}, errors=["boom"]))
        signals = detect_all(trace, CheckReport())
        assert signals.of_kind(SignalKind.EMPTY_OUTPUT)[0].severity is Severity.WARNING

    def test_error_lines_are_classified_by_their_fault_tag(self) -> None:
        """Diagnosis must not have to regex English prose."""
        trace = Trace(run_id="r")
        trace.record_result(
            "n1",
            InvocationResult(
                ok=False,
                errors=[error_line(FaultClass.ENVIRONMENT, "docker daemon unreachable")],
            ),
        )
        signals = detect_all(trace, CheckReport())
        assert signals.of_kind(SignalKind.ADAPTER_MISSING)

    def test_unavailable_evaluator_is_reported_but_not_an_error(self) -> None:
        report = CheckReport(
            results=[
                CheckResult(
                    check_id="claim_supported",
                    level=CheckLevel.CLAIM,
                    status=CheckStatus.UNAVAILABLE,
                    summary="no claim-level oracle for this task",
                )
            ]
        )
        signals = detect_all(Trace(run_id="r"), report)
        unavailable = signals.of_kind(SignalKind.EVALUATOR_UNAVAILABLE)
        assert unavailable and unavailable[0].severity is Severity.INFO
        assert not unavailable[0].blocking

    def test_progress_stall_lists_unreached_nodes(self) -> None:
        trace = Trace(run_id="r")
        trace.record_result("s1__de", InvocationResult(ok=True))
        signals = detect_all(trace, CheckReport(), workflow())
        stalls = signals.of_kind(SignalKind.PROGRESS_STALL)
        assert stalls and "s2__interp" in stalls[0].evidence["unreached"]

    def test_detection_is_deterministic(self) -> None:
        trace = corrupted_upstream_trace()
        first = detect_all(trace, CheckReport()).ids()
        second = detect_all(trace, CheckReport()).ids()
        assert first == second


class TestArtifactValidation:
    def test_missing_required_facet_makes_an_artifact_invalid(self) -> None:
        artifact = make_artifact(type_name="gene_set", payload={"genes": ["A"]}, facets={})
        verdict = validate_artifact(artifact, declared=gene_type())
        assert not verdict.valid
        assert any("namespace" in p for p in verdict.problems)

    def test_wellformed_artifact_is_valid(self) -> None:
        artifact = make_artifact(
            type_name="gene_set", payload={"genes": ["A"]}, facets={"namespace": "HGNC"}
        )
        assert validate_artifact(artifact, declared=gene_type()).valid

    def test_emptiness_notes_make_an_artifact_invalid(self) -> None:
        artifact = make_artifact(
            type_name="gene_set",
            payload={"genes": []},
            facets={"namespace": "HGNC"},
            notes=[f"{EMPTY_FIELD_PREFIX}genes"],
        )
        assert not validate_artifact(artifact, declared=gene_type()).valid


class TestLocalization:
    def test_blame_lands_upstream_not_on_the_consumer(self) -> None:
        """The central test of this module.

        The interpreter is the node that failed. The fault is the gene set it
        was handed. Blaming the interpreter would lead to retrying it forever.
        """
        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        result = localize(trace, signals, workflow(), dossier())
        assert result.subject_kind is BlameTarget.ARTIFACT
        assert result.subject is not None and result.subject.startswith("s1__de::gene_set")
        assert "not at fault" in result.rationale

    def test_the_consumer_is_blamed_when_its_inputs_are_sound(self) -> None:
        trace = Trace(run_id="r")
        good = trace.record_artifact(
            make_artifact(
                type_name="gene_set",
                payload={"genes": ["A"]},
                facets={"namespace": "HGNC"},
                producer="s1__de",
            )
        )
        out = trace.record_artifact(
            Artifact(
                artifact_id="report::y",
                type_name="report",
                payload={"summary": "x"},
                producer="s2__interp",
                derived_from=[good.artifact_id],
            )
        )
        trace.record_result("s1__de", InvocationResult(ok=True, outputs={"gene_set": good}))
        trace.record_result(
            "s2__interp",
            InvocationResult(ok=False, errors=[error_line(FaultClass.TOOL_FAILURE, "crashed")]),
        )
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        result = localize(trace, signals, workflow(), dossier())
        assert result.subject_kind is BlameTarget.NODE
        assert result.subject == "s2__interp"
        assert "satisfies its own contract" in result.rationale

    def test_contamination_lists_everything_downstream(self) -> None:
        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        result = localize(trace, signals, workflow(), dossier())
        assert result.contaminated == ["report::x"]

    def test_no_blocking_signal_means_nothing_to_localize(self) -> None:
        result = localize(Trace(run_id="r"), SignalSet(), workflow(), dossier())
        assert result.subject is None
        assert "nothing to localize" in result.rationale

    def test_slice_path_is_recorded_for_audit(self) -> None:
        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        result = localize(trace, signals, workflow(), dossier())
        assert len(result.slice_path) >= 1
        assert result.verdicts


class TestDiagnosis:
    def _outcome(self, trace: Trace):
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        loc = localize(trace, signals, workflow(), dossier())
        return Diagnoser().diagnose(signals, loc, workflow(), dossier())

    def test_artifact_blame_favours_a_contract_fault(self) -> None:
        outcome = self._outcome(corrupted_upstream_trace())
        assert outcome.diagnosis.top.fault_class is FaultClass.ARTIFACT_CONTRACT
        assert outcome.diagnosis.localized_kind is BlameTarget.ARTIFACT

    def test_hypotheses_are_normalized(self) -> None:
        outcome = self._outcome(corrupted_upstream_trace())
        assert sum(h.probability for h in outcome.diagnosis.hypotheses) == pytest.approx(1.0)

    def test_contradicting_signals_are_retained(self) -> None:
        outcome = self._outcome(corrupted_upstream_trace())
        assert any(h.contradicting_signals for h in outcome.diagnosis.hypotheses)

    def test_a_diffuse_diagnosis_asks_for_evidence_rather_than_guessing(self) -> None:
        """The alternative to acting on a coin flip."""
        signals = SignalSet(
            signals=[
                Signal(
                    signal_id="s1",
                    kind=SignalKind.CHECK_FAILURE,
                    subject="n1",
                    subject_kind=BlameTarget.NONE,
                ),
            ]
        )
        from agentcoop.diagnose.localize import Localization

        outcome = Diagnoser(entropy_threshold=1.0).diagnose(
            signals, Localization(subject="n1", subject_kind=BlameTarget.NONE)
        )
        assert outcome.needs_more_evidence
        assert "rather than patching on a guess" in outcome.evidence_gathering_hint

    def test_a_sharp_diagnosis_does_not(self) -> None:
        outcome = self._outcome(corrupted_upstream_trace())
        assert outcome.diagnosis.entropy < 2.0

    def test_no_signals_yields_an_empty_diagnosis_not_a_guess(self) -> None:
        from agentcoop.diagnose.localize import Localization

        outcome = Diagnoser().diagnose(SignalSet(), Localization())
        assert outcome.diagnosis.hypotheses == []
        assert "no signal carried diagnostic weight" in outcome.diagnosis.notes

    def test_llm_cannot_introduce_an_unsupported_hypothesis(self) -> None:
        """A refiner that tries to invent a cause has it discarded."""

        def malicious(diagnosis: Diagnosis, _: dict) -> Diagnosis:
            return diagnosis.model_copy(
                update={
                    "hypotheses": [
                        FaultHypothesis(
                            fault_class=FaultClass.EVALUATOR_FAILURE,
                            probability=0.99,
                            rationale="the grader is definitely broken",
                        )
                    ]
                }
            )

        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        loc = localize(trace, signals, workflow(), dossier())
        baseline = Diagnoser().diagnose(signals, loc, workflow(), dossier())
        refined = Diagnoser(llm=malicious).diagnose(signals, loc, workflow(), dossier())

        supported = {h.fault_class for h in baseline.diagnosis.hypotheses}
        assert all(h.fault_class in supported for h in refined.diagnosis.hypotheses)

    def test_llm_may_reorder_within_the_supported_set(self) -> None:
        def reorder(diagnosis: Diagnosis, _: dict) -> Diagnosis:
            return diagnosis.model_copy(
                update={"hypotheses": list(reversed(diagnosis.hypotheses))}
            )

        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        loc = localize(trace, signals, workflow(), dossier())
        refined = Diagnoser(llm=reorder).diagnose(signals, loc, workflow(), dossier())
        assert any("reordering only" in n for n in refined.diagnosis.notes)

    def test_a_failing_llm_refiner_is_survivable(self) -> None:
        def broken(diagnosis: Diagnosis, _: dict) -> Diagnosis:
            raise RuntimeError("model unavailable")

        trace = corrupted_upstream_trace()
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        loc = localize(trace, signals, workflow(), dossier())
        outcome = Diagnoser(llm=broken).diagnose(signals, loc, workflow(), dossier())
        assert outcome.diagnosis.hypotheses

    def test_statistics_priors_shift_the_ranking(self) -> None:
        class Stats:
            def failure_distribution(self, component: str):
                return {FaultClass.ENVIRONMENT: 3.0} if component == "interp" else {}

        trace = Trace(run_id="r")
        good = trace.record_artifact(
            make_artifact(
                type_name="gene_set",
                payload={"genes": ["A"]},
                facets={"namespace": "HGNC"},
                producer="s1__de",
            )
        )
        trace.record_result("s1__de", InvocationResult(ok=True, outputs={"gene_set": good}))
        trace.record_result(
            "s2__interp",
            InvocationResult(ok=False, errors=[error_line(FaultClass.TOOL_FAILURE, "died")]),
        )
        signals = detect_all(trace, CheckReport(), workflow(), dossier())
        loc = localize(trace, signals, workflow(), dossier())

        plain = Diagnoser().diagnose(signals, loc, workflow(), dossier())
        primed = Diagnoser(statistics=Stats()).diagnose(signals, loc, workflow(), dossier())

        def p(outcome, fault):
            return next(
                (h.probability for h in outcome.diagnosis.hypotheses if h.fault_class is fault),
                0.0,
            )

        assert p(primed, FaultClass.ENVIRONMENT) > p(plain, FaultClass.ENVIRONMENT)

    def test_diagnosis_is_deterministic(self) -> None:
        first = self._outcome(corrupted_upstream_trace())
        second = self._outcome(corrupted_upstream_trace())
        assert [
            (h.fault_class, round(h.probability, 9)) for h in first.diagnosis.hypotheses
        ] == [(h.fault_class, round(h.probability, 9)) for h in second.diagnosis.hypotheses]
