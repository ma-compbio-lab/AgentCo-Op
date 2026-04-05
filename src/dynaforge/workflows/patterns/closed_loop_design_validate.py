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
    _default_refiner_prompt,
)

if TYPE_CHECKING:
    from dynaforge.workflows.patterns import PatternBuildContext

JsonDict = Dict[str, Any]


def build(context: PatternBuildContext) -> WorkflowBlueprint:
    cfg = context.design.pattern_override(WorkflowPattern.closed_loop_design_validate.value)
    planner = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="Planner",
        description="Plan a constrained design-and-validation workflow and choose the initial configuration.",
        model=context.model,
        system_prompt=cfg.get("planner_system_prompt") or _default_closed_loop_planner_prompt(),
        tool_discovery=ToolDiscoverySpec(
            mode=ToolDiscoveryMode.merge,
            include_web_search=True,
            max_candidate_tools=int(cfg.get("max_candidate_tools", 8)),
        ),
        io=IOContract(
            output_schema={"type": "object", "required": ["design_config", "validation_plan", "summary"]}
        ),
    )
    designer = _build_tool_node_from_cfg(
        node_id="design_runner",
        role="DesignRunner",
        description="Generate the first constrained design candidate and its artifacts.",
        payload=dict(cfg.get("tool_nodes", {})).get("design_runner", {}),
    )
    validator = _build_tool_node_from_cfg(
        node_id="validation_runner",
        role="ValidationRunner",
        description="Validate the current design and expose failure reasons or replacement needs.",
        payload=dict(cfg.get("tool_nodes", {})).get("validation_runner", {}),
    )
    repair = NodeSpec(
        node_id="repair_controller",
        kind=NodeKind.agent,
        role="RepairController",
        description="Update the design configuration using validation failures and constraints.",
        model=context.review_model,
        system_prompt=cfg.get("repair_system_prompt") or _default_refiner_prompt("closed-loop design"),
        io=IOContract(output_schema={"type": "object", "required": ["design_config", "summary"]}),
    )
    redesigned = designer.model_copy(deep=True, update={"node_id": "design_runner_refined", "role": "DesignRunnerRefined"})
    revalidated = validator.model_copy(
        deep=True,
        update={"node_id": "validation_runner_refined", "role": "ValidationRunnerRefined"},
    )
    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.closed_loop_design_validate.value,
            "preferred_answer_nodes": ["validation_runner_refined", "validation_runner", "design_runner"],
        }
    )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=[planner, designer, validator],
        base_edges=[
            EdgeSpec(edge_id="edge_planner_to_design_runner", src="planner", dst="design_runner", mapping={"design_config": "design_config"}),
            EdgeSpec(
                edge_id="edge_design_runner_to_validation_runner",
                src="design_runner",
                dst="validation_runner",
                mapping={"design_result": "result", "validation_plan": "validation_plan"},
            ),
        ],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_replan",
                purpose="Closed-loop redesign after validation failure or poor utility.",
                nodes=[repair, redesigned, revalidated],
                edges=[
                    EdgeSpec(
                        edge_id="edge_design_runner_to_repair_controller",
                        src="design_runner",
                        dst="repair_controller",
                        mapping={"design_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_validation_runner_to_repair_controller",
                        src="validation_runner",
                        dst="repair_controller",
                        mapping={"validation_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_repair_controller_to_design_runner_refined",
                        src="repair_controller",
                        dst="design_runner_refined",
                        mapping={"design_config": "design_config"},
                    ),
                    EdgeSpec(
                        edge_id="edge_design_runner_refined_to_validation_runner_refined",
                        src="design_runner_refined",
                        dst="validation_runner_refined",
                        mapping={"design_result": "result"},
                    ),
                ],
                entry_nodes=["repair_controller"],
                exit_nodes=["validation_runner_refined"],
                default_enabled=False,
            )
        ],
        gates=[
            GateSpec(
                gate_id="closed_loop_replan_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="validation_runner"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(field="node.outputs.needs_replan", op=ConditionOp.eq, value=True),
                                AtomicCondition(field="report.failure_type", op=ConditionOp.eq, value="low_confidence"),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["sg_replan"],
                max_activations=1,
            )
        ],
        eval=EvalSpec(),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Helpers specific to closed_loop_design_validate
# ---------------------------------------------------------------------------


def _default_closed_loop_planner_prompt() -> str:
    return (
        "You are planning a closed-loop design-and-validation workflow under explicit constraints. "
        "Search for relevant evidence or tool guidance when needed, then return JSON with "
        "output.design_config, output.validation_plan, output.summary, output.requires_review, and confidence."
    )
