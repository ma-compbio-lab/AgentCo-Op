"""Backward slicing over artifact lineage.

The failure a run reports is almost never where the fault is. A gene-set
interpreter that returns nonsense because the differential-expression step
emitted Ensembl IDs did not malfunction — it did exactly what it was asked to,
with input it could not interpret. Blaming it (and "repairing" it by retrying
or rewording its prompt) is the specific pathology this module removes.

The procedure:

1. Start at the earliest *blocking* signal, not the last failure.
2. Collect the artifacts implicated by it — the ones the failing node consumed,
   or the artifact the signal is about.
3. Walk ``derived_from`` backwards, re-validating each artifact against its own
   declared type: structural schema, semantic facets, and emptiness.
4. Blame the **earliest** artifact that fails its own check.
5. Only if every upstream artifact is valid do we blame the node that raised.

Step 5 is the important asymmetry: a node is presumed innocent until its inputs
are shown to be sound.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.components.base import EMPTY_FIELD_PREFIX, EMPTY_PAYLOAD_NOTE
from agentcoop.diagnose.signals import SignalSet
from agentcoop.execute.trace import Trace
from agentcoop.ir.artifacts import Artifact, ArtifactType, TypeRegistry, validate_payload
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.faults import BlameTarget
from agentcoop.ir.workflow import CompiledWorkflow


class ArtifactVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    valid: bool
    problems: list[str] = Field(default_factory=list)


class Localization(BaseModel):
    """Where the fault actually is, and how we got there."""

    model_config = ConfigDict(extra="forbid")

    subject: Optional[str] = None
    subject_kind: BlameTarget = BlameTarget.NONE
    #: The backward slice walked, failure first.
    slice_path: list[str] = Field(default_factory=list)
    earliest_anomalous_artifact: Optional[str] = None
    #: Per-artifact verdicts along the slice, for auditing.
    verdicts: list[ArtifactVerdict] = Field(default_factory=list)
    #: Artifacts downstream of the blamed one — all suspect regardless of
    #: whether they passed their own checks.
    contaminated: list[str] = Field(default_factory=list)
    rationale: str = ""

    @property
    def blames_upstream(self) -> bool:
        return self.subject_kind is BlameTarget.ARTIFACT


def validate_artifact(
    artifact: Artifact,
    *,
    declared: Optional[ArtifactType],
) -> ArtifactVerdict:
    """Does this artifact satisfy *its own* declared contract?

    Deliberately independent of who consumed it: the question is whether the
    artifact is sound, not whether a particular consumer liked it.
    """
    problems: list[str] = []

    if declared is not None:
        problems.extend(validate_payload(artifact.payload, declared))
        for key in declared.required_facets:
            if key not in artifact.facets:
                problems.append(f"required facet '{key}' is not declared")

    if EMPTY_PAYLOAD_NOTE in artifact.notes:
        problems.append("payload is empty")
    for note in artifact.notes:
        if note.startswith(EMPTY_FIELD_PREFIX):
            problems.append(f"empty field '{note[len(EMPTY_FIELD_PREFIX):]}'")

    return ArtifactVerdict(
        artifact_id=artifact.artifact_id, valid=not problems, problems=problems
    )


def localize(
    trace: Trace,
    signals: SignalSet,
    workflow: Optional[CompiledWorkflow] = None,
    dossier: Optional[TaskEvidenceDossier] = None,
    types: Optional[TypeRegistry] = None,
) -> Localization:
    types = types or TypeRegistry()
    seed = signals.earliest()
    if seed is None:
        return Localization(rationale="no blocking signal; nothing to localize")

    implicated = _implicated_artifacts(trace, seed.subject, seed.subject_kind)
    if not implicated:
        return Localization(
            subject=seed.subject,
            subject_kind=seed.subject_kind or BlameTarget.NODE,
            slice_path=[seed.subject] if seed.subject else [],
            rationale=(
                f"'{seed.subject}' produced the earliest blocking signal and consumed "
                "no artifacts, so the fault is local to it"
            ),
        )

    # Walk the full lineage, nearest first, then reverse to get oldest first.
    chain: list[str] = []
    for artifact_id in implicated:
        for ancestor in trace.lineage(artifact_id):
            if ancestor not in chain:
                chain.append(ancestor)
    oldest_first = list(reversed(chain))

    verdicts: list[ArtifactVerdict] = []
    culprit: Optional[str] = None
    for artifact_id in oldest_first:
        artifact = trace.artifacts.get(artifact_id)
        if artifact is None:
            continue
        declared = None
        if dossier is not None:
            declared = dossier.artifact_type(artifact.type_name)
        if declared is None:
            declared = types.get(artifact.type_name)
        verdict = validate_artifact(artifact, declared=declared)
        verdicts.append(verdict)
        if not verdict.valid and culprit is None:
            culprit = artifact_id

    if culprit is not None:
        artifact = trace.artifacts[culprit]
        problems = next(v.problems for v in verdicts if v.artifact_id == culprit)
        return Localization(
            subject=culprit,
            subject_kind=BlameTarget.ARTIFACT,
            slice_path=chain,
            earliest_anomalous_artifact=culprit,
            verdicts=verdicts,
            contaminated=trace.descendants_of(culprit),
            rationale=(
                f"backward slice from '{seed.subject}' reaches '{culprit}' "
                f"(emitted by '{artifact.producer}'), the earliest artifact failing "
                f"its own contract: {'; '.join(problems)}. Downstream nodes consumed "
                "it and are not at fault"
            ),
        )

    return Localization(
        subject=seed.subject,
        subject_kind=BlameTarget.NODE if seed.subject_kind is BlameTarget.NONE else seed.subject_kind,
        slice_path=chain,
        verdicts=verdicts,
        rationale=(
            f"every artifact upstream of '{seed.subject}' satisfies its own contract "
            f"({len(verdicts)} checked), so the fault is local to '{seed.subject}'"
        ),
    )


def _implicated_artifacts(
    trace: Trace, subject: Optional[str], subject_kind: BlameTarget
) -> list[str]:
    """Artifacts the seed signal points at, nearest-first."""
    if subject is None:
        return []

    if subject_kind is BlameTarget.ARTIFACT and subject in trace.artifacts:
        return [subject]

    if subject_kind is BlameTarget.EDGE and "->" in subject:
        source, _, _target = subject.partition("->")
        return [a.artifact_id for a in trace.artifacts_of(source)]

    # A node: the artifacts it consumed. If it never ran, use what its
    # predecessors emitted; the run's own outputs will not exist.
    consumed: list[str] = []
    for artifact in trace.artifacts.values():
        if artifact.producer == subject:
            consumed.extend(artifact.derived_from)
    if consumed:
        return sorted(set(consumed))

    # Node produced nothing: fall back to everything it could have consumed.
    handoffs = [
        e.artifact_id
        for e in trace.events
        if e.node_id == subject and e.artifact_id and e.kind.value in ("handoff", "handoff_rejected")
    ]
    return sorted(set(a for a in handoffs if a))


__all__ = ["ArtifactVerdict", "Localization", "validate_artifact", "localize"]
