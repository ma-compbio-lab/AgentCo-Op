"""Signals — observations, not conclusions.

The distinction this module enforces is the one v1 collapsed. A signal says
"the unit tests failed" or "this artifact is empty". It does not say why, and
nothing downstream may treat it as if it had. Turning signals into a ranked
set of root causes is :mod:`agentcoop.diagnose.diagnose`'s job, and it needs
the raw observations kept separate to do it.

Each signal carries its provenance so that a diagnosis can be audited, and so
that a signal produced by a model-based judge can be weighted differently from
one produced by a non-zero exit code.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.checks import SignalSource
from agentcoop.ir.faults import BlameTarget


class SignalKind(str, Enum):
    CHECK_FAILURE = "check_failure"
    SCHEMA_VIOLATION = "schema_violation"
    FACET_VIOLATION = "facet_violation"
    HANDOFF_REJECTED = "handoff_rejected"
    TOOL_ERROR = "tool_error"
    ADAPTER_MISSING = "adapter_missing"
    TIMEOUT = "timeout"
    EMPTY_OUTPUT = "empty_output"
    MERGE_REFUSED = "merge_refused"
    BRANCH_DISAGREEMENT = "branch_disagreement"
    MISSING_EVIDENCE = "missing_evidence"
    COST_ANOMALY = "cost_anomaly"
    #: Two nodes chained with no artifact crossing between them.
    NEEDLESS_SERIALIZATION = "needless_serialization"
    PROGRESS_STALL = "progress_stall"
    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Signal(BaseModel):
    """One observation about a run."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    kind: SignalKind
    severity: Severity = Severity.ERROR
    source: SignalSource = SignalSource.DETERMINISTIC
    #: What the observation is *about* — not necessarily what caused it.
    subject: Optional[str] = None
    subject_kind: BlameTarget = BlameTarget.NONE
    detail: str = ""
    #: Step in the trace where this was observed. Used to order signals so the
    #: earliest anomaly can be found without consulting a clock.
    step: int = 0
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity in (Severity.ERROR, Severity.CRITICAL)


class SignalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals: list[Signal] = Field(default_factory=list)

    def add(self, signal: Signal) -> Signal:
        self.signals.append(signal)
        return signal

    def extend(self, signals: list[Signal]) -> None:
        self.signals.extend(signals)

    def of_kind(self, kind: SignalKind) -> list[Signal]:
        return [s for s in self.signals if s.kind == kind]

    def blocking(self) -> list[Signal]:
        return [s for s in self.signals if s.blocking]

    def earliest(self) -> Optional[Signal]:
        """The first blocking observation, by trace step.

        Localization starts here rather than at the last failure, because the
        last failure is usually a consequence.
        """
        blocking = self.blocking()
        if not blocking:
            return None
        return sorted(blocking, key=lambda s: (s.step, s.signal_id))[0]

    def subjects(self) -> list[str]:
        return sorted({s.subject for s in self.signals if s.subject})

    def ids(self) -> list[str]:
        return [s.signal_id for s in self.signals]

    def __len__(self) -> int:
        return len(self.signals)


__all__ = ["SignalKind", "Severity", "Signal", "SignalSet"]
