from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from dynaforge.ir.schema import (
    AtomicCondition,
    BudgetSpec,
    ConditionOp,
    EdgeSpec,
    EvalSpec,
    GateSpec,
    IOContract,
    ModelSpec,
    NodeKind,
    NodeSpec,
    SandboxSpec,
    SubgraphSpec,
    TaskSpec,
    ToolDiscoveryMode,
    ToolDiscoverySpec,
    ToolRef,
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.workflows.design import BlueprintCompilerConfig, TaskProfile, WorkflowPattern

JsonDict = Dict[str, Any]


@dataclass
class PatternBuildContext:
    task: TaskSpec
    budget: BudgetSpec
    meta: JsonDict
    model: ModelSpec
    review_model: ModelSpec
    design: BlueprintCompilerConfig
    resolved_config: Mapping[str, Any]


def build_pattern_blueprint(pattern_id: str, context: PatternBuildContext) -> WorkflowBlueprint:
    if pattern_id == WorkflowPattern.direct_answer.value:
        return _build_direct_answer_blueprint(context)
    if pattern_id == WorkflowPattern.reason_execute_select.value:
        return _build_reason_execute_select_blueprint(context)
    if pattern_id == WorkflowPattern.code_generate_test_repair.value:
        return _build_code_generate_test_repair_blueprint(context)
    if pattern_id == WorkflowPattern.repo_transfer_validate.value:
        return _build_repo_transfer_validate_blueprint(context)
    if pattern_id == WorkflowPattern.closed_loop_design_validate.value:
        return _build_closed_loop_design_validate_blueprint(context)
    if pattern_id == WorkflowPattern.specialist_assembly.value:
        return _build_specialist_assembly_blueprint(context)
    if pattern_id == WorkflowPattern.synthesized_hybrid.value:
        return _build_synthesized_hybrid_blueprint(context)
    raise ValueError(f"Unsupported workflow pattern: {pattern_id}")


def _build_direct_answer_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
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


def _build_reason_execute_select_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    cfg = context.design.pattern_override(WorkflowPattern.reason_execute_select.value)
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
    programmer = NodeSpec(
        node_id="programmer",
        kind=NodeKind.agent,
        role="Programmer",
        description="Write a compact Python program or symbolic computation that can verify the answer.",
        model=context.model,
        system_prompt=cfg.get("programmer_system_prompt") or _default_programmer_prompt(profile),
        io=IOContract(output_schema={"type": "object", "required": ["program", "final_answer"]}),
        meta={
            "output_backfills": [
                {
                    "target": "program",
                    "strategy": "python_assignment_from_field",
                    "source": "final_answer",
                    "variable_name": "FINAL_ANSWER",
                    "sentinel": "DYNaforge synthetic program fallback",
                }
            ]
        },
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
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=[solver, programmer, program_exec, selector],
        base_edges=[
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
        ],
        subgraphs=[review_subgraph],
        gates=[
            GateSpec(
                gate_id="reason_execute_review_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="selector"),
                        TriggerExpr(
                            any_of=[
                                AtomicCondition(field="node.outputs.needs_review", op=ConditionOp.eq, value=True),
                                AtomicCondition(field="node.inputs.passed", op=ConditionOp.eq, value=False),
                                TriggerExpr(
                                    all_of=[
                                        AtomicCondition(field="node.inputs.program_executed_answer", op=ConditionOp.contains, value="."),
                                        TriggerExpr(
                                            any_of=[
                                                AtomicCondition(field="node.inputs.solver_answer", op=ConditionOp.contains, value="\\pi"),
                                                AtomicCondition(field="node.inputs.solver_answer", op=ConditionOp.contains, value="pi"),
                                                AtomicCondition(field="node.inputs.solver_answer", op=ConditionOp.contains, value="sqrt"),
                                                AtomicCondition(field="node.inputs.solver_answer", op=ConditionOp.contains, value="/"),
                                            ]
                                        ),
                                    ]
                                ),
                                AtomicCondition(
                                    field="report.confidence",
                                    op=ConditionOp.lt,
                                    value=float(cfg.get("review_threshold", max(profile.review_threshold, 0.78))),
                                ),
                            ]
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


def _build_code_generate_test_repair_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    cfg = context.design.pattern_override(WorkflowPattern.code_generate_test_repair.value)
    tool_cfg = dict(cfg.get("tool_nodes", {})).get("test_runner", {})
    test_runner = _build_tool_node_from_cfg(
        node_id="public_test_runner",
        role="PublicTestRunner",
        description="Execute the candidate implementation against the visible/public tests.",
        payload=tool_cfg,
    )
    retest_runner = test_runner.model_copy(
        deep=True,
        update={
            "node_id": "retest_runner",
            "role": "RetestRunner",
            "meta": {**dict(test_runner.meta), "execution_condition": "inputs.repair_changed == True"},
        },
    )
    coder = NodeSpec(
        node_id="coder",
        kind=NodeKind.agent,
        role="Coder",
        description="Write the requested implementation.",
        model=context.model,
        system_prompt=cfg.get("coder_system_prompt") or _default_coder_prompt(),
        io=IOContract(output_schema={"type": "object", "required": ["completion"]}),
    )
    reviewer = NodeSpec(
        node_id="reviewer",
        kind=NodeKind.evaluator,
        role="Reviewer",
        description="Diagnose why the candidate code failed the public tests.",
        model=context.review_model,
        system_prompt=cfg.get("reviewer_system_prompt") or _default_code_reviewer_prompt(),
        io=IOContract(output_schema={"type": "object", "required": ["corrected_completion"]}),
    )
    reviser = NodeSpec(
        node_id="reviser",
        kind=NodeKind.agent,
        role="Reviser",
        description="Rewrite the implementation using reviewer feedback and test results.",
        model=context.review_model,
        system_prompt=cfg.get("reviser_system_prompt") or _default_code_reviser_prompt(),
        io=IOContract(output_schema={"type": "object", "required": ["completion"]}),
        meta={
            "output_change_guard": {
                "output_path": "completion",
                "compare_against": ["inputs.candidate_code"],
                "normalize": "code",
                "record_field": "repair_changed",
                "reason_field": "repair_change_reason",
            }
        },
    )
    rewriter = NodeSpec(
        node_id="rewriter",
        kind=NodeKind.agent,
        role="Rewriter",
        description="Escalate to a fresh implementation when minimal repair stalls or still fails tests.",
        model=context.review_model,
        system_prompt=cfg.get("rewriter_system_prompt") or _default_code_rewriter_prompt(),
        io=IOContract(output_schema={"type": "object", "required": ["completion"]}),
        meta={
            "output_change_guard": {
                "output_path": "completion",
                "compare_against": ["inputs.candidate_code"],
                "normalize": "code",
                "record_field": "rewrite_changed",
                "reason_field": "rewrite_change_reason",
            }
        },
    )
    rewrite_test_runner = test_runner.model_copy(
        deep=True,
        update={
            "node_id": "rewrite_test_runner",
            "role": "RewriteTestRunner",
            "meta": {**dict(test_runner.meta), "execution_condition": "inputs.rewrite_changed == True"},
        },
    )
    meta = dict(context.meta)
    meta.update(
        {
            "workflow_pattern": WorkflowPattern.code_generate_test_repair.value,
            "preferred_answer_nodes": ["rewriter", "reviser", "coder"],
        }
    )
    return WorkflowBlueprint(
        task=context.task,
        budget=context.budget,
        base_nodes=[coder, test_runner],
        base_edges=[
            EdgeSpec(
                edge_id="edge_coder_to_public_test",
                src="coder",
                dst="public_test_runner",
                mapping={"candidate_code": "completion"},
            )
        ],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_fix",
                purpose="Repair code after public-test failure.",
                nodes=[reviewer, reviser, retest_runner],
                edges=[
                    EdgeSpec(
                        edge_id="edge_coder_to_reviewer",
                        src="coder",
                        dst="reviewer",
                        mapping={"candidate_code": "completion", "coder_notes": "notes"},
                    ),
                    EdgeSpec(
                        edge_id="edge_public_test_to_reviewer",
                        src="public_test_runner",
                        dst="reviewer",
                        mapping={"public_test_passed": "passed", "public_test_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_coder_to_reviser",
                        src="coder",
                        dst="reviser",
                        mapping={"candidate_code": "completion", "coder_notes": "notes"},
                    ),
                    EdgeSpec(
                        edge_id="edge_public_test_to_reviser",
                        src="public_test_runner",
                        dst="reviser",
                        mapping={"public_test_passed": "passed", "public_test_result": "result"},
                    ),
                    EdgeSpec(edge_id="edge_reviewer_to_reviser", src="reviewer", dst="reviser"),
                    EdgeSpec(
                        edge_id="edge_reviser_to_retest",
                        src="reviser",
                        dst="retest_runner",
                        mapping={
                            "candidate_code": "completion",
                            "repair_changed": "repair_changed",
                            "repair_change_reason": "repair_change_reason",
                        },
                    ),
                ],
                entry_nodes=["reviewer"],
                exit_nodes=["reviser", "retest_runner"],
                default_enabled=False,
            ),
            SubgraphSpec(
                subgraph_id="sg_escalate",
                purpose="Escalate to a stronger rewrite when minimal repair makes no change or still fails tests.",
                nodes=[rewriter, rewrite_test_runner],
                edges=[
                    EdgeSpec(
                        edge_id="edge_coder_to_rewriter",
                        src="coder",
                        dst="rewriter",
                        mapping={"candidate_code": "completion", "coder_notes": "notes"},
                    ),
                    EdgeSpec(
                        edge_id="edge_public_test_to_rewriter",
                        src="public_test_runner",
                        dst="rewriter",
                        mapping={"public_test_passed": "passed", "public_test_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviewer_to_rewriter",
                        src="reviewer",
                        dst="rewriter",
                        mapping={"reviewer_result": "*"},
                    ),
                    EdgeSpec(
                        edge_id="edge_reviser_to_rewriter",
                        src="reviser",
                        dst="rewriter",
                        mapping={
                            "repair_completion": "completion",
                            "repair_changed": "repair_changed",
                            "repair_change_reason": "repair_change_reason",
                        },
                    ),
                    EdgeSpec(
                        edge_id="edge_retest_to_rewriter",
                        src="retest_runner",
                        dst="rewriter",
                        mapping={"retest_passed": "passed", "retest_result": "result"},
                    ),
                    EdgeSpec(
                        edge_id="edge_rewriter_to_rewrite_test",
                        src="rewriter",
                        dst="rewrite_test_runner",
                        mapping={
                            "candidate_code": "completion",
                            "rewrite_changed": "rewrite_changed",
                            "rewrite_change_reason": "rewrite_change_reason",
                        },
                    ),
                ],
                entry_nodes=["rewriter"],
                exit_nodes=["rewriter", "rewrite_test_runner"],
                default_enabled=False,
            ),
        ],
        gates=[
            GateSpec(
                gate_id="code_generate_test_repair_gate",
                trigger=TriggerExpr(
                    all_of=[
                        AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="public_test_runner"),
                        AtomicCondition(field="node.outputs.passed", op=ConditionOp.eq, value=False),
                    ]
                ),
                enable_subgraphs=["sg_fix"],
                max_activations=1,
            ),
            GateSpec(
                gate_id="code_generate_rewrite_gate",
                trigger=TriggerExpr(
                    any_of=[
                        TriggerExpr(
                            all_of=[
                                AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="reviser"),
                                AtomicCondition(field="node.outputs.repair_changed", op=ConditionOp.eq, value=False),
                            ]
                        ),
                        TriggerExpr(
                            all_of=[
                                AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="retest_runner"),
                                AtomicCondition(field="node.outputs.passed", op=ConditionOp.eq, value=False),
                            ]
                        ),
                    ]
                ),
                enable_subgraphs=["sg_escalate"],
                max_activations=1,
            ),
        ],
        eval=EvalSpec(),
        meta=meta,
    )


def _build_repo_transfer_validate_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
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


def _build_closed_loop_design_validate_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
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


def _build_specialist_assembly_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
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


def _build_synthesized_hybrid_blueprint(context: PatternBuildContext) -> WorkflowBlueprint:
    profile = context.design.task_profile
    if profile.requires_specialists:
        return _build_specialist_assembly_blueprint(context)
    if profile.requires_closed_loop:
        return _build_closed_loop_design_validate_blueprint(context)
    if profile.requires_repo_search or profile.requires_web_research:
        return _build_repo_transfer_validate_blueprint(context)
    if profile.requires_test_execution or profile.answer_mode == "code":
        return _build_code_generate_test_repair_blueprint(context)
    if profile.requires_tool_execution or profile.answer_mode == "exact_answer":
        return _build_reason_execute_select_blueprint(context)
    return _build_direct_answer_blueprint(context)


def _build_tool_node_from_cfg(node_id: str, role: str, description: str, payload: Mapping[str, Any]) -> NodeSpec:
    if not payload:
        raise ValueError(f"Missing tool-node configuration for {node_id}")
    sandbox_payload = payload.get("sandbox")
    io_payload = payload.get("io", {})
    return NodeSpec(
        node_id=node_id,
        kind=NodeKind.tool,
        role=str(payload.get("role", role)),
        description=str(payload.get("description", description)),
        tools=[ToolRef.model_validate(tool) for tool in payload.get("tools", [])],
        sandbox=SandboxSpec.model_validate(sandbox_payload) if sandbox_payload else None,
        io=IOContract.model_validate(io_payload or {}),
        meta=dict(payload.get("meta", {})),
    )


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


def _default_exact_solver_prompt(profile: TaskProfile) -> str:
    del profile
    return (
        "You are an exact-answer problem solver. Solve the task in task.description. "
        "Prefer exact symbolic or rational forms over decimal approximations unless the problem explicitly asks for a decimal. "
        "Check your candidate answer against the explicit constraints in the problem statement before returning it. "
        "If your derivation depends on an unverified assumption, or your answer may violate a stated constraint, set output.requires_review=true and explain why. "
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


def _default_coder_prompt() -> str:
    return (
        "You are a code-generation node. Write the final Python implementation for the task in task.description. "
        "Preserve the required function signature exactly, prefer the simplest correct implementation, and mentally check it against the visible examples or obvious edge cases before returning it. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )


def _default_code_reviewer_prompt() -> str:
    return (
        "You are a code reviewer for failed public tests. "
        "Use the failing public_test_result plus the original candidate_code to diagnose the smallest real bug. "
        "Preserve any behavior that is already consistent with the prompt and the visible tests. "
        "When a failure is caused by indexing, imports, recursion, or syntax, say that explicitly in output.review_notes. "
        "Return JSON with output.review_notes, output.corrected_completion, confidence, and summary. "
        "Do not use markdown fences."
    )


def _default_code_reviser_prompt() -> str:
    return (
        "You are a code reviser. Use the original task, failed candidate_code, public_test_result, and reviewer notes. "
        "Treat reviewer.corrected_completion as the strongest starting point and make the smallest coherent repair that fixes the concrete failing behavior without rewriting unrelated logic. "
        "Preserve the required function name and signature exactly. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )


def _default_code_rewriter_prompt() -> str:
    return (
        "You are an escalation rewrite node for a code-generation workflow. "
        "The minimal repair path either made no meaningful change or still failed the visible tests. "
        "Use the original task, failed candidate_code, public_test_result, reviewer notes, and any retest_result to produce a fresh but still concise implementation. "
        "Preserve the required function name and signature exactly. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )


def _default_repo_scout_prompt() -> str:
    return (
        "You are a repo-and-tool scout for workflow transfer. Use available web and tool search when needed. "
        "Identify the best candidate repo or workflow, justify the choice, and propose an initial execution plan. "
        "Return JSON with output.selected_repo, output.repo_candidates, output.analysis_config, output.summary, "
        "output.requires_review, and confidence."
    )


def _default_artifact_validator_prompt() -> str:
    return (
        "You are an artifact validator. Judge whether the produced artifact bundle matches the task intent, "
        "required artifacts, and scientific constraints. "
        "Return JSON with output.methodology_faithfulness, output.artifact_completeness, output.biological_plausibility, "
        "output.overall_verdict, output.should_refine, output.concerns, confidence, and summary."
    )


def _default_refiner_prompt(task_label: str) -> str:
    return (
        f"You are refining a {task_label} workflow after validation feedback. "
        "Update the plan conservatively, keep the task intent fixed, and return JSON with "
        "output.selected_repo, output.analysis_config, output.design_config, output.selected_specialists, "
        "output.handoff_contract, output.summary, and confidence. "
        "Include only the fields that make sense for the task."
    )


def _default_closed_loop_planner_prompt() -> str:
    return (
        "You are planning a closed-loop design-and-validation workflow under explicit constraints. "
        "Search for relevant evidence or tool guidance when needed, then return JSON with "
        "output.design_config, output.validation_plan, output.summary, output.requires_review, and confidence."
    )


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
