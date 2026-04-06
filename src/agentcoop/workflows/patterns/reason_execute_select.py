from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping

from agentcoop.ir.schema import (
    AtomicCondition,
    ConditionOp,
    EdgeSpec,
    EvalSpec,
    GateSpec,
    IOContract,
    NodeKind,
    NodeSpec,
    SubgraphSpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from agentcoop.workflows.design import TaskProfile, WorkflowPattern

from agentcoop.workflows.patterns._common import (
    _build_tool_node_from_cfg,
    _default_exact_solver_prompt,
)

if TYPE_CHECKING:
    from agentcoop.workflows.patterns import PatternBuildContext

JsonDict = Dict[str, Any]


def build(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    cfg = context.design.pattern_override(WorkflowPattern.reason_execute_select.value)
    enable_challenger = bool(cfg.get("enable_challenger_solver", False))
    tool_cfg = dict(cfg.get("tool_nodes", {})).get("program_exec", {})
    program_exec = _build_tool_node_from_cfg(
        node_id="program_exec",
        role="ProgramExecutor",
        description="Execute a candidate program in an isolated sandbox and return the observed answer.",
        payload=tool_cfg,
    )
    solver = NodeSpec(
        node_id="solver",
        kind=NodeKind.agent,
        role="Solver",
        description="Produce a direct reasoning-based answer.",
        model=context.model,
        system_prompt=cfg.get("solver_system_prompt") or _default_exact_solver_prompt(profile),
        io=IOContract(output_schema={"type": "object", "required": ["final_answer"]}),
    )
    challenger_solver: NodeSpec | None = None
    if enable_challenger:
        challenger_solver = NodeSpec(
            node_id="solver_challenger",
            kind=NodeKind.agent,
            role="ChallengerSolver",
            description="Produce an independent alternative reasoning-based answer.",
            model=context.model,
            system_prompt=cfg.get("challenger_system_prompt") or _default_challenger_exact_solver_prompt(profile),
            io=IOContract(output_schema={"type": "object", "required": ["final_answer"]}),
        )
    programmer_meta: JsonDict = {}
    if bool(cfg.get("allow_program_backfill", True)):
        programmer_meta["output_backfills"] = [
            {
                "target": "program",
                "strategy": "python_assignment_from_field",
                "source": "final_answer",
                "variable_name": "FINAL_ANSWER",
                "sentinel": "DYNaforge synthetic program fallback",
            }
        ]
    programmer = NodeSpec(
        node_id="programmer",
        kind=NodeKind.agent,
        role="Programmer",
        description="Write a compact Python program or symbolic computation that can verify the answer.",
        model=context.model,
        system_prompt=cfg.get("programmer_system_prompt") or _default_programmer_prompt(profile),
        io=IOContract(output_schema={"type": "object", "required": ["program", "final_answer"]}),
        meta=programmer_meta,
    )
    selector = NodeSpec(
        node_id="selector",
        kind=NodeKind.evaluator,
        role="Selector",
        description="Choose the final answer using reasoning and execution evidence.",
        model=context.review_model,
        system_prompt=cfg.get("selector_system_prompt") or _default_selector_prompt(),
        io=IOContract(output_schema={"type": "object", "required": ["final_answer", "needs_review"]}),
    )
    review_subgraph = _reason_execute_review_subgraph(context, cfg)
    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.reason_execute_select.value,
            "preferred_answer_nodes": ["reviser", "selector", "solver"],
        }
    )
    base_nodes = [solver]
    if challenger_solver is not None:
        base_nodes.append(challenger_solver)
    base_nodes.extend([programmer, program_exec, selector])
    base_edges = [
        EdgeSpec(
            edge_id="edge_solver_to_selector",
            src="solver",
            dst="selector",
            mapping={"solver_answer": "final_answer", "solver_outline": "solution_outline"},
        ),
        EdgeSpec(
            edge_id="edge_programmer_to_program_exec",
            src="programmer",
            dst="program_exec",
            mapping={"program": "program", "candidate_answer": "final_answer"},
        ),
        EdgeSpec(
            edge_id="edge_program_exec_to_selector",
            src="program_exec",
            dst="selector",
            mapping={
                "program_execution_passed": "passed",
                "program_executed_answer": "executed_answer",
                "program_execution_error": "execution_error",
                "program_is_synthetic": "synthetic_program",
            },
        ),
    ]
    if challenger_solver is not None:
        base_edges.append(
            EdgeSpec(
                edge_id="edge_challenger_to_selector",
                src="solver_challenger",
                dst="selector",
                mapping={
                    "challenger_answer": "final_answer",
                    "challenger_outline": "solution_outline",
                },
            )
        )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=base_nodes,
        base_edges=base_edges,
        subgraphs=[review_subgraph],
        gates=[
            GateSpec(
                gate_id="reason_execute_review_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="selector"),
                        AtomicCondition(
                            field="report.confidence",
                            op=ConditionOp.lt,
                            value=float(cfg.get("review_threshold", max(profile.review_threshold, 0.50))),
                        ),
                    ]
                ),
                enable_subgraphs=["sg_review"],
                max_activations=1,
            )
        ],
        eval=EvalSpec(),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Helpers specific to reason_execute_select
