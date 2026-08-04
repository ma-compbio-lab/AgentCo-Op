"""Patches: cause-specific, pure, and constrained by the fault taxonomy.

Two invariants hold throughout this module.

**Application is pure.** ``apply(patch, workflow)`` returns a *new*
``CompiledWorkflow``; the original is untouched and serves as the rollback
point. Transactional repair is only possible because of this.

**Families are constrained by cause.** Every patch belongs to a family, and a
family is only proposable for fault classes that list it in
``admissible_patches``. A semantic contract violation cannot be answered by
re-rolling a prompt, and an evaluator failure cannot be answered by patching
the thing being evaluated. That constraint lives in
:mod:`agentcoop.ir.faults` and is enforced in :mod:`agentcoop.repair.propose`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.faults import FaultHypothesis, RepairTier
from agentcoop.ir.workflow import (
    Atomic,
    CompiledWorkflow,
    Fallback,
    HumanGate,
    Join,
    Parallel,
    Sequence,
    Verify,
    WorkflowTerm,
    find_term,
    replace_term,
)


class Patch(BaseModel):
    """A proposed change, with the hypothesis that motivates it."""

    model_config = ConfigDict(extra="forbid")

    patch_id: str
    family: str
    tier: RepairTier
    #: Term id the patch acts on.
    target: str
    description: str
    rationale: str
    hypothesis: FaultHypothesis
    params: dict[str, Any] = Field(default_factory=dict)
    #: True when the patch changes no structure, only node configuration.
    config_only: bool = False
    #: True when the patch is terminal: it ends the repair loop rather than
    #: producing a workflow to re-run.
    terminal: bool = False


class PatchError(ValueError):
    """The patch cannot be applied to this workflow."""


#: Applies a patch to a workflow term, returning a replacement term.
TermTransform = Callable[[WorkflowTerm, Patch], WorkflowTerm]


# ---------------------------------------------------------------------------
# Term transforms
# ---------------------------------------------------------------------------


def _require_atomic(term: WorkflowTerm, family: str) -> Atomic:
    if not isinstance(term, Atomic):
        raise PatchError(
            f"patch family '{family}' targets a component node, but "
            f"'{term.term_id}' is a {term.kind} term"
        )
    return term


def _config_patch(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    """Merge params into the node's config without touching structure."""
    atom = _require_atomic(term, patch.family)
    config = {**atom.config, **patch.params}
    return atom.model_copy(update={"config": config})


