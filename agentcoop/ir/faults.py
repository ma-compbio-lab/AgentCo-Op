"""Fault taxonomy for heterogeneous agent workflows.

The point of a taxonomy is that a *symptom* is not a *cause*. A failing unit
test can be caused by a bad prompt, bad generated code, a malformed upstream
artifact, a broken dependency, an incorrect grader, or a workflow whose
topology never produced the information the node needed. Mapping the symptom
straight onto an action ("test failed -> retry the node") is the mistake this
module exists to prevent.

Each fault class declares:

* where it can be *localized* to (node, edge, artifact, environment, contract);
* which repair tier is appropriate (contract repair, local optimization,
  global redesign);
* which patch families are admissible responses.

Diagnosis produces a ranked hypothesis set over these classes; the repair
planner is only allowed to propose patches admissible for the hypothesis it
is acting on.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RepairTier(str, Enum):
    """Three distinct processes that the v1 system conflated into one.

    ``CONTRACT_REPAIR`` restores executability and hard invariants. It says
    nothing about quality. ``LOCAL_OPTIMIZATION`` improves utility once the
    contract holds. ``GLOBAL_REDESIGN`` changes how the task is decomposed at
    all, and is reserved for structural faults that keep recurring.
    """

    CONTRACT_REPAIR = "contract_repair"
    LOCAL_OPTIMIZATION = "local_optimization"
    GLOBAL_REDESIGN = "global_redesign"


class BlameTarget(str, Enum):
    NODE = "node"
    EDGE = "edge"
    ARTIFACT = "artifact"
    COMPONENT = "component"
    ENVIRONMENT = "environment"
    CONTRACT = "contract"
    EVALUATOR = "evaluator"
    TASK_SPEC = "task_spec"
    TOPOLOGY = "topology"
    NONE = "none"


class FaultClass(str, Enum):
    """Root-cause classes. Deliberately small and mutually distinguishable."""

    #: The task specification itself is ambiguous, contradictory, or missing
    #: an invariant. No amount of node-level repair fixes this.
    TASK_SPECIFICATION = "task_specification"
    #: A component was asked to do something it is not capable of. Detected
    #: by contrast between the subgoal contract and the capability card.
    CAPABILITY_MISMATCH = "capability_mismatch"
    #: The component can do the job but was configured/prompted badly.
    CONFIGURATION = "configuration"
    #: The underlying tool, API, or backend failed (non-zero exit, timeout).
    TOOL_FAILURE = "tool_failure"
    #: Dependency, image, CUDA, version, or resource-limit problem.
    ENVIRONMENT = "environment"
    #: An artifact crossing an edge violated its structural schema or its
    #: semantic facets (namespace, organism, units, normalization).
    ARTIFACT_CONTRACT = "artifact_contract"
    #: Memory or cached state that is stale, contaminated, or leaked across
    #: what should have been independent branches.
    STALE_STATE = "stale_state"
    #: The graph itself is wrong: missing verifier, needless serialization,
    #: a join with no defined merge algebra, an absent feedback path.
    COORDINATION = "coordination"
    #: The evaluator/grader is itself broken, miscalibrated, or judging its
    #: own generator's output.
    EVALUATOR_FAILURE = "evaluator_failure"
    #: The signal is real but the answer is genuinely uncertain given the
    #: available data. The correct action is to report uncertainty, not patch.
    IRREDUCIBLE_UNCERTAINTY = "irreducible_uncertainty"


class FaultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_class: FaultClass
    description: str
    blame_targets: list[BlameTarget]
    tier: RepairTier
    #: Patch family names admissible for this fault class. Enforced by the
    #: repair planner so a namespace mismatch can never be "fixed" by
    #: raising the temperature.
    admissible_patches: list[str] = Field(default_factory=list)
    #: If true, automated repair is not appropriate; escalate instead.
    escalate: bool = False


FAULT_TAXONOMY: dict[FaultClass, FaultSpec] = {
    FaultClass.TASK_SPECIFICATION: FaultSpec(
        fault_class=FaultClass.TASK_SPECIFICATION,
        description=(
            "The dossier is under-specified or self-contradictory: a required "
            "artifact has no producer, an invariant conflicts with a preference, "
            "or a subgoal has no measurable completion condition."
        ),
        blame_targets=[BlameTarget.TASK_SPEC],
        tier=RepairTier.GLOBAL_REDESIGN,
        admissible_patches=["clarify_specification", "add_human_gate"],
        escalate=True,
    ),
    FaultClass.CAPABILITY_MISMATCH: FaultSpec(
        fault_class=FaultClass.CAPABILITY_MISMATCH,
        description=(
            "The bound component cannot serve the subgoal: its certified "
            "capabilities, input contract, or empirical probe record do not "
            "cover what the subgoal requires."
        ),
        blame_targets=[BlameTarget.NODE, BlameTarget.COMPONENT],
        tier=RepairTier.LOCAL_OPTIMIZATION,
        admissible_patches=["replace_component", "decompose_subgoal", "add_specialist"],
    ),
    FaultClass.CONFIGURATION: FaultSpec(
        fault_class=FaultClass.CONFIGURATION,
        description=(
            "Correct component, wrong parameters: prompt, threshold, seed, "
            "model choice, or tool policy."
        ),
        blame_targets=[BlameTarget.NODE],
        tier=RepairTier.LOCAL_OPTIMIZATION,
        admissible_patches=["retune_config", "rewrite_prompt", "retry_node"],
    ),
    FaultClass.TOOL_FAILURE: FaultSpec(
        fault_class=FaultClass.TOOL_FAILURE,
        description="External tool or API returned an error, timed out, or was rate limited.",
        blame_targets=[BlameTarget.NODE, BlameTarget.COMPONENT],
        tier=RepairTier.CONTRACT_REPAIR,
        admissible_patches=["retry_node", "backoff_retry", "replace_component", "add_fallback"],
    ),
    FaultClass.ENVIRONMENT: FaultSpec(
        fault_class=FaultClass.ENVIRONMENT,
        description=(
            "Dependency conflict, missing system library, CUDA/driver mismatch, "
            "or resource limit exceeded."
        ),
        blame_targets=[BlameTarget.ENVIRONMENT, BlameTarget.COMPONENT],
        tier=RepairTier.CONTRACT_REPAIR,
        admissible_patches=["pin_environment", "isolate_environment", "raise_resource_limit"],
    ),
    FaultClass.ARTIFACT_CONTRACT: FaultSpec(
        fault_class=FaultClass.ARTIFACT_CONTRACT,
        description=(
            "An artifact violated its structural schema or its semantic facets "
            "(identifier namespace, organism, units, normalization) at an edge."
        ),
        blame_targets=[BlameTarget.EDGE, BlameTarget.ARTIFACT, BlameTarget.CONTRACT],
        tier=RepairTier.CONTRACT_REPAIR,
        admissible_patches=["insert_adapter", "tighten_contract", "replace_component"],
    ),
    FaultClass.STALE_STATE: FaultSpec(
        fault_class=FaultClass.STALE_STATE,
        description=(
            "Cached or shared state leaked between steps or branches that were "
            "assumed independent, producing spurious agreement or stale results."
        ),
        blame_targets=[BlameTarget.EDGE, BlameTarget.TOPOLOGY],
        tier=RepairTier.CONTRACT_REPAIR,
        admissible_patches=["isolate_state", "invalidate_cache", "serialize_branches"],
    ),
    FaultClass.COORDINATION: FaultSpec(
        fault_class=FaultClass.COORDINATION,
        description=(
            "The topology is wrong: a verifier is missing, a join has no merge "
            "algebra, independent work is needlessly serialized, or a needed "
            "information path does not exist."
        ),
        blame_targets=[BlameTarget.TOPOLOGY, BlameTarget.EDGE],
        tier=RepairTier.GLOBAL_REDESIGN,
        admissible_patches=[
            "add_verifier",
            "define_merge",
            "parallelize",
            "serialize_branches",
            "add_information_edge",
        ],
    ),
    FaultClass.EVALUATOR_FAILURE: FaultSpec(
        fault_class=FaultClass.EVALUATOR_FAILURE,
        description=(
            "The check that fired is itself unreliable: miscalibrated rubric, "
            "judge sharing a generator's bias, or a grader with a broken oracle."
        ),
        blame_targets=[BlameTarget.EVALUATOR],
        tier=RepairTier.LOCAL_OPTIMIZATION,
        admissible_patches=["diversify_evaluator", "add_deterministic_check", "add_human_gate"],
    ),
    FaultClass.IRREDUCIBLE_UNCERTAINTY: FaultSpec(
        fault_class=FaultClass.IRREDUCIBLE_UNCERTAINTY,
        description=(
            "The data genuinely do not determine the answer. Patching further "
            "would manufacture false confidence."
        ),
        blame_targets=[BlameTarget.NONE],
        tier=RepairTier.CONTRACT_REPAIR,
        admissible_patches=["report_uncertainty", "add_human_gate"],
        escalate=True,
    ),
}


class FaultHypothesis(BaseModel):
    """One candidate root cause with a calibrated belief."""

    model_config = ConfigDict(extra="forbid")

    fault_class: FaultClass
    probability: float = Field(ge=0.0, le=1.0)
    blame_target: BlameTarget = BlameTarget.NONE
    #: Identifier of the node / edge / artifact held responsible.
    subject: Optional[str] = None
    rationale: str = ""
    #: Check ids and signal ids that support this hypothesis.
    supporting_signals: list[str] = Field(default_factory=list)
    #: Signals that argue against it — retained so the diagnosis is auditable.
    contradicting_signals: list[str] = Field(default_factory=list)

    @property
    def spec(self) -> FaultSpec:
        return FAULT_TAXONOMY[self.fault_class]

    @property
    def tier(self) -> RepairTier:
        return self.spec.tier

    def admits(self, patch_family: str) -> bool:
        return patch_family in self.spec.admissible_patches


class Diagnosis(BaseModel):
    """A ranked hypothesis set. Never a single natural-language conclusion."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[FaultHypothesis] = Field(default_factory=list)
    #: Artifact/node the localizer identified as the earliest anomaly.
    localized_to: Optional[str] = None
    localized_kind: BlameTarget = BlameTarget.NONE
    #: Signals that triggered diagnosis in the first place.
    trigger_signals: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def top(self) -> Optional[FaultHypothesis]:
        return self.hypotheses[0] if self.hypotheses else None

    def top_k(self, k: int) -> list[FaultHypothesis]:
        return self.hypotheses[:k]

    @property
    def entropy(self) -> float:
        """Shannon entropy over the hypothesis set, in bits.

        High entropy means the diagnosis is not confident. The repair policy
        uses this to decide between acting and gathering more evidence
        (re-running with more instrumentation) instead of guessing.
        """
        import math

        ps = [h.probability for h in self.hypotheses if h.probability > 0]
        total = sum(ps)
        if total <= 0:
            return 0.0
        return -sum((p / total) * math.log2(p / total) for p in ps)

    def normalized(self) -> "Diagnosis":
        total = sum(h.probability for h in self.hypotheses)
        if total <= 0:
            return self
        ranked = sorted(
            (h.model_copy(update={"probability": h.probability / total}) for h in self.hypotheses),
            key=lambda h: -h.probability,
        )
        return self.model_copy(update={"hypotheses": ranked})


__all__ = [
    "RepairTier",
    "BlameTarget",
    "FaultClass",
    "FaultSpec",
    "FAULT_TAXONOMY",
    "FaultHypothesis",
    "Diagnosis",
]
