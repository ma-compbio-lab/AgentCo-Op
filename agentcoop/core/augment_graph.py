"""Augment an imported workflow with skill cards, tools, and gates.

Case Study 3 takes an AFlow-imported blueprint (from `aflow_import.py`),
attaches skill/tool bindings, and loads gate definitions from a YAML file
declared in `configs/gates/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from agentcoop.core.schema import GatePolicy, NodeSpec, WorkflowBlueprint
from agentcoop.skills.registry import SkillRegistry


def attach_skills_and_tools(
    blueprint: WorkflowBlueprint,
    *,
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    registry: SkillRegistry | None = None,
) -> WorkflowBlueprint:
    """Attach skill refs (meta or agent skills) and tool skill refs to nodes.

    Matching is intentionally permissive: each node accumulates any skill
    whose tag overlaps the node's role or whose name is explicitly listed.
    """
    skills = list(skills or [])
    tools = list(tools or [])
    if registry is not None:
        known = set(registry.meta) | set(registry.agents)
        skills = [s for s in skills if s in known or True]  # don't drop unknown; they may live in YAML only

    new_nodes: list[NodeSpec] = []
    for node in blueprint.nodes:
        refs = list(node.skill_refs)
        for s in skills:
            if s not in refs:
                refs.append(s)
        tool_policy = dict(node.tool_policy) if node.tool_policy else {}
        if tools:
            tool_policy.setdefault("allowed_tools", [])
            for t in tools:
                if t not in tool_policy["allowed_tools"]:
                    tool_policy["allowed_tools"].append(t)
        new_nodes.append(node.model_copy(update={"skill_refs": refs, "tool_policy": tool_policy}))

    blueprint.nodes = new_nodes
    blueprint.provenance.append(
        f"augment:skills={','.join(skills) or '-'};tools={','.join(tools) or '-'}"
    )
    return blueprint


def load_gate_yaml(path: str | Path) -> list[GatePolicy]:
    """Load a gate YAML (case_study.md §4.5 / §4.6 format) into GatePolicy."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    gates_spec = data.get("gates") or data
    if isinstance(gates_spec, dict):
        items = gates_spec.items()
    else:
        items = ((g["name"], g) for g in gates_spec)
    policies: list[GatePolicy] = []
    for name, spec in items:
        if not isinstance(spec, dict):
            continue
        policies.append(
            GatePolicy(
                name=name,
                trigger=spec.get("trigger", name),
                threshold=spec.get("threshold"),
                action=spec.get("action", "retry_node"),
                max_activations=int(spec.get("max_activations", 1)),
                scope=spec.get("scope", "node"),
                node_ids=list(spec.get("node_ids", [])),
            )
        )
    return policies


def apply_gates(blueprint: WorkflowBlueprint, gates: Iterable[GatePolicy]) -> WorkflowBlueprint:
    existing = {p.name for p in blueprint.gate_policies}
    for g in gates:
        if g.name in existing:
            continue
        blueprint.gate_policies.append(g)
    return blueprint


__all__ = ["attach_skills_and_tools", "load_gate_yaml", "apply_gates"]
