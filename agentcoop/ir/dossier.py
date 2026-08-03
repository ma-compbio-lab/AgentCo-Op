"""The Task Evidence Dossier.

v1 accepted a task as ``x = (goal, context, resources, constraints)`` and let
the profiler infer everything else from regexes over the prompt. That is
enough to route GSM8K; it is not enough to justify a workflow.

A dossier makes explicit the things a workflow's structure must be derived
*from*:

* **subgoals** with typed consumes/produces contracts — these are what create
  the requirement evidence for a node existing at all;
* **invariants** that must hold no matter what (hard constraints);
* **preferences** that are directional but deliberately *not* scalarized;
* **evaluator availability** per check level, including honest "unavailable";
* **resource limits** and **human-review conditions**.

If a dossier has a required output artifact that no subgoal produces, that is
a specification fault, and the compiler says so instead of improvising.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentcoop.ir.artifacts import ArtifactType
from agentcoop.ir.checks import CheckLevel


class Direction(str, Enum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvaluatorAvailability(str, Enum):
    """How well we can actually measure a given check level for this task.

    ``UNAVAILABLE`` is a first-class value. Open-ended scientific tasks
    genuinely lack a claim-level oracle, and pretending otherwise is how
    systems end up optimizing a metric that does not measure the goal.
    """

    DETERMINISTIC = "deterministic"   # exact oracle exists
    PARTIAL = "partial"               # necessary but not sufficient checks
    RUBRIC = "rubric"                 # model/human rubric, calibration unknown
    PREFERENCE_ONLY = "preference_only"  # only pairwise expert judgment
    UNAVAILABLE = "unavailable"


class Invariant(BaseModel):
    """A hard constraint. Violating one makes the run invalid, full stop."""

    model_config = ConfigDict(extra="forbid")

    invariant_id: str
    description: str
    #: Name of a registered check implementation. An invariant with no
    #: executable check is itself a specification defect and is reported.
    check: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    level: CheckLevel = CheckLevel.HARD


class Preference(BaseModel):
    """A soft objective. Compared, never summed with the others."""

    model_config = ConfigDict(extra="forbid")

    preference_id: str
    description: str
    #: Utility dimension this preference maps onto.
    dimension: str
    direction: Direction = Direction.MAXIMIZE
    #: Optional hint used only for tie-breaking inside the Pareto set, never
    #: to collapse the vector into a scalar objective.
    weight_hint: Optional[float] = None


class Subgoal(BaseModel):
    """A unit of work with a typed contract.

    The compiler's requirement evidence is derived from these: a node exists
    because some subgoal must be served, and that subgoal declares exactly
    which artifacts must flow in and out.
    """

    model_config = ConfigDict(extra="forbid")

    subgoal_id: str
    description: str
    #: Capability keyword a component must claim (and have certified) to serve
    #: this subgoal.
    required_capability: str
    consumes: list[str] = Field(default_factory=list)   # artifact type names
    produces: list[str] = Field(default_factory=list)   # artifact type names
    #: Facets the produced artifacts must carry, keyed by artifact type name.
    required_output_facets: dict[str, dict[str, str]] = Field(default_factory=dict)
    optional: bool = False
    risk: RiskLevel = RiskLevel.LOW
    #: Minimum certification a component must hold to be bound here.
    #: High-risk subgoals demand more than a passing smoke test.
    min_certification: str = "PROBED"
    #: Whether the output of this subgoal must be independently verified.
    requires_verification: bool = False
    #: Subgoal ids that must complete first, beyond what artifact flow implies.
    depends_on: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_usd: Optional[float] = None
    max_wall_time_s: Optional[float] = None
    max_tokens: Optional[int] = None
    max_component_calls: Optional[int] = None
    max_repair_transactions: int = 5
    #: Budget reserved for shadow validation, so speculative repair testing
    #: cannot consume the entire run budget.
    shadow_budget_fraction: float = 0.25


class HumanReviewCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    description: str
    #: Trigger expression name resolved by the escalation policy.
    trigger: str
    params: dict[str, Any] = Field(default_factory=dict)


class EvidenceChainRequirement(BaseModel):
    """What the final scientific claim must be traceable to."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    description: str
    #: Artifact type names that must appear in the provenance of any claim.
    must_trace_to: list[str] = Field(default_factory=list)
    #: Sensitivity analyses that must have been run for the claim to stand.
    required_sensitivity: list[str] = Field(default_factory=list)


