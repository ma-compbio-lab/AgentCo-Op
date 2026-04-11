from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from agentcoop.ir.schema import (
    AtomicCondition,
    ConditionOp,
    EdgeSpec,
    EvalSpec,
    GateSpec,
    IOContract,
    NodeKind,
    NodeSpec,
    ReviewPolicy,
    SubgraphSpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from agentcoop.workflows.design import TaskProfile, WorkflowPattern

from agentcoop.workflows.patterns._common import _default_exact_solver_prompt

if TYPE_CHECKING:
    from agentcoop.workflows.patterns import PatternBuildContext

JsonDict = Dict[str, Any]


def build(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    cfg = context.design.pattern_override(WorkflowPattern.direct_answer.value)
    choice_mode = profile.answer_mode == "choice_label"
    solver_prompt = cfg.get("solver_system_prompt") or _default_direct_solver_prompt(profile)
    solver_node = NodeSpec(
        node_id="solver",
        kind=NodeKind.agent,
        role="Solver",
        description=cfg.get("solver_description", "Answer the task directly with a concise, structured response."),
        model=context.model,
        system_prompt=solver_prompt,
        io=IOContract(output_schema=_direct_answer_output_schema(profile)),
        cacheable=bool(cfg.get("solver_cacheable", True)),
        max_steps=int(cfg.get("solver_max_steps", 1 if profile.complexity == "low" else 2)),
    )

    base_nodes = [solver_node]
    base_edges: list[EdgeSpec] = []
    subgraphs: list[SubgraphSpec] = []
    gates: list[GateSpec] = []
    preferred_answers = ["solver"]

    if profile.preferred_review_style != "none" and not bool(cfg.get("disable_review_subgraph", False)):
        reviewer_prompt = cfg.get("reviewer_system_prompt") or _default_direct_reviewer_prompt(profile)
        reviser_prompt = cfg.get("reviser_system_prompt") or _default_direct_reviser_prompt(profile)
        reviewer = NodeSpec(
            node_id="reviewer",
            kind=NodeKind.evaluator,
            role="Reviewer",
            description="Audit the direct answer and decide whether it should be revised.",
            model=context.review_model,
            system_prompt=reviewer_prompt,
            io=IOContract(output_schema=_direct_review_output_schema(profile)),
        )
        reviser = NodeSpec(
            node_id="reviser",
            kind=NodeKind.agent,
            role="Reviser",
            description="Emit the final answer after reviewer critique.",
            model=context.review_model,
            system_prompt=reviser_prompt,
            io=IOContract(output_schema=_direct_answer_output_schema(profile)),
        )
        subgraphs.append(
            SubgraphSpec(
                subgraph_id="sg_review",
                purpose="Optional answer verification for low-confidence or flagged cases.",
                review_policy=ReviewPolicy.verify_then_correct,
                nodes=[reviewer, reviser],
                edges=[
                    EdgeSpec(edge_id="edge_solver_to_reviewer", src="solver", dst="reviewer"),
                    EdgeSpec(edge_id="edge_reviewer_to_reviser", src="reviewer", dst="reviser"),
                    EdgeSpec(edge_id="edge_solver_to_reviser", src="solver", dst="reviser"),
                ],
                entry_nodes=["reviewer"],
                exit_nodes=["reviser"],
                default_enabled=profile.preferred_review_style == "always",
            )
        )
        if profile.preferred_review_style != "always":
            any_of: list[AtomicCondition | TriggerExpr] = [
                AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="solver"),
                TriggerExpr(
                    any_of=[
                        AtomicCondition(field="node.outputs.requires_review", op=ConditionOp.eq, value=True),
                        AtomicCondition(field="report.confidence", op=ConditionOp.lt, value=profile.review_threshold),
                    ]
                ),
            ]
            trigger = TriggerExpr(all_of=any_of)
            hint_field = profile.high_risk_hint_field.strip()
            if hint_field and profile.high_risk_hint_values:
                risk_checks = [
                    AtomicCondition(
                        field=f"node.inputs.task.hints.{hint_field}",
                        op=ConditionOp.eq,
                        value=value,
                    )
                    for value in profile.high_risk_hint_values
                ]
                trigger = TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="solver"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(field="node.outputs.requires_review", op=ConditionOp.eq, value=True),
                                AtomicCondition(field="report.confidence", op=ConditionOp.lt, value=profile.review_threshold),
                                TriggerExpr(any_of=risk_checks),
                            ]
                        ),
                    ]
                )
            gates.append(
                GateSpec(
                    gate_id="direct_answer_review_gate",
                    trigger=trigger,
                    enable_subgraphs=["sg_review"],
                    max_activations=1,
                )
            )
        preferred_answers = ["reviser", "reviewer", "solver"] if choice_mode else ["reviser", "solver"]

    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.direct_answer.value,
            "preferred_answer_nodes": preferred_answers,
        }
    )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        mcp_servers=[],
        base_nodes=base_nodes,
        base_edges=base_edges,
        subgraphs=subgraphs,
        gates=gates,
        eval=EvalSpec(),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Helpers specific to direct_answer
