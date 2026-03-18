from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from dynaforge.ir.schema import (
    ConditionOp,
    EdgeSpec,
    GateSpec,
    HardCheck,
    IOContract,
    ModelProvider,
    ModelSpec,
    NodeSpec,
    PatchOp,
    PatchOpType,
    PatchPlan,
    SandboxSpec,
    SoftJudge,
    SubgraphSpec,
    ToolRef,
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.runtime.reports import ExecutionReport


class DeterministicPatchPolicy:
    """Low-cost fallback policy for common failure types."""

    def __init__(self, cheaper_model_name: str = "gpt-4.1-mini"):
        self.cheaper_model_name = cheaper_model_name

    def propose(self, blueprint: WorkflowBlueprint, report: ExecutionReport) -> PatchPlan:
        primary = report.blame_candidates[0] if report.blame_candidates else None
        target_node = primary.target_id if primary and primary.target_type == "node" else self._fallback_node(report)
        failure_type = report.failure_type.value
        ops: List[PatchOp] = []
        expected_effect = "Stabilize execution with a minimal targeted repair."
        node = blueprint.node_map().get(target_node) if target_node else None

        if failure_type in {"env_missing_dep", "env_version_conflict"} and target_node:
            sandbox = self._next_sandbox(node)
            ops.append(
                PatchOp(
                    op=PatchOpType.change_sandbox,
                    target=target_node,
                    params={"sandbox": sandbox.model_dump()},
                    reason="Switch to a prebuilt isolated sandbox image to address environment drift.",
                )
            )
            ops.append(
                PatchOp(
                    op=PatchOpType.add_hard_check,
                    params={
                        "hard_check": HardCheck(
                            check_id=f"hc_{target_node}_import",
                            description=f"Verify node {target_node} runtime entrypoint remains callable.",
                            cmd=["python", "-c", "print('sandbox-ok')"],
                            timeout_s=30,
                        ).model_dump()
                    },
                    reason="Add a lightweight regression check after sandbox repair.",
                )
            )
            expected_effect = "Repair environment setup and catch regressions earlier."
        elif failure_type in {"tool_schema_mismatch", "tool_runtime_error"} and target_node:
            replacement = self._swap_tool(node)
            if replacement is not None:
                ops.append(
                    PatchOp(
                        op=PatchOpType.swap_tool,
                        target=target_node,
                        params={"to": replacement.model_dump()},
                        reason="Switch to a safer tool binding under the same MCP server.",
                    )
                )
            else:
                ops.append(
                    PatchOp(
                        op=PatchOpType.tighten_contract,
                        target=target_node,
                        params={"io": {"input_schema": {"type": "object", "required": ["payload"]}}},
                        reason="Constrain upstream inputs to avoid malformed tool calls.",
                    )
                )
            expected_effect = "Reduce tool invocation mismatch and force consistent tool input shape."
        elif failure_type in {"output_contract_violation", "file_missing"} and target_node:
            ops.append(
                PatchOp(
                    op=PatchOpType.tighten_contract,
                    target=target_node,
                    params={"io": {"output_schema": {"type": "object", "required": ["result"]}}},
                    reason="Make required outputs explicit so downstream checks can fail fast.",
                )
            )
            expected_effect = "Enforce required outputs before downstream consumers run."
        elif failure_type in {"low_confidence", "reasoning_inconsistency"}:
            review_subgraph = self._pick_review_subgraph(blueprint)
            if review_subgraph is not None:
                ops.append(
                    PatchOp(
                        op=PatchOpType.add_gate,
                        params={
                            "gate": GateSpec(
                                gate_id=f"gate_{review_subgraph}",
                                trigger=TriggerExpr(
                                    any_of=[
                                        {
                                            "field": "report.confidence",
                                            "op": ConditionOp.lt,
                                            "value": 0.65,
                                        }
                                    ]
                                ),
                                enable_subgraphs=[review_subgraph],
                            ).model_dump(by_alias=True)
                        },
                        reason="Only enable the review path when confidence drops below threshold.",
                    )
                )
            elif target_node:
                ops.append(
                    PatchOp(
                        op=PatchOpType.insert_node,
                        params={
                            "new_node": self._reviewer_node(target_node).model_dump(),
                            "position": "after",
                            "anchors": [target_node],
                        },
                        reason="Inject a reviewer immediately after the suspect node.",
                    )
                )
            expected_effect = "Raise answer reliability on hard inputs without always paying reviewer cost."
        elif failure_type in {"timeout", "budget_exceeded"} and target_node:
            if node and node.model:
                cheaper_model = node.model.model_copy(
                    update={
                        "provider": node.model.provider if node.model.provider != ModelProvider.local else ModelProvider.local,
                        "name": self.cheaper_model_name,
                        "temperature": min(node.model.temperature, 0.2),
                    }
                )
                ops.append(
                    PatchOp(
                        op=PatchOpType.replace_model,
                        target=target_node,
                        params=cheaper_model.model_dump(),
                        reason="Reduce latency and cost on the highest-risk node.",
                    )
                )
            elif blueprint.subgraphs:
                ops.append(
                    PatchOp(
                        op=PatchOpType.prune_subgraph,
                        target=blueprint.subgraphs[-1].subgraph_id,
                        reason="Prune the last optional subgraph to stay within budget.",
                    )
                )
            expected_effect = "Lower marginal cost and avoid runaway execution."
        elif target_node:
            ops.append(
                PatchOp(
                    op=PatchOpType.add_hard_check,
                    params={
                        "hard_check": HardCheck(
                            check_id=f"hc_{target_node}_sanity",
                            description=f"Sanity-check outputs around {target_node}.",
                            cmd=["python", "-c", "print('ok')"],
                        ).model_dump()
                    },
                    reason="Add a cheap guardrail before further patching.",
                )
            )

        if not ops and target_node:
            ops.append(
                PatchOp(
                    op=PatchOpType.tighten_contract,
                    target=target_node,
                    params={"io": {"invariants": ["outputs.get('result') is not None"]}},
                    reason="Fallback repair is to make success criteria explicit.",
                )
            )

        return PatchPlan(
            plan_id=f"auto-{report.task_id}-{len(report.traces)}",
            ops=ops[:5],
            expected_effect=expected_effect,
            risk="low" if len(ops) <= 2 else "medium",
            estimated_delta_usd=0.05 * len(ops),
        )

    @staticmethod
    def _fallback_node(report: ExecutionReport) -> Optional[str]:
        if report.traces:
            return report.traces[-1].node_id
        return None

    @staticmethod
    def _swap_tool(node: Optional[NodeSpec]) -> Optional[ToolRef]:
        if node is None:
            return None
        if len(node.tools) > 1:
            return node.tools[1]
        if len(node.tools) == 1:
            tool = node.tools[0]
            return tool.model_copy(update={"tool": f"{tool.tool}_fallback"})
        return None

    @staticmethod
    def _next_sandbox(node: Optional[NodeSpec]) -> SandboxSpec:
        if node and node.sandbox:
            return node.sandbox.model_copy(
                update={
                    "sandbox_id": f"{node.sandbox.sandbox_id}-repair",
                    "image": f"{node.sandbox.image}-repair",
                }
            )
        return SandboxSpec(sandbox_id="default-repair", image="python:3.11-slim")

    @staticmethod
    def _pick_review_subgraph(blueprint: WorkflowBlueprint) -> Optional[str]:
        for subgraph in blueprint.subgraphs:
            if "review" in subgraph.purpose.lower() or "review" in subgraph.subgraph_id.lower():
                return subgraph.subgraph_id
        return blueprint.subgraphs[0].subgraph_id if blueprint.subgraphs else None

    @staticmethod
    def _reviewer_node(anchor_node_id: str) -> NodeSpec:
        return NodeSpec(
            node_id=f"{anchor_node_id}_reviewer",
            kind="evaluator",
            role="Reviewer",
            description="Targeted review node inserted by deterministic patch policy.",
            model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            io=IOContract(input_schema={"type": "object"}),
        )


def compute_impacted_nodes(blueprint: WorkflowBlueprint, patch_plan: PatchPlan) -> Set[str]:
    node_map = blueprint.node_map()
    children: Dict[str, Set[str]] = defaultdict(set)
    for edge in blueprint.all_edges():
        children[edge.src].add(edge.dst)

    impacted: Set[str] = set()
    queue: deque[str] = deque()
    for op in patch_plan.ops:
        if op.target and op.target in node_map:
            queue.append(op.target)
            impacted.add(op.target)
        if op.op == PatchOpType.add_subgraph and "subgraph" in op.params:
            subgraph = SubgraphSpec.model_validate(op.params["subgraph"])
            for node in subgraph.nodes:
                impacted.add(node.node_id)
                queue.append(node.node_id)
        if op.op == PatchOpType.insert_node and "new_node" in op.params:
            new_node_id = op.params["new_node"]["node_id"]
            impacted.add(new_node_id)
            queue.append(new_node_id)
            for anchor in op.params.get("anchors", []):
                if anchor in node_map:
                    impacted.add(anchor)
                    queue.append(anchor)

    while queue:
        current = queue.popleft()
        for child in children.get(current, set()):
            if child not in impacted:
                impacted.add(child)
                queue.append(child)

    if not impacted:
        impacted = set(node_map.keys())
    return impacted


def apply_patch_plan(blueprint: WorkflowBlueprint, patch_plan: PatchPlan | Dict[str, Any]) -> WorkflowBlueprint:
    if not isinstance(patch_plan, PatchPlan):
        patch_plan = PatchPlan.model_validate(patch_plan)
    updated = blueprint.model_copy(deep=True)
    for op in patch_plan.ops:
        _apply_patch_op(updated, op)
    return WorkflowBlueprint.model_validate(updated.model_dump(by_alias=True))


def _apply_patch_op(blueprint: WorkflowBlueprint, op: PatchOp) -> None:
    node_holder, node_index = _find_node_holder(blueprint, op.target) if op.target else (None, None)
    edge_holder, edge_index = _find_edge_holder(blueprint, op.target) if op.target else (None, None)

    if op.op == PatchOpType.replace_model:
        if node_holder is None or node_index is None:
            return
        node_holder[node_index].model = ModelSpec.model_validate(op.params)
        return

    if op.op == PatchOpType.swap_tool:
        if node_holder is None or node_index is None:
            return
        node = node_holder[node_index]
        replacement = ToolRef.model_validate(op.params["to"])
        if "from" in op.params:
            source = ToolRef.model_validate(op.params["from"])
            for idx, tool in enumerate(node.tools):
                if tool.server == source.server and tool.tool == source.tool:
                    node.tools[idx] = replacement
                    break
            else:
                node.tools.append(replacement)
        elif node.tools:
            node.tools[0] = replacement
        else:
            node.tools.append(replacement)
        return

    if op.op == PatchOpType.insert_node:
        new_node = NodeSpec.model_validate(op.params["new_node"])
        target_nodes = _target_node_list(blueprint, op.params.get("subgraph_id"))
        if all(node.node_id != new_node.node_id for node in target_nodes):
            target_nodes.append(new_node)
        _wire_inserted_node(blueprint, new_node.node_id, op.params)
        return

    if op.op == PatchOpType.remove_node:
        if node_holder is None or node_index is None:
            return
        removed_id = node_holder[node_index].node_id
        del node_holder[node_index]
        _remove_incident_edges(blueprint, removed_id)
        return

    if op.op == PatchOpType.insert_edge:
        target_edges = _target_edge_list(blueprint, op.params.get("subgraph_id"))
        edge_id = op.params.get("edge_id") or f"edge_{op.params['src']}_to_{op.params['dst']}"
        target_edges.append(
            EdgeSpec.model_validate(
                {
                "edge_id": edge_id,
                "src": op.params["src"],
                "dst": op.params["dst"],
                "mapping": op.params.get("mapping", {}),
                "condition": op.params.get("condition"),
                }
            )
        )
        return

    if op.op == PatchOpType.add_subgraph:
        subgraph = SubgraphSpec.model_validate(op.params["subgraph"])
        blueprint.subgraphs = [item for item in blueprint.subgraphs if item.subgraph_id != subgraph.subgraph_id]
        blueprint.subgraphs.append(subgraph)
        return

    if op.op == PatchOpType.remove_edge:
        if edge_holder is None or edge_index is None:
            return
        del edge_holder[edge_index]
        return

    if op.op == PatchOpType.add_gate:
        blueprint.gates.append(GateSpec.model_validate(op.params["gate"]))
        return

    if op.op == PatchOpType.prune_subgraph:
        if not op.target:
            return
        blueprint.subgraphs = [subgraph for subgraph in blueprint.subgraphs if subgraph.subgraph_id != op.target]
        for gate in blueprint.gates:
            gate.enable_subgraphs = [subgraph_id for subgraph_id in gate.enable_subgraphs if subgraph_id != op.target]
        return

    if op.op in {PatchOpType.tighten_contract, PatchOpType.relax_contract}:
        if node_holder is None or node_index is None:
            return
        node = node_holder[node_index]
        node.io = _merge_contract(
            node.io,
            op.params.get("io", {}),
            relax=op.op == PatchOpType.relax_contract,
        )
        return

    if op.op == PatchOpType.change_sandbox:
        if node_holder is None or node_index is None:
            return
        node_holder[node_index].sandbox = SandboxSpec.model_validate(op.params["sandbox"])
        return

    if op.op == PatchOpType.add_hard_check:
        blueprint.eval.hard_checks.append(HardCheck.model_validate(op.params["hard_check"]))
        return

    if op.op == PatchOpType.add_soft_judge:
        blueprint.eval.soft_judges.append(SoftJudge.model_validate(op.params["soft_judge"]))
        return


def _target_node_list(blueprint: WorkflowBlueprint, subgraph_id: Optional[str]) -> List[NodeSpec]:
    if subgraph_id:
        for subgraph in blueprint.subgraphs:
            if subgraph.subgraph_id == subgraph_id:
                return subgraph.nodes
    return blueprint.base_nodes


def _target_edge_list(blueprint: WorkflowBlueprint, subgraph_id: Optional[str]):
    if subgraph_id:
        for subgraph in blueprint.subgraphs:
            if subgraph.subgraph_id == subgraph_id:
                return subgraph.edges
    return blueprint.base_edges


def _find_node_holder(
    blueprint: WorkflowBlueprint, node_id: Optional[str]
) -> Tuple[Optional[List[NodeSpec]], Optional[int]]:
    if node_id is None:
        return None, None
    for idx, node in enumerate(blueprint.base_nodes):
        if node.node_id == node_id:
            return blueprint.base_nodes, idx
    for subgraph in blueprint.subgraphs:
        for idx, node in enumerate(subgraph.nodes):
            if node.node_id == node_id:
                return subgraph.nodes, idx
    return None, None


def _find_edge_holder(blueprint: WorkflowBlueprint, edge_id: Optional[str]):
    if edge_id is None:
        return None, None
    for idx, edge in enumerate(blueprint.base_edges):
        if edge.edge_id == edge_id:
            return blueprint.base_edges, idx
    for subgraph in blueprint.subgraphs:
        for idx, edge in enumerate(subgraph.edges):
            if edge.edge_id == edge_id:
                return subgraph.edges, idx
    return None, None


def _remove_incident_edges(blueprint: WorkflowBlueprint, node_id: str) -> None:
    blueprint.base_edges = [edge for edge in blueprint.base_edges if edge.src != node_id and edge.dst != node_id]
    for subgraph in blueprint.subgraphs:
        subgraph.edges = [edge for edge in subgraph.edges if edge.src != node_id and edge.dst != node_id]


def _wire_inserted_node(blueprint: WorkflowBlueprint, new_node_id: str, params: Dict[str, Any]) -> None:
    target_edges = _target_edge_list(blueprint, params.get("subgraph_id"))
    position = params.get("position")
    anchors = params.get("anchors", [])
    explicit_edges = params.get("connect", [])

    for edge_spec in explicit_edges:
        target_edges.append(EdgeSpec.model_validate(edge_spec))

    if position == "between" and len(anchors) >= 2:
        src, dst = anchors[0], anchors[1]
        target_edges[:] = [edge for edge in target_edges if not (edge.src == src and edge.dst == dst)]
        target_edges.append(EdgeSpec(edge_id=f"edge_{src}_to_{new_node_id}", src=src, dst=new_node_id))
        target_edges.append(EdgeSpec(edge_id=f"edge_{new_node_id}_to_{dst}", src=new_node_id, dst=dst))
    elif position == "after" and anchors:
        anchor = anchors[0]
        target_edges.append(EdgeSpec(edge_id=f"edge_{anchor}_to_{new_node_id}", src=anchor, dst=new_node_id))
    elif position == "before" and anchors:
        anchor = anchors[0]
        target_edges.append(EdgeSpec(edge_id=f"edge_{new_node_id}_to_{anchor}", src=new_node_id, dst=anchor))


def _merge_contract(current: IOContract, updates: Dict[str, Any], *, relax: bool) -> IOContract:
    merged = current.model_dump()
    for key in ("input_schema", "output_schema"):
        if key not in updates:
            continue
        value = updates[key]
        if relax:
            merged[key] = value
        else:
            merged[key] = _merge_schema_values(merged.get(key), value)

    if "invariants" in updates:
        if relax:
            merged["invariants"] = updates["invariants"]
        else:
            merged["invariants"] = list(dict.fromkeys([*merged.get("invariants", []), *updates["invariants"]]))
    return IOContract.model_validate(merged)


def _merge_schema_values(current: Any, updates: Any) -> Any:
    if isinstance(current, dict) and isinstance(updates, dict):
        merged = dict(current)
        for key, value in updates.items():
            if key == "required" and isinstance(value, list):
                existing = merged.get("required", [])
                if isinstance(existing, list):
                    merged["required"] = list(dict.fromkeys([*existing, *value]))
                else:
                    merged["required"] = value
            elif key in merged:
                merged[key] = _merge_schema_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    if isinstance(current, list) and isinstance(updates, list):
        return list(dict.fromkeys([*current, *updates]))
    return updates