def _retry(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    atom = _require_atomic(term, patch.family)
    attempts = int(atom.config.get("max_retries", 0)) + int(patch.params.get("attempts", 1))
    return atom.model_copy(update={"config": {**atom.config, "max_retries": attempts}})


def _insert_adapter(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    """Put a converter in front of the node that could not read its input."""
    atom = _require_atomic(term, patch.family)
    converter = patch.params.get("converter")
    if not converter:
        raise PatchError("insert_adapter requires a 'converter' parameter")
    adapter = Atomic(
        component=str(converter),
        subgoal_id=atom.subgoal_id,
        config={"role": "adapter", **patch.params.get("config", {})},
    ).ensure_ids()
    return Sequence(children_terms=[adapter, atom]).ensure_ids()


def _replace_component(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    atom = _require_atomic(term, patch.family)
    replacement = patch.params.get("component")
    if not replacement:
        raise PatchError("replace_component requires a 'component' parameter")
    return Atomic(
        component=str(replacement), subgoal_id=atom.subgoal_id, config=dict(atom.config)
    ).ensure_ids()


def _add_verifier(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    verifier = patch.params.get("verifier")
    if not verifier:
        raise PatchError(f"{patch.family} requires a 'verifier' parameter")
    if isinstance(term, Verify) and term.verifier == verifier:
        raise PatchError(f"verifier '{verifier}' is already attached to '{term.term_id}'")
    return Verify(body=term, verifier=str(verifier)).ensure_ids()


def _define_merge(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    merge = patch.params.get("merge")
    if not merge:
        raise PatchError("define_merge requires a 'merge' parameter")
    if isinstance(term, Join):
        return term.model_copy(update={"merge": str(merge), "term_id": ""}).ensure_ids()
    if isinstance(term, Parallel):
        return Join(branches=list(term.branches), merge=str(merge)).ensure_ids()
    raise PatchError(
        f"define_merge targets a join or parallel term, not {term.kind}"
    )


def _serialize_branches(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    if isinstance(term, Parallel):
        return Sequence(children_terms=list(term.branches)).ensure_ids()
    if isinstance(term, Join):
        return Sequence(children_terms=list(term.branches)).ensure_ids()
    raise PatchError(f"serialize_branches targets a parallel or join, not {term.kind}")


def _parallelize(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    if not isinstance(term, Sequence):
        raise PatchError(f"parallelize targets a sequence, not {term.kind}")
    return Parallel(
        branches=list(term.children_terms),
        independence_ref=str(patch.params.get("independence_ref", "")),
    ).ensure_ids()


def _add_fallback(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    alternate = patch.params.get("component")
    if not alternate:
        raise PatchError("add_fallback requires a 'component' parameter")
    atom = _require_atomic(term, patch.family)
    return Fallback(
        primary=atom,
        alternate=Atomic(component=str(alternate), subgoal_id=atom.subgoal_id).ensure_ids(),
    ).ensure_ids()


def _add_human_gate(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    condition = patch.params.get("condition")
    if not condition:
        raise PatchError("add_human_gate requires a 'condition' parameter")
    return HumanGate(body=term, condition=str(condition)).ensure_ids()


def _add_specialist(term: WorkflowTerm, patch: Patch) -> WorkflowTerm:
    """Append a second component after the one that fell short."""
    component = patch.params.get("component")
    if not component:
        raise PatchError("add_specialist requires a 'component' parameter")
    atom = _require_atomic(term, patch.family)
    specialist = Atomic(
        component=str(component), subgoal_id=atom.subgoal_id
    ).ensure_ids()
    return Sequence(children_terms=[atom, specialist]).ensure_ids()


class PatchFamily(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    tier: RepairTier
    description: str
    transform: Optional[TermTransform] = Field(default=None, exclude=True)
    #: Parameters that must be supplied for the patch to be applicable.
    required_params: list[str] = Field(default_factory=list)
    config_only: bool = False
    terminal: bool = False


PATCH_FAMILIES: dict[str, PatchFamily] = {
    f.name: f
    for f in [
        PatchFamily(
            name="retry_node",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Re-run the node. Only valid for transient tool failures.",
            transform=_retry,
            config_only=True,
        ),
        PatchFamily(
            name="backoff_retry",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Re-run with a delay, for rate limits and transient outages.",
            transform=_retry,
            config_only=True,
        ),
        PatchFamily(
            name="insert_adapter",
            tier=RepairTier.CONTRACT_REPAIR,
            description=(
                "Insert an executable converter ahead of a node whose input "
                "arrived in a namespace, unit, or organism it cannot read."
            ),
            transform=_insert_adapter,
            required_params=["converter"],
        ),
        PatchFamily(
            name="tighten_contract",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Declare the facet the producer left unstated.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="replace_component",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Bind a different certified component to the subgoal.",
            transform=_replace_component,
            required_params=["component"],
        ),
        PatchFamily(
            name="add_verifier",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Attach a verifier where an unchecked output was consumed.",
            transform=_add_verifier,
            required_params=["verifier"],
        ),
        PatchFamily(
            name="define_merge",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Declare how concurrent branch outputs combine.",
            transform=_define_merge,
            required_params=["merge"],
        ),
        PatchFamily(
            name="parallelize",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Run independent work concurrently.",
            transform=_parallelize,
        ),
        PatchFamily(
            name="serialize_branches",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Order branches that turned out not to be independent.",
            transform=_serialize_branches,
        ),
        PatchFamily(
            name="isolate_state",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Give each branch its own scratch state.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="invalidate_cache",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Discard cached results that may be stale.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="pin_environment",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Pin the dependency versions the component needs.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="isolate_environment",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Move the component into its own container image.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="raise_resource_limit",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Increase the memory, time, or CPU ceiling.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="retune_config",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Adjust thresholds, seeds, or tool policy.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="rewrite_prompt",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Revise the instruction given to a model-backed node.",
            transform=_config_patch,
            config_only=True,
        ),
        PatchFamily(
            name="add_fallback",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Add an alternate arm for a component with a real failure rate.",
            transform=_add_fallback,
            required_params=["component"],
        ),
        PatchFamily(
            name="add_specialist",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Add a component that covers what the bound one cannot.",
            transform=_add_specialist,
            required_params=["component"],
        ),
        PatchFamily(
            name="decompose_subgoal",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Split a subgoal no single component can serve.",
            transform=None,
            terminal=True,
        ),
        PatchFamily(
            name="diversify_evaluator",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Add an independent evaluator when the judge is suspect.",
            transform=_add_verifier,
            required_params=["verifier"],
        ),
        PatchFamily(
            name="add_deterministic_check",
            tier=RepairTier.LOCAL_OPTIMIZATION,
            description="Replace a model-based judgement with a computable one.",
            transform=_add_verifier,
            required_params=["verifier"],
        ),
        PatchFamily(
            name="add_human_gate",
            tier=RepairTier.CONTRACT_REPAIR,
            description="Suspend for human review.",
            transform=_add_human_gate,
            required_params=["condition"],
        ),
        PatchFamily(
            name="add_information_edge",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Route information the graph never supplied.",
            transform=None,
            terminal=True,
        ),
        PatchFamily(
            name="report_uncertainty",
            tier=RepairTier.CONTRACT_REPAIR,
            description=(
                "Stop and report that the data do not determine the answer. "
                "Patching further would manufacture false confidence."
            ),
            transform=None,
            terminal=True,
        ),
        PatchFamily(
            name="clarify_specification",
            tier=RepairTier.GLOBAL_REDESIGN,
            description="Escalate: the task specification itself is defective.",
            transform=None,
            terminal=True,
        ),
    ]
}


def apply(patch: Patch, workflow: CompiledWorkflow) -> CompiledWorkflow:
    """Apply a patch, returning a NEW workflow. The original is the rollback point."""
    family = PATCH_FAMILIES.get(patch.family)
    if family is None:
        raise PatchError(f"unknown patch family '{patch.family}'")
    if family.terminal or family.transform is None:
        raise PatchError(
            f"patch family '{patch.family}' is terminal and produces no new workflow"
        )
    missing = [p for p in family.required_params if p not in patch.params]
    if missing:
        raise PatchError(
            f"patch family '{patch.family}' requires parameter(s): {', '.join(missing)}"
        )

    target = find_term(workflow.term, patch.target)
    if target is None:
        raise PatchError(f"target term '{patch.target}' is not in the workflow")

    replacement = family.transform(target, patch)
    new_term, changed = replace_term(workflow.term, patch.target, replacement)
    if not changed:
        raise PatchError(f"failed to replace term '{patch.target}'")
    return workflow.with_term(
        new_term, note=f"{patch.family}@{patch.target}: {patch.description}"
    )


__all__ = ["Patch", "PatchError", "PatchFamily", "PATCH_FAMILIES", "apply"]
