"""Check results — the atomic unit of evidence in AgentCo-Op.

Every assertion the system makes about a component, an artifact, a workflow,
or a claim is recorded as a :class:`CheckResult`. Checks carry the level of
the evaluation contract they belong to, a machine-readable status, and the
provenance of the signal that produced them.

Nothing in AgentCo-Op is allowed to assert "this worked" without emitting a
check. That is what makes design decisions and repairs auditable rather than
asserted by an LLM.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CheckLevel(str, Enum):
    """The six strata of the evaluation contract stack.

    The stack exists because open-ended scientific tasks have no single
    scalar metric. Different strata have different availability: ``HARD`` is
    almost always computable, ``CLAIM`` and ``PREFERENCE`` frequently are not.
    A workflow is never scored by collapsing these into one number.
    """

    HARD = "hard"              # exit status, schema, statistical preconditions
    ARTIFACT = "artifact"      # intermediate artifact validity, coverage, provenance
    PROCESS = "process"        # required analysis steps performed, sanity checks run
    CLAIM = "claim"            # conclusion supported by data / literature / tools
    PREFERENCE = "preference"  # expert pairwise preference among admissible results
    RESOURCE = "resource"      # cost, time, memory, API risk, reproducibility


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    #: The check could not be evaluated. This is *not* a pass. Systems that
    #: silently treat unavailable evaluators as success are the reason
    #: "the workflow ran to completion" gets mistaken for "the result is good".
    UNAVAILABLE = "unavailable"
    #: Evaluated, but the evaluator itself is not trusted enough to decide.
    INCONCLUSIVE = "inconclusive"


class SignalSource(str, Enum):
    """Where a check's evidence came from. Used to weight trust and to detect
    reviewer/generator correlation (an LLM judging its own output)."""

    DETERMINISTIC = "deterministic"   # exit code, schema validation, assertion
    STATISTICAL = "statistical"       # resampling, sensitivity, negative control
    TOOL = "tool"                     # external database / analysis tool
    LITERATURE = "literature"         # retrieved reference
    LLM_JUDGE = "llm_judge"           # model-based rubric
    HUMAN = "human"                   # expert annotation
    HISTORICAL = "historical"         # prior run statistics


class CheckResult(BaseModel):
    """One evaluated assertion.

    ``score`` is optional and intentionally *not* comparable across levels:
    a 0.9 artifact-coverage score and a 0.9 claim-support score do not mean
    the same thing and must never be averaged together.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    level: CheckLevel
    status: CheckStatus
    source: SignalSource = SignalSource.DETERMINISTIC
    summary: str = ""
    score: Optional[float] = None
    #: Node / artifact / edge this check is about. Enables blame assignment.
    subject: Optional[str] = None
    subject_kind: Literal["node", "edge", "artifact", "workflow", "claim", "component"] = "workflow"
    #: Raw evidence retained so a reviewer can re-derive the verdict.
    evidence: dict[str, Any] = Field(default_factory=dict)
    #: Whether failing this check violates a hard invariant of the task.
    blocking: bool = False

    @property
    def ok(self) -> bool:
        return self.status in (CheckStatus.PASS, CheckStatus.WARN)

    @property
    def decided(self) -> bool:
        """True when the check actually reached a verdict."""
        return self.status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN)


class CheckReport(BaseModel):
    """A bundle of checks with non-collapsing aggregation helpers."""

    model_config = ConfigDict(extra="forbid")

    results: list[CheckResult] = Field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def extend(self, results: list[CheckResult]) -> None:
        self.results.extend(results)

    def by_level(self, level: CheckLevel) -> list[CheckResult]:
        return [r for r in self.results if r.level == level]

    def failures(self, *, blocking_only: bool = False) -> list[CheckResult]:
        out = [r for r in self.results if r.status == CheckStatus.FAIL]
        return [r for r in out if r.blocking] if blocking_only else out

    def unavailable(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == CheckStatus.UNAVAILABLE]

    @property
    def hard_constraints_satisfied(self) -> bool:
        """Hard invariants hold iff no blocking check failed.

        Unavailable checks do not satisfy a hard constraint — they are
        reported separately as coverage gaps.
        """
        return not any(r.blocking and r.status == CheckStatus.FAIL for r in self.results)

    def coverage(self, level: CheckLevel) -> float:
        """Fraction of checks at ``level`` that actually reached a verdict."""
        at_level = self.by_level(level)
        if not at_level:
            return 0.0
        return sum(1 for r in at_level if r.decided) / len(at_level)

    def pass_rate(self, level: CheckLevel) -> Optional[float]:
        """Pass rate among *decided* checks at ``level``; ``None`` if none decided."""
        decided = [r for r in self.by_level(level) if r.decided]
        if not decided:
            return None
        return sum(1 for r in decided if r.ok) / len(decided)

    def subjects_with_failures(self) -> set[str]:
        return {r.subject for r in self.failures() if r.subject}


__all__ = [
    "CheckLevel",
    "CheckStatus",
    "SignalSource",
    "CheckResult",
    "CheckReport",
]
