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
    TriggerExpr,
    WorkflowBlueprint,
)
from dynaforge.workflows.design import WorkflowPattern

from dynaforge.workflows.patterns._common import _build_tool_node_from_cfg

if TYPE_CHECKING:
    from dynaforge.workflows.patterns import PatternBuildContext

JsonDict = Dict[str, Any]


def build(context: PatternBuildContext) -> WorkflowBlueprint:
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
                        mapping={
                            "public_test_passed": "passed",
                            "public_test_result": "result",
                            "public_failure_summary": "failure_summary",
                            "public_failing_assertion": "failing_assertion",
                            "public_failing_call": "failing_call",
                            "public_assertion_operator": "assertion_operator",
                            "public_expected_repr": "expected_repr",
                            "public_failing_actual_repr": "failing_actual_repr",
                            "public_probe_status": "probe_status",
                            "public_probe_message": "probe_message",
                            "public_exception_type": "exception_type",
                            "public_exception_message": "exception_message",
                        },
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
                        mapping={
                            "public_test_passed": "passed",
                            "public_test_result": "result",
                            "public_failure_summary": "failure_summary",
                            "public_failing_assertion": "failing_assertion",
                            "public_failing_call": "failing_call",
                            "public_assertion_operator": "assertion_operator",
                            "public_expected_repr": "expected_repr",
                            "public_failing_actual_repr": "failing_actual_repr",
                            "public_probe_status": "probe_status",
                            "public_probe_message": "probe_message",
                            "public_exception_type": "exception_type",
                            "public_exception_message": "exception_message",
                        },
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
                        mapping={
                            "public_test_passed": "passed",
                            "public_test_result": "result",
                            "public_failure_summary": "failure_summary",
                            "public_failing_assertion": "failing_assertion",
                            "public_failing_call": "failing_call",
                            "public_assertion_operator": "assertion_operator",
                            "public_expected_repr": "expected_repr",
                            "public_failing_actual_repr": "failing_actual_repr",
                            "public_probe_status": "probe_status",
                            "public_probe_message": "probe_message",
                            "public_exception_type": "exception_type",
                            "public_exception_message": "exception_message",
                        },
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
                        mapping={
                            "retest_passed": "passed",
                            "retest_result": "result",
                            "retest_failure_summary": "failure_summary",
                            "retest_failing_assertion": "failing_assertion",
                            "retest_failing_call": "failing_call",
                            "retest_assertion_operator": "assertion_operator",
                            "retest_expected_repr": "expected_repr",
                            "retest_failing_actual_repr": "failing_actual_repr",
                            "retest_probe_status": "probe_status",
                            "retest_probe_message": "probe_message",
                            "retest_exception_type": "exception_type",
                            "retest_exception_message": "exception_message",
                        },
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


# ---------------------------------------------------------------------------
# Helpers specific to code_generate_test_repair
# ---------------------------------------------------------------------------


def _default_coder_prompt() -> str:
    return (
        "You are a code-generation node. Write the final Python implementation for the task in task.description. "
        "Preserve the required function signature exactly, prefer the simplest correct implementation, and mentally check it against the visible examples or obvious edge cases before returning it. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )


def _default_code_reviewer_prompt() -> str:
    return (
        "You are a code reviewer for failed public tests. "
        "Use public_failure_summary, public_failing_assertion, public_exception_type, public_exception_message, "
        "the failing public_test_result, and the original candidate_code to diagnose the smallest real bug. "
        "Preserve any behavior that is already consistent with the prompt and the visible tests. "
        "When a failure is caused by indexing, imports, recursion, or syntax, say that explicitly in output.review_notes. "
        "Return JSON with output.review_notes, output.corrected_completion, confidence, and summary. "
        "Do not use markdown fences."
    )


def _default_code_reviser_prompt() -> str:
    return (
        "You are a code reviser. Use the original task, failed candidate_code, public_failure_summary, public_failing_assertion, "
        "public_exception_type, public_exception_message, public_test_result, and reviewer notes. "
        "Treat reviewer.corrected_completion as the strongest starting point and make the smallest coherent repair that fixes the concrete failing behavior without rewriting unrelated logic. "
        "Preserve the required function name and signature exactly. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )


def _default_code_rewriter_prompt() -> str:
    return (
        "You are an escalation rewrite node for a code-generation workflow. "
        "The minimal repair path either made no meaningful change or still failed the visible tests. "
        "Use the original task, failed candidate_code, public_failure_summary, public_failing_assertion, reviewer notes, "
        "retest_failure_summary, retest_failing_assertion, and any retest_result to produce a fresh but still concise implementation. "
        "Preserve the required function name and signature exactly. "
        "Return JSON with output.completion, output.notes, confidence, and summary. Do not use markdown fences."
    )
