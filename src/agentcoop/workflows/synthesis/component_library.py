from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ComponentSpec:
    """A reusable workflow component extracted from the pattern library."""
    component_id: str
    kind: str  # "node", "edge_pattern", "subgraph_fragment"
    source_pattern: str  # which WorkflowPattern it came from
    role: str  # the node role name (e.g. "Solver", "Reviewer")
    capability_tags: List[str]
    domain_tags: List[str]
    complexity_score: float  # 0.0-1.0
    description: str
    node_spec_template: Optional[Dict[str, Any]] = field(default=None, compare=False)


class ComponentLibrary:
    """Indexes all components from the patterns package. Built once by scanning pattern modules."""

    def __init__(self) -> None:
        self._components: List[ComponentSpec] = []
        self._build()

    def _build(self) -> None:
        from agentcoop.workflows.design import WorkflowPattern
        from agentcoop.workflows.patterns import PatternBuildContext, build_pattern_blueprint
        from agentcoop.ir.schema import TaskSpec, BudgetSpec, ModelSpec, NodeKind
        from agentcoop.workflows.design import BlueprintCompilerConfig

        dummy_ctx = PatternBuildContext(
            task=TaskSpec(task_id="probe", title="probe", description="probe"),
            budget=BudgetSpec(),
            meta={},
            model=ModelSpec(provider="openai", name="gpt-4o-mini"),
            review_model=ModelSpec(provider="openai", name="gpt-4o-mini"),
            design=BlueprintCompilerConfig(),
            resolved_config={},
        )

        patterns_to_tags: Dict[str, List[str]] = {
            WorkflowPattern.direct_answer.value: ["reasoning", "qa", "choice"],
            WorkflowPattern.reason_execute_select.value: ["math", "reasoning", "tool", "verification"],
            WorkflowPattern.code_generate_test_repair.value: ["code", "testing", "repair", "execution"],
            WorkflowPattern.repo_transfer_validate.value: ["repo", "transfer", "artifact", "research"],
            WorkflowPattern.closed_loop_design_validate.value: ["optimization", "design", "closed_loop"],
            WorkflowPattern.specialist_assembly.value: ["specialist", "collaboration", "multi_agent"],
        }

        for pattern_id, cap_tags in patterns_to_tags.items():
            try:
                blueprint = build_pattern_blueprint(pattern_id, dummy_ctx)
            except Exception:
                continue
            for node in blueprint.all_nodes():
                if node.kind not in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
                    continue
                domain_tags = [t for t in cap_tags if t in {"math", "code", "qa", "research"}] or ["generic"]
                self._components.append(ComponentSpec(
                    component_id=f"{pattern_id}:{node.node_id}",
                    kind="node",
                    source_pattern=pattern_id,
                    role=node.role,
                    capability_tags=cap_tags,
                    domain_tags=domain_tags,
                    complexity_score=0.6,
                    description=node.description or f"{node.role} node from {pattern_id}",
                    node_spec_template=node.model_dump(mode="json"),
                ))

    @property
    def components(self) -> List[ComponentSpec]:
        return list(self._components)

    def by_role(self, role: str) -> List[ComponentSpec]:
        return [c for c in self._components if c.role.lower() == role.lower()]