# ---------------------------------------------------------------------------


def _default_direct_solver_prompt(profile: TaskProfile) -> str:
    if profile.answer_mode == "choice_label":
        if profile.domain == "medical_qa":
            return (
                "You are a high-precision medical multiple-choice solver. "
                "Solve the question directly from task.description and task.hints.options without relying on other nodes. "
                "Return JSON with output.final_answer_label, output.final_answer_text, output.rationale, "
                "output.requires_review, output.uncertainty_reason, confidence, and summary. "
                "Choose exactly one available option. Set requires_review=true only when the stem is genuinely ambiguous or you are not confident."
            )
        return (
            "You are a multiple-choice solver. Solve the question directly from task.description and task.hints.options. "
            "Return JSON with output.final_answer_label, output.final_answer_text, output.rationale, "
            "output.requires_review, output.uncertainty_reason, confidence, and summary. "
            "Choose exactly one available option."
        )
    if profile.answer_mode == "exact_answer":
        return _default_exact_solver_prompt(profile)
    return (
        "You are a direct-answer solver. Solve the task directly. "
        "Return JSON with output.result, output.rationale, output.requires_review, output.uncertainty_reason, confidence, and summary."
    )


def _default_direct_reviewer_prompt(profile: TaskProfile) -> str:
    if profile.answer_mode == "choice_label":
        return (
            "You are reviewing a direct multiple-choice answer. Assume the solver may be wrong. "
            "Audit the answer against the question and options, then return JSON with "
            "output.review_verdict ('keep' or 'revise'), output.corrected_answer_label, output.corrected_answer_text, "
            "output.critique, output.rationale, confidence, and summary."
        )
    return (
        "You are reviewing a direct answer. Assume the solver may be wrong. "
        "Return JSON with output.review_verdict, output.corrected_result, output.critique, output.rationale, confidence, and summary."
    )


def _default_direct_reviser_prompt(profile: TaskProfile) -> str:
    if profile.answer_mode == "choice_label":
        return (
            "You are the final revision node for a multiple-choice task. "
            "Use the original task, the solver output, and the reviewer output to emit the final answer. "
            "Return JSON with output.final_answer_label, output.final_answer_text, output.rationale, confidence, and summary."
        )
    if profile.answer_mode == "exact_answer":
        return (
            "You are the final revision node for an exact-answer task. "
            "Return JSON with output.final_answer, output.solution_outline, confidence, and summary."
        )
    return (
        "You are the final revision node. "
        "Return JSON with output.result, output.rationale, confidence, and summary."
    )


def _direct_answer_output_schema(profile: TaskProfile) -> JsonDict:
    if profile.answer_mode == "choice_label":
        return {
            "type": "object",
            "required": ["final_answer_label", "final_answer_text"],
        }
    if profile.answer_mode == "exact_answer":
        return {"type": "object", "required": ["final_answer"]}
    return {"type": "object", "required": ["result"]}


def _direct_review_output_schema(profile: TaskProfile) -> JsonDict:
    if profile.answer_mode == "choice_label":
        return {
            "type": "object",
            "required": ["review_verdict"],
        }
    return {"type": "object", "required": ["review_verdict"]}
