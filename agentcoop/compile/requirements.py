"""Requirement planning: what work the dossier actually demands.

This is where *requirement evidence* comes from. A node exists in a compiled
workflow because some subgoal must be served, and that subgoal declares
exactly which artifacts flow in and out. Nothing here consults a component
library or a model — the plan is a mechanical consequence of the task
specification, which is why the evidence it yields is `DERIVED` and
load-bearing.

Ordering is induced by artifact flow first (A produces what B consumes) and by
explicit `depends_on` second. If those two disagree, or either introduces a
cycle, that is a specification defect and is reported rather than resolved by
guesswork.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.dossier import Subgoal, TaskEvidenceDossier


class RequirementPlan(BaseModel):
    """The subgoal DAG, plus everything the compiler needs to walk it."""

    model_config = ConfigDict(extra="forbid")

    #: Topologically sorted subgoal ids.
    order: list[str] = Field(default_factory=list)
    #: Subgoal ids at the same depth — candidates for a Parallel term.
    layers: list[list[str]] = Field(default_factory=list)
    #: artifact type -> subgoal ids producing it.
    producers: dict[str, list[str]] = Field(default_factory=dict)
    #: artifact type -> subgoal ids consuming it.
    consumers: dict[str, list[str]] = Field(default_factory=dict)
    #: subgoal id -> subgoal ids it must follow.
    predecessors: dict[str, list[str]] = Field(default_factory=dict)
    defects: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.defects

    def independent_groups(self) -> list[list[str]]:
        """Layers with more than one subgoal — the only Parallel candidates.

        Being in the same layer is *necessary* for parallelism, not
        sufficient: `can_parallelize` still has to find a positive reason.
        """
        return [layer for layer in self.layers if len(layer) > 1]

    def subgoals_producing(self, artifact_type: str) -> list[str]:
        return self.producers.get(artifact_type, [])


def plan_requirements(dossier: TaskEvidenceDossier) -> RequirementPlan:
    """Derive the subgoal DAG from the dossier alone."""
    plan = RequirementPlan(defects=list(dossier.specification_defects()))

    subgoals: dict[str, Subgoal] = {s.subgoal_id: s for s in dossier.subgoals}
    for subgoal in dossier.subgoals:
        for artifact in subgoal.produces:
            plan.producers.setdefault(artifact, []).append(subgoal.subgoal_id)
        for artifact in subgoal.consumes:
            plan.consumers.setdefault(artifact, []).append(subgoal.subgoal_id)
    for key in plan.producers:
        plan.producers[key].sort()
    for key in plan.consumers:
        plan.consumers[key].sort()

    provided = set(dossier.provided_inputs)
    predecessors: dict[str, set[str]] = {sid: set() for sid in subgoals}
    for subgoal in dossier.subgoals:
        for artifact in subgoal.consumes:
            if artifact in provided:
                continue
            for producer in plan.producers.get(artifact, []):
                if producer != subgoal.subgoal_id:
                    predecessors[subgoal.subgoal_id].add(producer)
        for dep in subgoal.depends_on:
            if dep in subgoals:
                predecessors[subgoal.subgoal_id].add(dep)

    plan.predecessors = {k: sorted(v) for k, v in sorted(predecessors.items())}

    order, layers, cycle = _topological_layers(plan.predecessors)
    if cycle:
        plan.defects.append(
            "subgoal dependency graph contains a cycle involving: " + ", ".join(cycle)
        )
    plan.order = order
    plan.layers = layers
    return plan


def _topological_layers(
    predecessors: dict[str, list[str]]
) -> tuple[list[str], list[list[str]], list[str]]:
    """Kahn's algorithm, grouped by depth. Returns (order, layers, cycle)."""
    remaining = {k: set(v) for k, v in predecessors.items()}
    order: list[str] = []
    layers: list[list[str]] = []

    while remaining:
        ready = sorted(k for k, deps in remaining.items() if not deps)
        if not ready:
            return order, layers, sorted(remaining)
        layers.append(ready)
        order.extend(ready)
        for node in ready:
            del remaining[node]
        for deps in remaining.values():
            deps.difference_update(ready)

    return order, layers, []


def unserved_subgoals(
    plan: RequirementPlan, bound: dict[str, Optional[str]]
) -> list[str]:
    """Subgoal ids with no component bound. Reported, never silently dropped."""
    return sorted(sid for sid in plan.order if not bound.get(sid))


__all__ = ["RequirementPlan", "plan_requirements", "unserved_subgoals"]
