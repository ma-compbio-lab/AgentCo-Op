from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from agentcoop.ir.schema import (
    BudgetSpec,
    EdgeSpec,
    IOContract,
    ModelSpec,
    NodeKind,
    NodeSpec,
    SkillRef,
    TaskSpec,
    WorkflowBlueprint,
)
from agentcoop.runtime.skills import LoadedSkill
from agentcoop.skills.library import SkillLibrary

JsonDict = Dict[str, Any]


class SkillDrivenAssembler:
    """Assembles a WorkflowBlueprint from a meta-skill + per-role agent-skill search."""

    def __init__(
        self,
        library: SkillLibrary,
        max_skills_per_agent: int = 3,
        deduplicate: bool = True,
    ) -> None:
        self._library = library
        self._max_skills = max_skills_per_agent
        self._deduplicate = deduplicate

    def _build_role_query(self, role_spec: JsonDict, task: TaskSpec) -> str:
        skill_tags = role_spec.get("skill_tags", [])
        role_desc = str(role_spec.get("description", ""))
        return " ".join([*[str(t) for t in skill_tags], role_desc, task.description])

    def _resolve_skill_ownership(
        self,
        roles: List[JsonDict],
        task: TaskSpec,
    ) -> Dict[str, str]:
        """For non-shareable skills, determine which role is the best owner.

        Returns a mapping of skill_name -> role_name for non-shareable skills
        that appear as candidates for multiple roles.
        """
        # Collect (skill_name, score, role_name) for all non-shareable candidates
        skill_best_role: Dict[str, Tuple[float, str]] = {}

        for role_spec in roles:
            role_name = str(role_spec.get("role", ""))
            query = self._build_role_query(role_spec, task)
            candidates = self._library.search_agent_skills(
                query, top_k=self._max_skills * 2
            )
            for skill, score in candidates:
                if skill.shareable:
                    continue
                prev = skill_best_role.get(skill.name)
                if prev is None or score > prev[0]:
                    skill_best_role[skill.name] = (score, role_name)

        return {name: role for name, (_, role) in skill_best_role.items()}

    def assemble(
        self,
        *,
        meta_skill: LoadedSkill,
        task: TaskSpec,
        budget: BudgetSpec,
        model: ModelSpec,
        review_model: ModelSpec,
    ) -> WorkflowBlueprint:
        nodes: List[NodeSpec] = []
        edges: List[EdgeSpec] = []

        # Pre-compute ownership for non-shareable skills when deduplicating
        skill_owner: Dict[str, str] = {}
        if self._deduplicate:
            skill_owner = self._resolve_skill_ownership(meta_skill.roles, task)

        for role_spec in meta_skill.roles:
            role_name = str(role_spec.get("role", ""))
            role_desc = str(role_spec.get("description", ""))
            kind_str = str(role_spec.get("kind", "agent"))
            kind = NodeKind(kind_str) if kind_str in {k.value for k in NodeKind} else NodeKind.agent

            node_id = role_name.lower().replace(" ", "_")

            query = self._build_role_query(role_spec, task)
            candidates = self._library.search_agent_skills(
                query, top_k=self._max_skills * 2
            )

            selected_skills: List[LoadedSkill] = []
            for skill, score in candidates:
                if len(selected_skills) >= self._max_skills:
                    break
                if self._deduplicate and not skill.shareable:
                    owner = skill_owner.get(skill.name)
                    if owner is not None and owner != role_name:
                        continue
                selected_skills.append(skill)

            skill_refs = [
                SkillRef(name=s.name, optional=True)
                for s in selected_skills
            ]

            node_model = review_model if kind == NodeKind.evaluator else model

            nodes.append(NodeSpec(
                node_id=node_id,
                kind=kind,
                role=role_name,
                description=role_desc,
                model=node_model,
                system_prompt=meta_skill.body.strip() or None,
                skills=skill_refs,
                io=IOContract(),
            ))

        for edge_spec in meta_skill.edges:
            src = str(edge_spec.get("src", "")).lower().replace(" ", "_")
            dst = str(edge_spec.get("dst", "")).lower().replace(" ", "_")
            edges.append(EdgeSpec(
                edge_id=f"{src}_to_{dst}",
                src=src,
                dst=dst,
            ))

        return WorkflowBlueprint(
            task=task,
            budget=budget,
            base_nodes=nodes,
            base_edges=edges,
            meta={"skill_driven": True, "meta_skill": meta_skill.name},
        )
