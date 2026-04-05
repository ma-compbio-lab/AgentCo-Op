from __future__ import annotations

from typing import List

from agentcoop.ir.schema import WorkflowBlueprint


class BlueprintValidationError(ValueError):
    """Raised when an assembled blueprint fails structural validation."""


class BlueprintValidator:
    """Validates a WorkflowBlueprint: IR schema, acyclicity, gate references, budget."""

    def validate(self, blueprint: WorkflowBlueprint) -> None:
        errors = self._collect_errors(blueprint)
        if errors:
            raise BlueprintValidationError(
                f"Assembled blueprint failed validation ({len(errors)} error(s)):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    def _collect_errors(self, blueprint: WorkflowBlueprint) -> List[str]:
        errors: List[str] = []

        if not blueprint.base_nodes:
            errors.append("Blueprint has no base_nodes.")

        node_ids = {n.node_id for n in blueprint.all_nodes()}
        for edge in blueprint.base_edges:
            if edge.src not in node_ids:
                errors.append(f"Edge {edge.edge_id!r}: src={edge.src!r} is not a known node.")
            if edge.dst not in node_ids:
                errors.append(f"Edge {edge.edge_id!r}: dst={edge.dst!r} is not a known node.")

        if not errors:
            errors.extend(self._check_acyclic(blueprint))

        subgraph_ids = {sg.subgraph_id for sg in blueprint.subgraphs}
        for gate in blueprint.gates:
            for sg_id in gate.enable_subgraphs:
                if sg_id not in subgraph_ids:
                    errors.append(f"Gate {gate.gate_id!r} references unknown subgraph {sg_id!r}.")

        if blueprint.budget.max_total_usd is not None and blueprint.budget.max_total_usd <= 0:
            errors.append("Budget max_total_usd must be positive.")

        return errors

    def _check_acyclic(self, blueprint: WorkflowBlueprint) -> List[str]:
        adj: dict[str, List[str]] = {n.node_id: [] for n in blueprint.base_nodes}
        for edge in blueprint.base_edges:
            if edge.src in adj:
                adj[edge.src].append(edge.dst)

        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            in_stack.add(node_id)
            for neighbor in adj.get(node_id, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in in_stack:
                    return True
            in_stack.discard(node_id)
            return False

        for node_id in adj:
            if node_id not in visited:
                if dfs(node_id):
                    return [f"Cycle detected in base graph involving node {node_id!r}."]
        return []
