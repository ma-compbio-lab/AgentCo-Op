"""Detectors — turning a trace into observations.

Each detector answers one narrow question and records where the answer came
from. Detectors never speculate about causes; several of them fire on the same
underlying fault and it is the diagnoser's job to reconcile them.

The one that matters most is :func:`detect_empty_output`. A component that
returns exit code 0 with an empty gene set has not failed in any way the
executor can see, and in v1 that ran straight through to a confident final
report. The adapters annotate emitted artifacts with emptiness notes at the
boundary where it is observable; this detector promotes those notes into
signals.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from agentcoop.components.base import (
    EMPTY_FIELD_PREFIX,
    EMPTY_PAYLOAD_NOTE,
    FACET_CONFLICT_PREFIX,
    empty_field_paths,
    parse_error_line,
    payload_is_empty,
)
from agentcoop.diagnose.signals import Severity, Signal, SignalKind, SignalSet
from agentcoop.execute.trace import EventKind, Trace
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckStatus, SignalSource
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.faults import BlameTarget, FaultClass
from agentcoop.ir.workflow import CompiledWorkflow


class Detector(Protocol):
    name: str

    def detect(
        self,
        trace: Trace,
        report: CheckReport,
        workflow: Optional[CompiledWorkflow],
        dossier: Optional[TaskEvidenceDossier],
    ) -> list[Signal]: ...


def _sid(prefix: str, *parts: Any) -> str:
    return ":".join([prefix, *(str(p) for p in parts if p is not None)])


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


def detect_check_failures(trace: Trace, report: CheckReport, *_: Any) -> list[Signal]:
    out: list[Signal] = []
    for result in report.failures():
        kind = SignalKind.CHECK_FAILURE
        if "schema" in result.summary.lower():
            kind = SignalKind.SCHEMA_VIOLATION
        elif "facet" in result.summary.lower() or "underspecified" in result.summary.lower():
            kind = SignalKind.FACET_VIOLATION
        blame = {
            "node": BlameTarget.NODE,
            "edge": BlameTarget.EDGE,
            "artifact": BlameTarget.ARTIFACT,
            "component": BlameTarget.COMPONENT,
        }.get(result.subject_kind, BlameTarget.NONE)
        out.append(
            Signal(
                signal_id=_sid("check", result.check_id),
                kind=kind,
                severity=Severity.CRITICAL if result.blocking else Severity.ERROR,
                source=result.source,
                subject=result.subject,
                subject_kind=blame,
                detail=result.summary,
                step=_step_of(trace, result.subject),
                evidence={"check_id": result.check_id, "level": result.level.value},
            )
        )
    return out


def detect_evaluator_unavailable(trace: Trace, report: CheckReport, *_: Any) -> list[Signal]:
    """An unavailable evaluator is a coverage gap, reported as such.

    Not an error — but not silence either. A workflow whose claim-level checks
    could never run has a different standing from one whose checks passed.
    """
    return [
        Signal(
            signal_id=_sid("unavailable", result.check_id),
            kind=SignalKind.EVALUATOR_UNAVAILABLE,
            severity=Severity.INFO,
            source=result.source,
            subject=result.subject,
            subject_kind=BlameTarget.EVALUATOR,
            detail=result.summary,
            evidence={"check_id": result.check_id, "level": result.level.value},
        )
        for result in report.unavailable()
    ]


def detect_tool_errors(trace: Trace, *_: Any) -> list[Signal]:
    out: list[Signal] = []
    for node_id in sorted(trace.node_results):
        result = trace.node_results[node_id]
        if result.ok:
            continue
        for line in result.errors:
            fault, message = parse_error_line(line)
            kind = SignalKind.TOOL_ERROR
            if fault is FaultClass.ENVIRONMENT:
                kind = SignalKind.ADAPTER_MISSING
            elif "timeout" in message.lower():
                kind = SignalKind.TIMEOUT
            elif fault is FaultClass.ARTIFACT_CONTRACT:
                kind = SignalKind.FACET_VIOLATION
            elif fault is FaultClass.COORDINATION:
                kind = SignalKind.MERGE_REFUSED
            out.append(
                Signal(
                    signal_id=_sid("error", node_id, len(out)),
                    kind=kind,
                    severity=Severity.CRITICAL,
                    subject=node_id,
                    subject_kind=BlameTarget.NODE,
                    detail=message,
                    step=_step_of(trace, node_id),
                    evidence={
                        "declared_fault": fault.value if fault else None,
                        "exit_code": result.exit_code,
                    },
                )
            )
    return out


def detect_empty_output(trace: Trace, *_: Any) -> list[Signal]:
    """Exit code 0 with nothing in it — the most dangerous shape of failure.

    Emptiness is recomputed from the payload rather than read off the notes
    the producing adapter attached. The notes are a supplement, not the
    source of truth: a component that returns an empty result while claiming
    success is precisely the component that will not have annotated its own
    emptiness, and a detector that trusted the annotation would go blind
    exactly where it is needed.
    """
    out: list[Signal] = []
    for artifact_id in sorted(trace.artifacts):
        artifact = trace.artifacts[artifact_id]
        empty_fields = sorted(
            set(empty_field_paths(artifact.payload))
            | {
                n[len(EMPTY_FIELD_PREFIX) :]
                for n in artifact.notes
                if n.startswith(EMPTY_FIELD_PREFIX)
            }
        )
        wholly_empty = EMPTY_PAYLOAD_NOTE in artifact.notes or (
            payload_is_empty(artifact.payload) and artifact.path is None
        )
        if not (empty_fields or wholly_empty):
            continue
        producer_ok = True
        result = trace.node_results.get(artifact.producer)
        if result is not None:
            producer_ok = result.ok
        out.append(
            Signal(
                signal_id=_sid("empty", artifact_id),
                kind=SignalKind.EMPTY_OUTPUT,
                # Silent is worse than loud: the component reported success.
                severity=Severity.CRITICAL if producer_ok else Severity.WARNING,
                subject=artifact_id,
                subject_kind=BlameTarget.ARTIFACT,
                detail=(
                    f"'{artifact.type_name}' from '{artifact.producer}' is empty"
                    + (f" at {', '.join(empty_fields)}" if empty_fields else "")
                    + (" while the component reported success" if producer_ok else "")
                ),
                step=artifact.step,
                evidence={
                    "producer": artifact.producer,
                    "empty_fields": empty_fields,
                    "silent": producer_ok,
                },
            )
        )
    return out


def detect_facet_conflicts(trace: Trace, *_: Any) -> list[Signal]:
    """A component whose observed facets contradict its declaration."""
    out: list[Signal] = []
    for artifact_id in sorted(trace.artifacts):
        artifact = trace.artifacts[artifact_id]
        for note in artifact.notes:
            if not note.startswith(FACET_CONFLICT_PREFIX):
                continue
            out.append(
                Signal(
                    signal_id=_sid("facet_conflict", artifact_id),
                    kind=SignalKind.FACET_VIOLATION,
                    severity=Severity.ERROR,
                    subject=artifact_id,
                    subject_kind=BlameTarget.ARTIFACT,
                    detail=note[len(FACET_CONFLICT_PREFIX) :],
                    step=artifact.step,
                    evidence={"producer": artifact.producer},
                )
            )
    return out


def detect_handoff_rejections(trace: Trace, *_: Any) -> list[Signal]:
    return [
        Signal(
            signal_id=_sid("handoff", event.node_id, event.artifact_id),
            kind=SignalKind.HANDOFF_REJECTED,
            severity=Severity.CRITICAL,
            subject=event.node_id,
            subject_kind=BlameTarget.EDGE,
            detail=event.detail,
            step=event.step,
            evidence={"artifact": event.artifact_id},
        )
        for event in trace.events_of(EventKind.HANDOFF_REJECTED)
    ]


def detect_merge_refusals(trace: Trace, *_: Any) -> list[Signal]:
    return [
        Signal(
            signal_id=_sid("merge", event.node_id),
            kind=SignalKind.MERGE_REFUSED,
            severity=Severity.CRITICAL,
            subject=event.node_id,
            subject_kind=BlameTarget.TOPOLOGY,
            detail=event.detail,
            step=event.step,
        )
        for event in trace.events_of(EventKind.MERGE_REFUSED)
    ]


def detect_branch_disagreement(trace: Trace, *_: Any) -> list[Signal]:
    """Sibling branches producing incompatible results.

    Disagreement is a signal, never itself a fault: it may mean one branch is
    broken, or that the branches genuinely measure different things and the
    merge is wrong, or that the finding is real and modality-specific.
    """
    out: list[Signal] = []
    by_type: dict[str, list] = {}
    for artifact_id in sorted(trace.artifacts):
        artifact = trace.artifacts[artifact_id]
        if artifact.producer.endswith("__merge"):
            continue
        by_type.setdefault(artifact.type_name, []).append(artifact)

    for type_name, artifacts in sorted(by_type.items()):
        if len(artifacts) < 2:
            continue
        hashes = {a.content_hash for a in artifacts}
        if len(hashes) <= 1:
            continue
        out.append(
            Signal(
                signal_id=_sid("disagreement", type_name),
                kind=SignalKind.BRANCH_DISAGREEMENT,
                severity=Severity.WARNING,
                subject=type_name,
                subject_kind=BlameTarget.ARTIFACT,
                detail=(
                    f"{len(hashes)} distinct '{type_name}' results from "
                    f"{', '.join(sorted(a.producer for a in artifacts))}"
                ),
                step=max(a.step for a in artifacts),
                evidence={"producers": sorted(a.producer for a in artifacts)},
            )
        )
    return out


def detect_needless_serialization(
    trace: Trace, report: CheckReport, workflow, dossier
) -> list[Signal]:
    """Two nodes chained with nothing flowing between them.

    A coordination fault that no node-level repair can address, and one that
    is invisible to every other detector because nothing breaks: the workflow
    runs correctly, just serialized, paying latency and adding a failure path
    for a dependency that does not exist.

    Evidence-based rather than merely structural: the edge is only reported
    when the trace shows no handoff actually crossed it. A declared ordering
    that *did* carry an artifact is a real dependency, whatever the graph
    happens to look like.
    """
    if workflow is None:
        return []
    try:
        graph = workflow.graph()
    except Exception:
        return []

    carried: set[tuple[str, str]] = set()
    consumed_by: dict[str, set[str]] = {}
    for event in trace.events:
        if event.kind is not EventKind.HANDOFF or not event.node_id:
            continue
        artifact = trace.artifacts.get(event.artifact_id or "")
        if artifact is None:
            continue
        consumed_by.setdefault(event.node_id, set()).add(artifact.artifact_id)
        if artifact.producer:
            carried.add((artifact.producer, event.node_id))

    out: list[Signal] = []
    for edge in sorted(graph.edges, key=lambda e: (e.source, e.target)):
        if (edge.source, edge.target) in carried:
            continue
        if edge.target not in trace.node_results or edge.source not in trace.node_results:
            continue
        if not trace.node_results[edge.source].ok:
            continue  # nothing flowed because the source failed; a different fault
        out.append(
            Signal(
                signal_id=_sid("serialization", edge.source, edge.target),
                kind=SignalKind.NEEDLESS_SERIALIZATION,
                severity=Severity.ERROR,
                subject=f"{edge.source}->{edge.target}",
                subject_kind=BlameTarget.EDGE,
                detail=(
                    f"'{edge.target}' was made to wait for '{edge.source}' but "
                    "consumed nothing it produced; the ordering costs latency and "
                    "adds a failure path without carrying any data"
                ),
                step=_step_of(trace, edge.target),
                evidence={"source": edge.source, "target": edge.target},
            )
        )
    return out


def detect_progress_stall(trace: Trace, report: CheckReport, workflow, dossier) -> list[Signal]:
    """Nodes the graph expected to run that never did."""
    if workflow is None:
        return []
    try:
        expected = {n.node_id for n in workflow.graph().nodes}
    except ValueError:
        return []
    executed = set(trace.node_results)
    missing = sorted(expected - executed)
    if not missing:
        return []
    return [
        Signal(
            signal_id=_sid("stall", "unreached"),
            kind=SignalKind.PROGRESS_STALL,
            severity=Severity.ERROR,
            subject=missing[0],
            subject_kind=BlameTarget.TOPOLOGY,
            detail=f"{len(missing)} node(s) never executed: {', '.join(missing)}",
            step=trace.step,
            evidence={"unreached": missing},
        )
    ]


def _step_of(trace: Trace, subject: Optional[str]) -> int:
    if not subject:
        return 0
    events = [e for e in trace.events if e.node_id == subject or e.artifact_id == subject]
    return events[0].step if events else 0


# ---------------------------------------------------------------------------


DEFAULT_DETECTORS = (
    detect_check_failures,
    detect_evaluator_unavailable,
    detect_tool_errors,
    detect_empty_output,
    detect_facet_conflicts,
    detect_handoff_rejections,
    detect_merge_refusals,
    detect_branch_disagreement,
    detect_needless_serialization,
    detect_progress_stall,
)


def detect_all(
    trace: Trace,
    report: CheckReport,
    workflow: Optional[CompiledWorkflow] = None,
    dossier: Optional[TaskEvidenceDossier] = None,
    detectors=DEFAULT_DETECTORS,
) -> SignalSet:
    """Run every detector. Signals are sorted so diagnosis is reproducible."""
    signals = SignalSet()
    for detector in detectors:
        try:
            signals.extend(list(detector(trace, report, workflow, dossier)))
        except TypeError:
            signals.extend(list(detector(trace, report)))  # 2-arg detectors
    signals.signals.sort(key=lambda s: (s.step, s.signal_id))
    return signals


__all__ = [
    "Detector",
    "SignalKind",
    "DEFAULT_DETECTORS",
    "detect_all",
    "detect_check_failures",
    "detect_evaluator_unavailable",
    "detect_tool_errors",
    "detect_empty_output",
    "detect_facet_conflicts",
    "detect_handoff_rejections",
    "detect_merge_refusals",
    "detect_branch_disagreement",
    "detect_needless_serialization",
    "detect_progress_stall",
]
