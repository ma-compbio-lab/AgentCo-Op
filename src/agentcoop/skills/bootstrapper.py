from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from agentcoop.ir.schema import BudgetSpec, ModelSpec, NodeKind, TaskSpec
from agentcoop.workflows.design import BlueprintCompilerConfig, WorkflowPattern
from agentcoop.workflows.patterns import PatternBuildContext, build_pattern_blueprint

JsonDict = Dict[str, Any]

_PATTERN_TAGS: Dict[str, Dict[str, List[str]]] = {
    WorkflowPattern.direct_answer.value: {
        "domain_tags": ["qa", "knowledge", "reasoning"],
        "capability_tags": ["direct_answer", "choice", "review"],
    },
    WorkflowPattern.reason_execute_select.value: {
        "domain_tags": ["math", "symbolic_reasoning"],
        "capability_tags": ["reasoning", "tool_execution", "verification", "selection"],
    },
    WorkflowPattern.code_generate_test_repair.value: {
        "domain_tags": ["coding", "program_synthesis"],
        "capability_tags": ["code_generation", "testing", "repair", "execution"],
    },
    WorkflowPattern.repo_transfer_validate.value: {
        "domain_tags": ["scientific_transfer", "repo_transfer"],
        "capability_tags": ["repo_search", "artifact_validation", "transfer"],
    },
    WorkflowPattern.closed_loop_design_validate.value: {
        "domain_tags": ["panel_design", "design_optimization"],
        "capability_tags": ["closed_loop", "optimization", "validation"],
    },
    WorkflowPattern.specialist_assembly.value: {
        "domain_tags": ["specialist_collaboration", "multi_agent_science"],
        "capability_tags": ["specialist", "collaboration", "integration"],
    },
}


_DUMMY_TOOL_NODE: JsonDict = {
    "meta": {"direct_sandbox_handler": True},
    "sandbox": {
        "sandbox_id": "probe",
        "image": "python:3.11-slim",
    },
}

_PATTERN_TOOL_OVERRIDES: Dict[str, JsonDict] = {
    WorkflowPattern.reason_execute_select.value: {
        "tool_nodes": {"program_exec": _DUMMY_TOOL_NODE},
    },
    WorkflowPattern.code_generate_test_repair.value: {
        "tool_nodes": {
            "test_runner": _DUMMY_TOOL_NODE,
            "public_test_runner": _DUMMY_TOOL_NODE,
        },
    },
    WorkflowPattern.repo_transfer_validate.value: {
        "tool_nodes": {"repo_runner": _DUMMY_TOOL_NODE},
    },
    WorkflowPattern.closed_loop_design_validate.value: {
        "tool_nodes": {
            "design_runner": _DUMMY_TOOL_NODE,
            "validation_runner": _DUMMY_TOOL_NODE,
        },
    },
    WorkflowPattern.specialist_assembly.value: {
        "tool_nodes": {
            "specialist_a": _DUMMY_TOOL_NODE,
            "specialist_b": _DUMMY_TOOL_NODE,
        },
    },
}


class SkillBootstrapper:
    """Generates meta-skill SKILL.md files from existing workflow patterns."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)

    def bootstrap_all(self) -> List[Path]:
        generated: List[Path] = []

        for pattern_id, tags in _PATTERN_TAGS.items():
            overrides = _PATTERN_TOOL_OVERRIDES.get(pattern_id, {})
            pattern_overrides: JsonDict = {pattern_id: overrides} if overrides else {}
            design = BlueprintCompilerConfig(pattern_overrides=pattern_overrides)
            dummy_ctx = PatternBuildContext(
                task=TaskSpec(task_id="probe", title="probe", description="probe"),
                budget=BudgetSpec(),
                meta={},
                model=ModelSpec(provider="openai", name="gpt-4o-mini"),
                review_model=ModelSpec(provider="openai", name="gpt-4o-mini"),
                design=design,
                resolved_config={},
            )

            try:
                blueprint = build_pattern_blueprint(pattern_id, dummy_ctx)
            except Exception:
                continue

            roles = []
            for node in blueprint.all_nodes():
                if node.kind not in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
                    continue
                role_tags = list(tags["capability_tags"][:2])
                if node.kind == NodeKind.evaluator:
                    role_tags.append("evaluation")
                elif node.kind == NodeKind.router:
                    role_tags.append("routing")
                roles.append({
                    "role": node.role,
                    "description": node.description or f"{node.role} in {pattern_id}",
                    "skill_tags": role_tags,
                    "kind": node.kind.value,
                })

            edges = []
            for edge in blueprint.base_edges:
                edges.append({"src": edge.src, "dst": edge.dst})

            meta_skill = {
                "name": pattern_id,
                "type": "meta-skill",
                "description": f"Workflow topology from {pattern_id} pattern",
                "domain_tags": tags["domain_tags"],
                "capability_tags": tags["capability_tags"],
                "roles": roles,
                "edges": edges,
            }

            skill_dir = self.output_dir / pattern_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
            frontmatter = yaml.dump(meta_skill, default_flow_style=False, sort_keys=False)
            body = f"Auto-generated meta-skill from the {pattern_id} workflow pattern."
            skill_path.write_text(f"---\n{frontmatter}---\n\n{body}\n", encoding="utf-8")
            generated.append(skill_path)

        return generated