# ---------------------------------------------------------------------------


def _default_challenger_exact_solver_prompt(profile: TaskProfile) -> str:
    del profile
    return (
        "You are an independent challenger solver for an exact-answer problem. "
        "Do not anchor on another candidate answer. Solve the task from scratch using a different angle when possible, "
        "such as a direct algebraic derivation, counting argument, sanity-check with small cases, or a geometric constraint check. "
        "Prefer exact symbolic or rational forms over decimal approximations unless the problem explicitly asks for a decimal. "
        "If the answer form still looks ambiguous or fragile, set output.requires_review=true and say why. "
        "Return JSON with output.final_answer, output.solution_outline, output.requires_review, output.uncertainty_reason, confidence, and summary."
    )


def _default_programmer_prompt(profile: TaskProfile) -> str:
    del profile
    return (
        "You are a program-backed verifier. Write compact Python code that computes the answer. "
        "Use only Python stdlib and SymPy unless the task explicitly requires something else. "
        "The code must set FINAL_ANSWER. Prefer a derivation or computation that can be checked against the problem constraints. "
        "When radicals, pi, fractions, or exact algebraic forms appear, keep them symbolic instead of converting to decimal approximations. "
        "Do not call evalf() or round() unless the problem explicitly requests a decimal approximation. "
        "If the computed result looks suspicious relative to the stated constraints, mention that in output.notes. "
        "Return JSON with output.program, output.final_answer, output.notes, confidence, and summary."
    )


def _default_selector_prompt() -> str:
    return (
        "You are an answer selector. Reconcile the direct reasoning answer with the executed program path. "
        "Prefer executed evidence when it is clean and well formed, but reject any candidate that contradicts explicit problem constraints. "
        "If the execution failed, produced an empty answer, or only produced a dubious approximation for an exact-answer task, "
        "set output.needs_review=true unless the solver answer is directly justified from the statement. "
        "If the solver answer and executed answer disagree, set output.needs_review=true unless one candidate can be ruled out directly from the statement. "
        "If both candidates look dubious, still choose the less implausible one but keep needs_review=true. "
        "Return JSON with output.final_answer, output.arbitration_notes, output.needs_review, confidence, and summary."
    )


def _reason_execute_review_subgraph(context: PatternBuildContext, cfg: Mapping[str, Any]) -> SubgraphSpec:
    reviewer = NodeSpec(
        node_id="reviewer",
        kind=NodeKind.evaluator,
        role="Reviewer",
        description="Reconcile the direct solver and the executed program path.",
        model=context.review_model,
        system_prompt=cfg.get("reviewer_system_prompt")
        or (
            "You are reviewing an exact-answer solution. Re-evaluate the task using the solver answer, programmer answer, "
            "execution result, and selector notes. Return JSON with output.corrected_answer, output.review_notes, confidence, and summary."
        ),
        io=IOContract(output_schema={"type": "object", "required": ["corrected_answer"]}),
        meta={
            "output_backfills": [
                {"target": "corrected_answer", "sources": ["outputs.final_answer", "outputs.answer", "outputs.result"]},
            ]
        },
    )
    reviser = NodeSpec(
        node_id="reviser",
        kind=NodeKind.agent,
        role="Reviser",
        description="Emit the final corrected answer after review.",
        model=context.review_model,
        system_prompt=cfg.get("reviser_system_prompt")
        or (
            "You are the final revision node for an exact-answer task. Use the reviewer and selector outputs and return JSON with "
            "output.final_answer, output.solution_outline, confidence, and summary."
        ),
        io=IOContract(output_schema={"type": "object", "required": ["final_answer"]}),
    )
    return SubgraphSpec(
        subgraph_id="sg_review",
        purpose="Review disagreements between reasoning and execution paths.",
        nodes=[reviewer, reviser],
        edges=[
            EdgeSpec(edge_id="edge_solver_to_reviewer", src="solver", dst="reviewer", mapping={"solver_answer": "final_answer"}),
            EdgeSpec(edge_id="edge_program_exec_to_reviewer", src="program_exec", dst="reviewer", mapping={"program_result": "*"}),
            EdgeSpec(edge_id="edge_selector_to_reviewer", src="selector", dst="reviewer", mapping={"selector_result": "*"}),
            EdgeSpec(edge_id="edge_reviewer_to_reviser", src="reviewer", dst="reviser"),
            EdgeSpec(edge_id="edge_selector_to_reviser", src="selector", dst="reviser"),
        ],
        entry_nodes=["reviewer"],
        exit_nodes=["reviser"],
        default_enabled=False,
    )
