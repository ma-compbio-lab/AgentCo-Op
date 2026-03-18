from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from dynaforge.ir.schema import (
    EdgeSpec,
    FailureType,
    GateSpec,
    IOContract,
    ModelSpec,
    NodeKind,
    NodeSpec,
    PatchOp,
    PatchOpType,
    PatchPlan,
    SubgraphSpec,
    ToolDiscoveryMode,
    ToolDiscoverySpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.runtime.reports import NodeExecutionResult
from dynaforge.workflows.design import RuntimeExpansionSpec

JsonDict = Dict[str, Any]


class RuntimeGraphExpansionPolicy:
    def __init__(
        self,
        spec: RuntimeExpansionSpec,
        *,
        model: ModelSpec,
        review_model: Optional[ModelSpec] = None,
    ) -> None:
        self.spec = spec
        self.model = model
        self.review_model = review_model or model

    def propose(
        self,
        *,
        blueprint: WorkflowBlueprint,
        node: NodeSpec,
        inputs: Mapping[str, Any],
        result: NodeExecutionResult,
        expansion_count: int,
    ) -> Optional[PatchPlan]:
        if not self.spec.enabled or expansion_count >= self.spec.max_expansions:
            return None
        if self._should_add_review(blueprint, node, result):
            return self._review_subgraph_patch(node.node_id)
        if self._should_add_debug(blueprint, node, result):
            return self._debug_subgraph_patch(node.node_id)
        if self._should_add_research(blueprint, node, result):
            return self._research_subgraph_patch(node.node_id)
        return None

    def _should_add_review(self, blueprint: WorkflowBlueprint, node: NodeSpec, result: NodeExecutionResult) -> bool:
        if not self.spec.expand_on_low_confidence:
            return False
        if self._has_subgraph_like(blueprint, "review"):
            return False
        if node.kind not in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
            return False
        if result.failure_type != FailureType.none:
            return False
        if result.outputs.get("requires_review") is True:
            return True
        if result.outputs.get("should_refine") is True:
            return True
        confidence = result.confidence
        return confidence is not None and confidence < 0.65

    def _should_add_debug(self, blueprint: WorkflowBlueprint, node: NodeSpec, result: NodeExecutionResult) -> bool:
        if not self.spec.expand_on_tool_failure:
            return False
        if self._has_subgraph_like(blueprint, "debug"):
            return False
        if result.failure_type in {
            FailureType.tool_runtime_error,
            FailureType.tool_schema_mismatch,
            FailureType.output_contract_violation,
        }:
            return True
        return False

    def _should_add_research(self, blueprint: WorkflowBlueprint, node: NodeSpec, result: NodeExecutionResult) -> bool:
        if not self.spec.expand_on_output_signal:
            return False
        if self._has_subgraph_like(blueprint, "research"):
            return False
        if node.kind not in {NodeKind.agent, NodeKind.router, NodeKind.evaluator}:
            return False
        return bool(result.outputs.get("needs_web_research") or result.outputs.get("needs_repo_search"))

    @staticmethod
    def _has_subgraph_like(blueprint: WorkflowBlueprint, token: str) -> bool:
        token = token.lower()
        return any(token in subgraph.subgraph_id.lower() or token in subgraph.purpose.lower() for subgraph in blueprint.subgraphs)

    def _review_subgraph_patch(self, anchor_node_id: str) -> PatchPlan:
        subgraph_id = f"runtime_review_{anchor_node_id}"
        return PatchPlan(
            plan_id=f"runtime-review-{anchor_node_id}",
            ops=[
                PatchOp(
                    op=PatchOpType.add_subgraph,
                    params={
                        "subgraph": SubgraphSpec(
                            subgraph_id=subgraph_id,
                            purpose="Runtime-added review subgraph for newly discovered uncertainty.",
                            nodes=[
                                NodeSpec(
                                    node_id=f"{anchor_node_id}_runtime_reviewer",
                                    kind=NodeKind.evaluator,
                                    role="RuntimeReviewer",
                                    description="Review an uncertain intermediate result discovered at runtime.",
                                    model=self.review_model,
                                    system_prompt=(
                                        "You are a runtime reviewer. Audit the upstream answer or artifact, then return JSON with "
                                        "output.review_verdict, output.corrected_result, output.concerns, confidence, and summary."
                                    ),
                                    io=IOContract(
                                        output_schema={"type": "object", "required": ["review_verdict", "corrected_result"]}
                                    ),
                                ),
                                NodeSpec(
                                    node_id=f"{anchor_node_id}_runtime_reviser",
                                    kind=NodeKind.agent,
                                    role="RuntimeReviser",
                                    description="Revise an uncertain intermediate result after runtime review.",
                                    model=self.review_model,
                                    system_prompt=(
                                        "You are a runtime reviser. Use the original task and reviewer output to emit the corrected result. "
                                        "Return JSON with output.result, confidence, and summary."
                                    ),
                                    io=IOContract(output_schema={"type": "object", "required": ["result"]}),
                                ),
                            ],
                            edges=[
                                EdgeSpec(
                                    edge_id=f"edge_{anchor_node_id}_to_runtime_reviewer",
                                    src=anchor_node_id,
                                    dst=f"{anchor_node_id}_runtime_reviewer",
                                ),
                                EdgeSpec(
                                    edge_id=f"edge_{anchor_node_id}_runtime_reviewer_to_reviser",
                                    src=f"{anchor_node_id}_runtime_reviewer",
                                    dst=f"{anchor_node_id}_runtime_reviser",
                                ),
                            ],
                            entry_nodes=[f"{anchor_node_id}_runtime_reviewer"],
                            exit_nodes=[f"{anchor_node_id}_runtime_reviser"],
                            default_enabled=True,
                        ).model_dump()
                    },
                    reason="Add a review subgraph because runtime uncertainty was higher than the original blueprint expected.",
                )
            ],
            expected_effect="Introduce a late review path for an initially underestimated task branch.",
        )

    def _debug_subgraph_patch(self, anchor_node_id: str) -> PatchPlan:
        subgraph_id = f"runtime_debug_{anchor_node_id}"
        return PatchPlan(
            plan_id=f"runtime-debug-{anchor_node_id}",
            ops=[
                PatchOp(
                    op=PatchOpType.add_subgraph,
                    params={
                        "subgraph": SubgraphSpec(
                            subgraph_id=subgraph_id,
                            purpose="Runtime-added debug subgraph for tool/contract failures.",
                            nodes=[
                                NodeSpec(
                                    node_id=f"{anchor_node_id}_runtime_debugger",
                                    kind=NodeKind.agent,
                                    role="RuntimeDebugger",
                                    description="Diagnose a runtime tool or contract failure and propose a safer rerun plan.",
                                    model=self.review_model,
                                    system_prompt=(
                                        "You are a runtime debugger. Diagnose the upstream failure and return JSON with "
                                        "output.result, output.suggested_fix, confidence, and summary."
                                    ),
                                    io=IOContract(output_schema={"type": "object", "required": ["result", "suggested_fix"]}),
                                )
                            ],
                            edges=[
                                EdgeSpec(
                                    edge_id=f"edge_{anchor_node_id}_to_runtime_debugger",
                                    src=anchor_node_id,
                                    dst=f"{anchor_node_id}_runtime_debugger",
                                )
                            ],
                            entry_nodes=[f"{anchor_node_id}_runtime_debugger"],
                            exit_nodes=[f"{anchor_node_id}_runtime_debugger"],
                            default_enabled=True,
                        ).model_dump()
                    },
                    reason="Add a debug branch because the runtime failure exceeded what the original graph explicitly handled.",
                )
            ],
            expected_effect="Capture extra failure analysis instead of stopping at the original static graph boundary.",
        )

    def _research_subgraph_patch(self, anchor_node_id: str) -> PatchPlan:
        subgraph_id = f"runtime_research_{anchor_node_id}"
        return PatchPlan(
            plan_id=f"runtime-research-{anchor_node_id}",
            ops=[
                PatchOp(
                    op=PatchOpType.add_subgraph,
                    params={
                        "subgraph": SubgraphSpec(
                            subgraph_id=subgraph_id,
                            purpose="Runtime-added research subgraph when a node requests extra web/repo evidence.",
                            nodes=[
                                NodeSpec(
                                    node_id=f"{anchor_node_id}_runtime_researcher",
                                    kind=NodeKind.agent,
                                    role="RuntimeResearcher",
                                    description="Gather extra repo/web evidence requested by an upstream node.",
                                    model=self.model,
                                    system_prompt=(
                                        "You are a runtime researcher. Use available search tools when needed and return JSON with "
                                        "output.result, output.sources, confidence, and summary."
                                    ),
                                    tool_discovery=ToolDiscoverySpec(
                                        mode=ToolDiscoveryMode.merge,
                                        include_web_search=True,
                                        max_candidate_tools=6,
                                    ),
                                    io=IOContract(output_schema={"type": "object", "required": ["result"]}),
                                )
                            ],
                            edges=[
                                EdgeSpec(
                                    edge_id=f"edge_{anchor_node_id}_to_runtime_researcher",
                                    src=anchor_node_id,
                                    dst=f"{anchor_node_id}_runtime_researcher",
                                )
                            ],
                            entry_nodes=[f"{anchor_node_id}_runtime_researcher"],
                            exit_nodes=[f"{anchor_node_id}_runtime_researcher"],
                            default_enabled=True,
                        ).model_dump()
                    },
                    reason="Add a research branch because the current graph did not include an evidence-gathering stage.",
                )
            ],
            expected_effect="Gather extra evidence instead of forcing the existing graph to continue blind.",
        )
