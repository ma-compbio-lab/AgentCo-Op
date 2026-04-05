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
    cfg = context.design.pattern_override(WorkflowPattern.repo_transfer_validate.value)
    repo_scout = NodeSpec(
        node_id="repo_scout",
        kind=NodeKind.agent,
        role="RepoScout",
        description="Search for the most suitable external repo or workflow and summarize how it should be adapted.",
        model=context.model,
        system_prompt=cfg.get("repo_scout_system_prompt") or _default_repo_scout_prompt(),
        tool_discovery=ToolDiscoverySpec(
            mode=ToolDiscoveryMode.merge,
            include_web_search=True,
            max_candidate_tools=int(cfg.get("max_candidate_tools", 8)),
        ),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_repo", "repo_candidates", "analysis_config", "summary"],
            }
        ),
    )
    repo_runner = _build_tool_node_from_cfg(
        node_id="repo_runner",
        role="RepoRunner",
        description="Execute the selected external repository or workflow inside an isolated sandbox.",
        payload=dict(cfg.get("tool_nodes", {})).get("repo_runner", {}),
    )
    validator = NodeSpec(
        node_id="validator",
        kind=NodeKind.evaluator,
        role="Validator",
        description="Check whether the transferred repo execution produced the required artifact bundle and scientific behavior.",
        model=context.review_model,
        system_prompt=cfg.get("validator_system_prompt") or _default_artifact_validator_prompt(),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["overall_verdict", "artifact_completeness", "should_refine", "concerns"],
            }
        ),
    )
    refiner = NodeSpec(
        node_id="refiner",
        kind=NodeKind.agent,
        role="Refiner",
        description="Revise repo selection or analysis configuration after validation feedback.",
        model=context.review_model,
        system_prompt=cfg.get("refiner_system_prompt") or _default_refiner_prompt("repo transfer"),
        io=IOContract(
            output_schema={
                "type": "object",
                "required": ["selected_repo", "analysis_config", "summary"],
            }
        ),
    )
    refined_runner = repo_runner.model_copy(deep=True, update={"node_id": "repo_runner_refined", "role": "RepoRunnerRefined"})
    refined_validator = validator.model_copy(
        deep=True,
        update={"node_id": "validator_refined", "role": "ValidatorRefined"},
    )
    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.repo_transfer_validate.value,
            "preferred_answer_nodes": ["validator_refined", "validator", "repo_runner"],
        }
    )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=[repo_scout, repo_runner, validator],
        base_edges=[
            EdgeSpec(
                edge_id="edge_repo_scout_to_repo_runner",
                src="repo_scout",
                dst="repo_runner",
                mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
            ),
            EdgeSpec(edge_id="edge_repo_runner_to_validator", src="repo_runner", dst="validator", mapping={"tool_result": "result"}),
        ],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_refine",
                purpose="Refine repo choice or analysis config after validation failure.",
                nodes=[refiner, refined_runner, refined_validator],
                edges=[
                    EdgeSpec(edge_id="edge_repo_scout_to_refiner", src="repo_scout", dst="refiner"),
                    EdgeSpec(edge_id="edge_repo_runner_to_refiner", src="repo_runner", dst="refiner", mapping={"tool_result": "result"}),
                    EdgeSpec(edge_id="edge_validator_to_refiner", src="validator", dst="refiner", mapping={"review_result": "*"}),
                    EdgeSpec(
                        edge_id="edge_refiner_to_repo_runner_refined",
                        src="refiner",
                        dst="repo_runner_refined",
                        mapping={"selected_repo": "selected_repo", "analysis_config": "analysis_config"},
                    ),
                    EdgeSpec(edge_id="edge_repo_runner_refined_to_validator_refined", src="repo_runner_refined", dst="validator_refined", mapping={"tool_result": "result"}),
                ],
                entry_nodes=["refiner"],
                exit_nodes=["validator_refined"],
                default_enabled=False,
            )
        ],
        gates=[
            GateSpec(
                gate_id="repo_transfer_refinement_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="validator"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(field="node.outputs.should_refine", op=ConditionOp.eq, value=True),
                                AtomicCondition(field="report.confidence", op=ConditionOp.lt, value=float(cfg.get("review_threshold", 0.75))),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["sg_refine"],
                max_activations=1,
            )
        ],
        eval=EvalSpec(),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Helpers specific to repo_transfer_validate
# ---------------------------------------------------------------------------


def _default_repo_scout_prompt() -> str:
    return (
        "You are a repo-and-tool scout for workflow transfer. Use available web and tool search when needed. "
        "Identify the best candidate repo or workflow, justify the choice, and propose an initial execution plan. "
        "Return JSON with output.selected_repo, output.repo_candidates, output.analysis_config, output.summary, "
        "output.requires_review, and confidence."
    )
