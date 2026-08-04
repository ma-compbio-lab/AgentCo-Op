"""Execution trace with artifact lineage.

The trace is not a log. It is the evidence base that diagnosis reasons over,
so two properties matter more than completeness:

**Lineage must be correct.** Every artifact records the artifact ids it was
derived from. Backward slicing walks that graph to find the earliest artifact
that fails its own validity check, which is what stops a downstream component
being blamed for consuming corrupted input. If ``derived_from`` is wrong,
blame assignment is wrong, and every repair built on it is wrong.

**Ordering must be reproducible.** Events carry a monotonic integer ``step``,
never a wall-clock timestamp, so two traces of the same deterministic workflow
diff cleanly. Wall-clock duration is recorded as cost, never as ordering.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.components.base import InvocationResult
from agentcoop.ir.artifacts import Artifact, lineage_of
from agentcoop.ir.checks import CheckResult


class EventKind(str, Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    NODE_START = "node_start"
    NODE_END = "node_end"
    NODE_SKIPPED = "node_skipped"
    ARTIFACT_EMITTED = "artifact_emitted"
    HANDOFF = "handoff"
    HANDOFF_REJECTED = "handoff_rejected"
    CHECK = "check"
    MERGE = "merge"
    MERGE_REFUSED = "merge_refused"
    GUARD = "guard"
    BUDGET = "budget"
    TERMINATED = "terminated"


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    step: int
    kind: EventKind
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    artifact_id: Optional[str] = None
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    """Everything observed during one execution.

    Field names are fixed by the v2 contract: the evaluation package
    duck-types against ``events`` / ``artifacts`` / ``node_results`` /
    ``lineage`` so it can be written and tested before this module exists.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    events: list[TraceEvent] = Field(default_factory=list)
    #: Every artifact ever recorded, keyed by artifact id.
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    node_results: dict[str, InvocationResult] = Field(default_factory=dict)
    checks: list[CheckResult] = Field(default_factory=list)
    #: Monotonic counter; the only ordering authority in the system.
    step: int = 0

    # -- recording ----------------------------------------------------------

    def _next_step(self) -> int:
        self.step += 1
        return self.step

    def record(
        self,
        kind: EventKind,
        *,
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        detail: str = "",
        **payload: Any,
    ) -> TraceEvent:
        step = self._next_step()
        event = TraceEvent(
            event_id=f"{self.run_id}:e{step:04d}",
            step=step,
            kind=kind,
            node_id=node_id,
            edge_id=edge_id,
            artifact_id=artifact_id,
            detail=detail,
            payload=dict(payload),
        )
        self.events.append(event)
        return event

    def record_artifact(self, artifact: Artifact, *, node_id: Optional[str] = None) -> Artifact:
        stored = artifact.finalize()
        self.artifacts[stored.artifact_id] = stored
        self.record(
            EventKind.ARTIFACT_EMITTED,
            node_id=node_id or stored.producer,
            artifact_id=stored.artifact_id,
            detail=stored.type_name,
            derived_from=list(stored.derived_from),
            facets=dict(stored.facets),
            notes=list(stored.notes),
        )
        return stored

    def record_result(self, node_id: str, result: InvocationResult) -> None:
        self.node_results[node_id] = result

    def record_check(self, result: CheckResult) -> CheckResult:
        self.checks.append(result)
        self.record(
            EventKind.CHECK,
            node_id=result.subject if result.subject_kind == "node" else None,
            detail=f"{result.check_id}={result.status.value}",
            level=result.level.value,
            status=result.status.value,
            subject=result.subject,
            blocking=result.blocking,
        )
        return result

    # -- lineage ------------------------------------------------------------

    def lineage(self, artifact_id: str) -> list[str]:
        """``artifact_id`` plus all transitive ancestors, nearest first."""
        return lineage_of(artifact_id, self.artifacts)

    def producer_of(self, artifact_id: str) -> Optional[str]:
        art = self.artifacts.get(artifact_id)
        return art.producer if art else None

    def artifacts_of(self, node_id: str) -> list[Artifact]:
        """Artifacts emitted by ``node_id``, in a deterministic order."""
        return [
            self.artifacts[k]
            for k in sorted(self.artifacts)
            if self.artifacts[k].producer == node_id
        ]

    def descendants_of(self, artifact_id: str) -> list[str]:
        """Artifacts transitively derived from ``artifact_id``.

        Used to size the blast radius of a corrupted artifact: everything
        downstream is suspect even if it passed its own checks.
        """
        out: list[str] = []
        frontier = [artifact_id]
        seen = {artifact_id}
        while frontier:
            current = frontier.pop(0)
            for key in sorted(self.artifacts):
                art = self.artifacts[key]
                if current in art.derived_from and key not in seen:
                    seen.add(key)
                    out.append(key)
                    frontier.append(key)
        return out

    # -- queries ------------------------------------------------------------

    def events_of(self, kind: EventKind) -> list[TraceEvent]:
        return [e for e in self.events if e.kind == kind]

    def events_for_node(self, node_id: str) -> list[TraceEvent]:
        return [e for e in self.events if e.node_id == node_id]

    def failed_nodes(self) -> list[str]:
        return sorted(nid for nid, r in self.node_results.items() if not r.ok)

    def executed_nodes(self) -> list[str]:
        return sorted(self.node_results)

    def check_report_results(self) -> list[CheckResult]:
        return list(self.checks)

    def snapshot(self) -> "Trace":
        """Deep copy, for shadow validation against a run prefix."""
        return Trace.model_validate(self.model_dump())


__all__ = ["EventKind", "TraceEvent", "Trace"]
