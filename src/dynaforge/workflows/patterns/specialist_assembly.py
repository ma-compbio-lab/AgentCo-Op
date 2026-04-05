from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from dynaforge.ir.schema import (
    AtomicCondition,
    ConditionOp,
    EdgeSpec,
    EvalSpec,
    GateSpec,
    IOContract,
    NodeKind,
    NodeSpec,
    SubgraphSpec,
    ToolDiscoveryMode,
    ToolDiscoverySpec,
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.workflows.design import WorkflowPattern

from dynaforge.workflows.patterns._common import (
    _build_tool_node_from_cfg,
    _default_artifact_validator_prompt,
    _default_refiner_prompt,
)

if TYPE_CHECKING:
    from dynaforge.workflows.patterns import PatternBuildContext

JsonDict = Dict[str, Any]


def build(context: PatternBuildContext) -> WorkflowBlueprint:
    cfg = context.design.pattern_override(WorkflowPattern.specialist_assembly.value)
    router = NodeSpec(
        node_id="specialist_router",
        kind=NodeKind.router,
        role="SpecialistRouter",
        description="Pick specialist repos/agents and define the handoff contract between them.",
        model=context.model,
        system_prompt=cfg.get("router_system_prompt") or _default_specialist_router_prompt(),
        tool_discovery=ToolDiscoverySpec(
            mode=ToolDiscoveryMode.merge,
            include_web_search=True,
            max_candidate_tools=int(cfg.get("max_candidate_tools", 8)),
        ),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_specialists", "handoff_contract", "summary"],
            }
        ),
    )
    specialist_a = _build_tool_node_from_cfg(
        node_id="specialist_a",
        role="SpecialistA",
        description="Run the first selected specialist agent or repo.",
        payload=dict(cfg.get("tool_nodes", {})).get("specialist_a", {}),
    )
    specialist_b = _build_tool_node_from_cfg(
        node_id="specialist_b",
        role="SpecialistB",
        description="Run the second selected specialist agent or repo.",
        payload=dict(cfg.get("tool_nodes", {})).get("specialist_b", {}),
    )
    integrator = NodeSpec(
        node_id="integrator",
        kind=NodeKind.agent,
        role="Integrator",
        description="Integrate the specialist outputs into one coherent proposal or artifact bundle.",
        model=context.review_model,
        system_prompt=cfg.get("integrator_system_prompt") or _default_integrator_prompt(),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["integrated_result", "handoff_summary", "requires_specialist_repair"],
            }
        ),
    )
    reviewer = NodeSpec(
        node_id="reviewer",
        kind=NodeKind.evaluator,
        role="Reviewer",
        description="Judge whether the specialist collaboration is scientifically grounded and complete.",
        model=context.review_model,
        system_prompt=cfg.get("reviewer_system_prompt") or _default_artifact_validator_prompt(),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["overall_verdict", "artifact_completeness", "should_refine", "concerns"],
            }
        ),
    )
    replacement = NodeSpec(
        node_id="specialist_repair",
        kind=NodeKind.agent,
        role="SpecialistRepair",
        description="Replace or reconfigure one of the specialists after a grounded review failure.",
        model=context.review_model,
        system_prompt=cfg.get("repair_system_prompt") or _default_refiner_prompt("specialist collaboration"),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_specialists", "handoff_contract", "summary"],
            }
        ),
    )
    specialist_b_refined = specialist_b.model_copy(
        deep=True,
        update={"node_id": "specialist_b_refined", "role": "SpecialistBRefined"},
    )
    refined_integrator = integrator.model_copy(
        deep=True,
        update={"node_id": "integrator_refined", "role": "IntegratorRefined"},
    )
    refined_reviewer = reviewer.model_copy(
        deep=True,
        update={"node_id": "reviewer_refined", "role": "ReviewerRefined"},
    )
    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.specialist_assembly.value,
            "preferred_answer_nodes": ["reviewer_refined", "reviewer", "integrator"],
        }
    )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=[router, specialist_a, specialist_b, integrator, reviewer],
        base_edges=[
            EdgeSpec(edge_id="edge_router_to_specialist_a", src="specialist_router", dst="specialist_a"),
            EdgeSpec(edge_id="edge_specialist_a_to_specialist_b", src="specialist_a", dst="specialist_b"),
            EdgeSpec(edge_id="edge_specialist_b_to_integrator", src="specialist_b", dst="integrator"),
            EdgeSpec(edge_id="edge_specialist_a_to_integrator", src="specialist_a", dst="integrator"),
            EdgeSpec(edge_id="edge_integrator_to_reviewer", src="integrator", dst="reviewer"),
        ],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_specialist_repair",
                purpose="Replace a specialist or revise the handoff after grounded review failure.",
                nodes=[replacement, specialist_b_refined, refined_integrator, refined_reviewer],
                edges=[
                    EdgeSpec(edge_id="edge_router_to_specialist_repair", src="specialist_router", dst="specialist_repair"),
                    EdgeSpec(edge_id="edge_specialist_a_to_specialist_repair", src="specialist_a", dst="specialist_repair"),
                    EdgeSpec(edge_id="edge_reviewer_to_specialist_repair", src="reviewer", dst="specialist_repair", mapping={"review_result": "*"}),
                    EdgeSpec(edge_id="edge_specialist_repair_to_specialist_b_refined", src="specialist_repair", dst="specialist_b_refined"),
                    EdgeSpec(edge_id="edge_specialist_b_refined_to_integrator_refined", src="specialist_b_refined", dst="integrator_refined"),
                    EdgeSpec(edge_id="edge_specialist_a_to_integrator_refined", src="specialist_a", dst="integrator_refined"),
                    EdgeSpec(edge_id="edge_integrator_refined_to_reviewer_refined", src="integrator_refined", dst="reviewer_refined"),
                ],
                entry_nodes=["specialist_repair"],
                exit_nodes=["reviewer_refined"],
                default_enabled=False,
            )
        ],
        gates=[
            GateSpec(
                gate_id="specialist_repair_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="reviewer"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(field="node.outputs.should_refine", op=ConditionOp.eq, value=True),
                                AtomicCondition(field="node.outputs.requires_specialist_repair", op=ConditionOp.eq, value=True),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["sg_specialist_repair"],
                max_activations=1,
            )
        ],
        eval=EvalSpec(),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Helpers specific to specialist_assembly
# ---------------------------------------------------------------------------


def _default_specialist_router_prompt() -> str:
    return (
        "You are routing a complex task across specialized agents or repos. "
        "Use search when needed, choose the best specialist combination, define the handoff contract, and return JSON with "
        "output.selected_specialists, output.handoff_contract, output.summary, output.requires_review, and confidence."
    )


def _default_integrator_prompt() -> str:
    return (
        "You are integrating outputs from multiple specialized agents. "
        "Return JSON with output.integrated_result, output.handoff_summary, output.requires_specialist_repair, confidence, and summary."
    )
