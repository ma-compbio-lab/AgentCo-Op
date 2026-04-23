"""AFlow workflow importer.

Converts an AFlow `workflow.py` (or a YAML/JSON description of its operator
graph) into an AgentCo-Op `WorkflowBlueprint` so downstream code can
augment + run it as Case Study 3's `AC-AFlowImported` and
`AC-AFlowImported-Gated` variants.

Framework-phase: we parse the AFlow Python file statically to pull out
operator calls, then map them to NodeSpec entries. A handful of operators
(Programmer, Format, Review, Revise, Ensemble, Test, GenerateTest,
MathSolver, MathFormat, etc.) are hard-coded. Any unknown operator is
recorded as an `llm` node with a warning in provenance.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcoop.core.schema import (
    Budget,
    EdgeSpec,
    EvalContract,
    GatePolicy,
    MemoryPlan,
    NodeSpec,
    TaskProfile,
    WorkflowBlueprint,
)


# Operator → (role, backend, default out_key, default in_keys)
OPERATOR_MAP: dict[str, tuple[str, str, str, list[str]]] = {
    "Custom": ("specialist", "llm", "response", ["problem"]),
    "CustomCodeGenerate": ("programmer", "llm", "code", ["problem"]),
    "Programmer": ("programmer", "llm", "code", ["problem"]),
    "ScEnsemble": ("integrator", "llm", "response", ["problem", "solutions"]),
    "SelfConsistency": ("integrator", "llm", "response", ["problem", "solutions"]),
    "SelfRefine": ("specialist", "llm", "response", ["problem", "draft"]),
    "Review": ("reviewer", "llm", "verdict", ["problem", "draft"]),
    "Revise": ("specialist", "llm", "draft", ["problem", "draft", "feedback"]),
    "Ensemble": ("integrator", "llm", "response", ["problem", "solutions"]),
    "Test": ("tool", "python_sandbox", "test_output", ["problem", "code"]),
    "GenerateTest": ("specialist", "llm", "tests", ["problem"]),
    "TestGenerator": ("specialist", "llm", "tests", ["problem"]),
    "Format": ("formatter", "llm", "response", ["problem", "draft"]),
    "MathSolver": ("solver", "llm", "response", ["problem"]),
    "MathFormat": ("formatter", "llm", "response", ["problem", "draft"]),
    "AnswerExtractor": ("formatter", "llm", "final_answer", ["response"]),
    "AnswerFormat": ("formatter", "llm", "final_answer", ["response"]),
    "HotpotQARetriever": ("retriever", "mcp", "context", ["problem"]),
    "DropFormatter": ("formatter", "llm", "response", ["response"]),
    "CoTSolution": ("solver", "llm", "response", ["problem"]),
    "CoT_SC": ("integrator", "llm", "response", ["problem"]),
    "MedPrompt": ("integrator", "llm", "response", ["problem"]),
    "MultiPersona": ("integrator", "llm", "response", ["problem"]),
}


@dataclass
class AFlowOperatorCall:
    operator: str
    var_name: str
    args: dict[str, Any]
    line: int


def parse_aflow_workflow(path: str | Path) -> list[AFlowOperatorCall]:
    """Parse a Python file like `external/AFlow/workspace/<ds>/<wf>.py`.

    We walk the AST looking for `self.<var> = <Operator>(...)` assignments
    in `__init__`, and `self.<var>(...)` calls inside `async def forward`.
    That is sufficient to recover the node sequence for the operator graph.
    """
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)

    # Map var_name → operator class name from __init__.
    operator_by_var: dict[str, tuple[str, int]] = {}
    call_sequence: list[AFlowOperatorCall] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # self.xxx = Something(...)
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "self"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, (ast.Name, ast.Attribute))
            ):
                func = node.value.func
                op_name = (
                    func.id if isinstance(func, ast.Name)
                    else getattr(func, "attr", None)
                )
                if op_name:
                    operator_by_var[node.targets[0].attr] = (op_name, node.lineno)

    # Now walk top-to-bottom inside forward-like methods, capturing calls.
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name in ("forward", "run", "__call__"):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "self"
                    ):
                        var_name = sub.func.attr
                        op_info = operator_by_var.get(var_name)
                        if not op_info:
                            continue
                        op_name, _ = op_info
                        args = {
                            kw.arg: _render(kw.value)
                            for kw in sub.keywords
                            if kw.arg
                        }
                        call_sequence.append(
                            AFlowOperatorCall(
                                operator=op_name,
                                var_name=var_name,
                                args=args,
                                line=sub.lineno,
                            )
                        )
    return call_sequence


def _render(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return ast.unparse(node) if hasattr(ast, "unparse") else ""


def call_sequence_to_blueprint(
    calls: list[AFlowOperatorCall],
    *,
    dataset: str,
    source_path: str | None = None,
) -> WorkflowBlueprint:
    """Lower a call sequence to a WorkflowBlueprint."""
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []
    unknown: list[str] = []

    for i, call in enumerate(calls):
        mapping = OPERATOR_MAP.get(call.operator)
        if mapping is None:
            role, backend = "specialist", "llm"
            unknown.append(call.operator)
        else:
            role, backend = mapping[0], mapping[1]
        nid = f"{call.var_name}_{i+1}"
        nodes.append(
            NodeSpec(
                node_id=nid,
                role=role,
                backend=backend,
                params={"aflow_operator": call.operator, "source_line": call.line, **call.args},
                skill_refs=[],
            )
        )
        if i > 0:
            edges.append(EdgeSpec(source=nodes[i - 1].node_id, target=nid))

    if not nodes:
        # Fallback to an `IO` single-agent graph if AST parsing found nothing.
        solver = NodeSpec(node_id="io_solver", role="solver", backend="llm")
        nodes.append(solver)

    profile = TaskProfile(
        task_id=f"aflow-import-{dataset}",
        raw_task=f"AFlow-imported workflow for {dataset}",
        domain=[dataset],
        budget=Budget(),
    )
    provenance = [f"aflow_import:{source_path or 'unknown'}"]
    if unknown:
        provenance.append(f"unknown_operators:{','.join(sorted(set(unknown)))}")

    return WorkflowBlueprint(
        blueprint_id=f"aflow-{uuid.uuid4().hex[:8]}",
        task_profile=profile,
        nodes=nodes,
        edges=edges,
        gate_policies=[
            GatePolicy(
                name="budget_near_limit",
                trigger="budget_near_limit",
                threshold=0.9,
                action="terminate_with_uncertainty",
                max_activations=1,
                scope="graph",
            )
        ],
        memory_plan=MemoryPlan(blackboard_keys=[n.node_id for n in nodes]),
        eval_contract=EvalContract(answer_type="short_answer"),
        budget=Budget(),
        topology_level=4,  # AFlow workflows are typically multi-specialist
        complexity=0.1 * len(nodes),
        provenance=provenance,
    )


def import_aflow_workflow(path: str | Path, *, dataset: str) -> WorkflowBlueprint:
    p = Path(path)
    if p.suffix == ".py":
        calls = parse_aflow_workflow(p)
    elif p.suffix in (".json", ".jsonl"):
        data = json.loads(p.read_text(encoding="utf-8"))
        calls = [
            AFlowOperatorCall(
                operator=entry["operator"],
                var_name=entry.get("var_name", entry["operator"].lower()),
                args=entry.get("args", {}) or {},
                line=entry.get("line", 0),
            )
            for entry in data.get("calls", [])
        ]
    elif p.suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        calls = [
            AFlowOperatorCall(
                operator=entry["operator"],
                var_name=entry.get("var_name", entry["operator"].lower()),
                args=entry.get("args", {}) or {},
                line=entry.get("line", 0),
            )
            for entry in data.get("calls", [])
        ]
    else:
        raise ValueError(f"unsupported AFlow workflow format: {p.suffix}")

    return call_sequence_to_blueprint(calls, dataset=dataset, source_path=str(p))


__all__ = [
    "AFlowOperatorCall",
    "parse_aflow_workflow",
    "call_sequence_to_blueprint",
    "import_aflow_workflow",
]