class TaskEvidenceDossier(BaseModel):
    """The compiled, machine-checkable statement of what the task requires."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str
    context: str = ""
    resources: dict[str, Any] = Field(default_factory=dict)

    subgoals: list[Subgoal] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    evidence_chain: list[EvidenceChainRequirement] = Field(default_factory=list)

    #: Artifact types this task must ultimately produce.
    required_outputs: list[str] = Field(default_factory=list)
    #: Artifact types available as inputs at the start of the run.
    provided_inputs: list[str] = Field(default_factory=list)

    #: Declared artifact types for this task, so the compiler can resolve
    #: names without a global registry.
    artifact_types: list[ArtifactType] = Field(default_factory=list)

    evaluators: dict[CheckLevel, EvaluatorAvailability] = Field(default_factory=dict)
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    human_review: list[HumanReviewCondition] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    #: Long-horizon tasks get different repair policies: a failure 40 steps in
    #: cannot simply restart the workflow.
    long_horizon: bool = False
    notes: list[str] = Field(default_factory=list)

    # -- lookups ------------------------------------------------------------

    def subgoal(self, subgoal_id: str) -> Optional[Subgoal]:
        for s in self.subgoals:
            if s.subgoal_id == subgoal_id:
                return s
        return None

    def artifact_type(self, name: str) -> Optional[ArtifactType]:
        for t in self.artifact_types:
            if t.name == name:
                return t
        return None

    def availability(self, level: CheckLevel) -> EvaluatorAvailability:
        return self.evaluators.get(level, EvaluatorAvailability.UNAVAILABLE)

    @property
    def has_scalar_oracle(self) -> bool:
        """True only when a deterministic claim-level evaluator exists.

        Most of the interesting tasks return False here, which is precisely
        why optimization is formulated over a Pareto set rather than a reward.
        """
        return self.availability(CheckLevel.CLAIM) is EvaluatorAvailability.DETERMINISTIC

    @property
    def required_capabilities(self) -> set[str]:
        return {s.required_capability for s in self.subgoals}

    # -- self-consistency ---------------------------------------------------

    def specification_defects(self) -> list[str]:
        """Structural problems with the dossier itself.

        Reported before compilation begins. A task whose required output has
        no producing subgoal is a TASK_SPECIFICATION fault; discovering that
        at runtime, after paying for six components, is pure waste.
        """
        defects: list[str] = []
        produced = {a for s in self.subgoals for a in s.produces}
        consumed = {a for s in self.subgoals for a in s.consumes}
        available = set(self.provided_inputs) | produced

        for out in self.required_outputs:
            if out not in produced:
                defects.append(
                    f"required output '{out}' is not produced by any subgoal"
                )
        for art in sorted(consumed):
            if art not in available:
                defects.append(
                    f"artifact '{art}' is consumed but never produced or provided as input"
                )

        declared_types = {t.name for t in self.artifact_types}
        for art in sorted(produced | consumed | set(self.required_outputs)):
            if declared_types and art not in declared_types:
                defects.append(f"artifact type '{art}' is referenced but not declared")

        ids = [s.subgoal_id for s in self.subgoals]
        for sid in ids:
            if ids.count(sid) > 1:
                defects.append(f"duplicate subgoal id '{sid}'")
                break
        for s in self.subgoals:
            for dep in s.depends_on:
                if dep not in ids:
                    defects.append(f"subgoal '{s.subgoal_id}' depends on unknown '{dep}'")

        for inv in self.invariants:
            if not inv.check:
                defects.append(
                    f"invariant '{inv.invariant_id}' has no executable check "
                    "(an unenforceable hard constraint is a specification defect)"
                )

        for pref in self.preferences:
            if not pref.dimension:
                defects.append(f"preference '{pref.preference_id}' has no utility dimension")

        if _has_dependency_cycle(self.subgoals):
            defects.append("subgoal depends_on graph contains a cycle")

        return defects

    @model_validator(mode="after")
    def _check_ids(self) -> "TaskEvidenceDossier":
        if not self.task_id:
            raise ValueError("dossier requires a task_id")
        return self

    # -- io -----------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TaskEvidenceDossier":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), sort_keys=False),
            encoding="utf-8",
        )


def _has_dependency_cycle(subgoals: list[Subgoal]) -> bool:
    adj = {s.subgoal_id: list(s.depends_on) for s in subgoals}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in adj}

    def visit(node: str) -> bool:
        color[node] = GRAY
        for nxt in adj.get(node, []):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[sid] == WHITE and visit(sid) for sid in list(color))


__all__ = [
    "Direction",
    "RiskLevel",
    "EvaluatorAvailability",
    "Invariant",
    "Preference",
    "Subgoal",
    "ResourceLimits",
    "HumanReviewCondition",
    "EvidenceChainRequirement",
    "TaskEvidenceDossier",
]
