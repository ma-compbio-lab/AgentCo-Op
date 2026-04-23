"""Gate evaluation + local graph patching.

Gates watch node results for specific failure signals and emit one of a
bounded set of `GraphPatch` ops. Patches are *local* repairs — never a
global re-search — and are capped by `max_activations`,
`max_total_activations`, and `max_same_node_repairs`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentcoop.core.schema import (
    EdgeSpec,
    GatePolicy,
    NodeResult,
    NodeSpec,
    RunState,
    WorkflowBlueprint,
)


class GraphPatchOp(str, Enum):
    RETRY_NODE = "retry_node"
    REPLACE_BACKEND = "replace_backend"
    ADD_REVIEWER = "add_reviewer"
    ADD_RETRIEVAL = "add_retrieval"
    ADD_SPECIALIST = "add_specialist"
    ADD_TEST_NODE = "add_test_node"
    ADD_FORMATTER = "add_formatter"
    REROUTE_TO_SIMPLE = "reroute_to_simple"
    ESCALATE_HUMAN = "escalate_human"
    TERMINATE_WITH_UNCERTAINTY = "terminate_with_uncertainty"


@dataclass
class GraphPatch:
    op: GraphPatchOp
    reason: str = ""
    target_node: str | None = None
    new_node: NodeSpec | None = None
    new_edges: list[EdgeSpec] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateLimits:
    max_total_activations: int = 5
    max_same_node_repairs: int = 2
    max_graph_patches: int = 3
    terminate_on_budget_fraction: float = 0.9


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_gates(
    blueprint: WorkflowBlueprint,
    state: RunState,
    node: NodeSpec,
    result: NodeResult,
) -> list[tuple[GatePolicy, str]]:
    """Return (policy, reason) for every gate that fires on this result."""
    fired: list[tuple[GatePolicy, str]] = []
    for policy in blueprint.gate_policies:
        activations = state.gate_activations.get(policy.name, 0)
        if activations >= policy.max_activations:
            continue
        if policy.scope == "node" and policy.node_ids and node.node_id not in policy.node_ids:
            continue
        reason = _match_trigger(policy, node, result, state, blueprint)
        if reason:
            fired.append((policy, reason))
    return fired


def _match_trigger(
    policy: GatePolicy,
    node: NodeSpec,
    result: NodeResult,
    state: RunState,
    blueprint: WorkflowBlueprint,
) -> str | None:
    trigger = policy.trigger

    if trigger == "schema_invalid":
        if not _matches_schema(result.output, node.output_schema):
            return "output did not match declared schema"
        return None

    if trigger == "low_confidence":
        thr = policy.threshold if policy.threshold is not None else 0.5
        if result.confidence is not None and result.confidence < thr:
            return f"confidence {result.confidence:.2f} < {thr}"
        return None

    if trigger == "test_failure":
        failed = int(result.metrics.get("tests_failed", 0))
        returncode = result.metrics.get("returncode")
        if failed > 0 or (returncode is not None and returncode != 0):
            return "unit tests reported failure"
        return None

    if trigger == "tool_error":
        if not result.ok or any(
            "toolerror" in e.lower() or "timeout" in e.lower() or "exit=" in e
            for e in result.errors
        ):
            return f"tool error: {';'.join(result.errors)[:120]}"
        return None

    if trigger == "evidence_missing":
        if not result.evidence and result.output.get("answer"):
            return "answer produced without evidence"
        return None

    if trigger == "specialist_disagreement":
        # Simple check: if the blackboard carries two answers that diverge.
        answers = result.output.get("candidate_answers", [])
        if isinstance(answers, list) and len({str(a) for a in answers}) > 1:
            return "specialists disagree"
        return None

    if trigger == "budget_near_limit":
        budget = blueprint.budget
        frac_tokens = (state.tokens_used / budget.max_tokens) if budget.max_tokens else 0
        frac_cost = (
            (state.cost_used_usd / budget.max_cost_usd)
            if budget.max_cost_usd
            else 0
        )
        thr = policy.threshold if policy.threshold is not None else 0.9
        if max(frac_tokens, frac_cost) >= thr:
            return f"budget usage {max(frac_tokens, frac_cost):.2f} >= {thr}"
        return None

    if trigger == "risk_escalation":
        if result.metrics.get("risk_escalation"):
            return "risk flag raised"
        return None

    # --- benchmarks.md §5 + case_study.md §4 triggers ------------------------
    if trigger in ("answer_format_invalid", "format_invalid"):
        if result.output.get("format_invalid") or result.output.get("format_error"):
            return "final output format invalid"
        if not _matches_schema(result.output, node.output_schema):
            return "output did not match declared schema"
        return None

    if trigger == "solver_disagreement":
        answers = result.output.get("solver_answers") or result.output.get("candidate_answers")
        if isinstance(answers, list) and len({str(a) for a in answers}) > 1:
            return "solver answers disagree"
        return None

    if trigger == "method_disagreement":
        flag = result.metrics.get("method_disagreement") or result.output.get("method_disagreement")
        if flag:
            return f"method disagreement flagged: {flag}"
        return None

    if trigger == "symbolic_check_fail":
        if result.metrics.get("symbolic_check") is False or "symbolic_check_fail" in result.errors:
            return "symbolic equivalence check failed"
        return None

    if trigger == "boxed_answer_missing":
        ans = result.output.get("final_answer") or result.output.get("answer") or ""
        if isinstance(ans, str) and r"\boxed{" not in ans and ans.strip() == "":
            return "boxed answer missing"
        return None

    if trigger == "domain_mismatch":
        flag = result.metrics.get("domain_mismatch") or result.output.get("domain_mismatch")
        if flag:
            return "routing / domain mismatch flagged"
        return None

    if trigger == "numeric_inconsistency":
        if result.metrics.get("numeric_inconsistency"):
            return "numeric arithmetic inconsistency"
        return None

    if trigger == "multi_span_conflict":
        if result.metrics.get("multi_span_conflict"):
            return "multi-span answer conflict"
        return None

    if trigger == "unusually_complex_problem":
        if result.confidence is not None and result.confidence < 0.3:
            return "low confidence with high-difficulty profile"
        return None

    if trigger == "arithmetic_fail":
        if result.metrics.get("arithmetic_verifier") is False:
            return "arithmetic verifier disagreed"
        return None

    if trigger == "answer_extraction_fail":
        if result.output.get("final_answer") in (None, ""):
            return "answer extraction failed"
        return None

    if trigger in ("syntax_error", "runtime_error", "public_or_generated_test_failure"):
        failed = int(result.metrics.get("tests_failed", 0))
        returncode = result.metrics.get("returncode")
        err_text = " ".join(result.errors).lower()
        if trigger == "syntax_error" and "syntaxerror" in err_text:
            return "syntax error"
        if trigger == "runtime_error" and ("traceback" in err_text or "error:" in err_text):
            return "runtime error"
        if trigger == "public_or_generated_test_failure" and (failed > 0 or (returncode is not None and returncode != 0)):
            return "public/generated test failure"
        return None

    if trigger == "timeout":
        if "timeout" in " ".join(result.errors).lower():
            return "sandbox timeout"
        return None

    if trigger == "answer_unsupported":
        if result.output.get("final_answer") and not result.evidence:
            return "answer lacks evidence"
        return None

    if trigger == "high_disagreement":
        if result.metrics.get("disagreement", 0) >= (policy.threshold or 0.5):
            return "cross-specialist disagreement above threshold"
        return None

    # --- Case Study 1 gates --------------------------------------------------
    if trigger == "design_ambiguous":
        if result.output.get("design_ambiguous"):
            return "experimental design ambiguous"
        return None

    if trigger == "too_few_markers":
        n = result.metrics.get("num_markers") or result.output.get("num_markers") or 0
        if isinstance(n, (int, float)) and n < (policy.threshold or 5):
            return f"too few markers: {n}"
        return None

    if trigger == "gene_mapping_low":
        frac = result.metrics.get("mapping_rate") or result.output.get("mapping_rate")
        if isinstance(frac, (int, float)) and frac < (policy.threshold or 0.7):
            return f"gene mapping rate low: {frac:.2f}"
        return None

    if trigger == "enrichment_empty":
        if (result.metrics.get("enrichment_rows") or 0) == 0:
            return "no significant ORA result"
        return None

    if trigger == "geneagent_unsupported_claim":
        if result.metrics.get("unsupported_claims", 0) > 0:
            return "verifier rejected some claims"
        return None

    # --- Case Study 2 gates --------------------------------------------------
    if trigger == "model_env_fail":
        err_text = " ".join(result.errors).lower()
        if "cuda" in err_text or "importerror" in err_text or "modulenotfounderror" in err_text:
            return "model environment import or CUDA failure"
        return None

    if trigger == "gene_universe_mismatch":
        if result.metrics.get("gene_universe_coverage", 1.0) < (policy.threshold or 0.9):
            return "model output genes differ from evaluator genes"
        return None

    if trigger == "prediction_schema_invalid":
        required = ("perturbation", "predicted_delta", "genes")
        if not all(k in result.output for k in required):
            return "prediction missing required schema keys"
        return None

    if trigger == "metric_outlier":
        if result.metrics.get("metric_outlier"):
            return "metric flagged as outlier"
        return None

    if trigger == "simple_baseline_beats_all":
        if result.metrics.get("simple_baseline_wins"):
            return "simple baseline wins validation"
        return None

    if trigger == "model_disagreement_high":
        if result.metrics.get("model_disagreement", 0) >= (policy.threshold or 0.5):
            return "models disagree strongly"
        return None

    return None


def _matches_schema(output: dict[str, Any], schema: dict[str, Any] | None) -> bool:
    """Cheap v0 schema check: required keys present and typed correctly."""
    if not schema:
        return True
    required = schema.get("required") or list(schema.get("properties", {}).keys())
    if isinstance(required, list):
        for key in required:
            if key not in output:
                return False
    props = schema.get("properties", {})
    if isinstance(props, dict):
        for key, prop in props.items():
            if key in output and "type" in prop:
                if not _matches_type(output[key], prop["type"]):
                    return False
    return True


def _matches_type(value: Any, type_name: str) -> bool:
    mapping = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected = mapping.get(type_name)
    if expected is None:
        return True
    return isinstance(value, expected)


# ---------------------------------------------------------------------------
# Patch planning + application
# ---------------------------------------------------------------------------


def plan_patch(policy: GatePolicy, node: NodeSpec, reason: str) -> GraphPatch:
    op = GraphPatchOp(policy.action) if _is_known_op(policy.action) else GraphPatchOp.RETRY_NODE
    patch = GraphPatch(op=op, reason=reason, target_node=node.node_id)

    if op is GraphPatchOp.ADD_REVIEWER:
        patch.new_node = NodeSpec(
            node_id=f"{node.node_id}_reviewer",
            role="reviewer",
            backend="llm",
            memory_scope="shared_read",
        )
        patch.new_edges = [EdgeSpec(source=node.node_id, target=patch.new_node.node_id)]
    elif op is GraphPatchOp.ADD_RETRIEVAL:
        patch.new_node = NodeSpec(
            node_id=f"{node.node_id}_retrieval",
            role="retriever",
            backend="mcp",
            memory_scope="shared_read",
        )
        patch.new_edges = [EdgeSpec(source=patch.new_node.node_id, target=node.node_id)]
    elif op is GraphPatchOp.ADD_SPECIALIST:
        patch.new_node = NodeSpec(
            node_id=f"{node.node_id}_specialist",
            role="specialist",
            backend="llm",
            memory_scope="shared_read",
        )
        patch.new_edges = [EdgeSpec(source=node.node_id, target=patch.new_node.node_id)]
    elif op is GraphPatchOp.ADD_TEST_NODE:
        patch.new_node = NodeSpec(
            node_id=f"{node.node_id}_tests",
            role="tool",
            backend="python_sandbox",
        )
        patch.new_edges = [EdgeSpec(source=node.node_id, target=patch.new_node.node_id)]
    elif op is GraphPatchOp.ADD_FORMATTER:
        patch.new_node = NodeSpec(
            node_id=f"{node.node_id}_formatter",
            role="formatter",
            backend="llm",
        )
        patch.new_edges = [EdgeSpec(source=node.node_id, target=patch.new_node.node_id)]
    return patch


def _is_known_op(action: str) -> bool:
    try:
        GraphPatchOp(action)
        return True
    except ValueError:
        return False


def apply_patch(
    blueprint: WorkflowBlueprint,
    state: RunState,
    patch: GraphPatch,
    limits: GateLimits,
) -> bool:
    """Mutate the blueprint + state in place. Returns True if applied."""

    if state.patches_applied >= limits.max_graph_patches:
        return False

    if patch.op is GraphPatchOp.TERMINATE_WITH_UNCERTAINTY:
        state.terminated = True
        state.termination_reason = patch.reason or "terminate_with_uncertainty"
        state.patches_applied += 1
        return True

    if patch.op is GraphPatchOp.ESCALATE_HUMAN:
        review = NodeSpec(
            node_id=f"{patch.target_node}_human_review" if patch.target_node else "human_review",
            role="human_review",
            backend="human_review",
        )
        blueprint.nodes.append(review)
        if patch.target_node:
            blueprint.edges.append(EdgeSpec(source=patch.target_node, target=review.node_id))
        state.patches_applied += 1
        return True

    if patch.op is GraphPatchOp.RETRY_NODE:
        if patch.target_node is None:
            return False
        used = state.node_retries.get(patch.target_node, 0)
        if used >= limits.max_same_node_repairs:
            return False
        state.node_retries[patch.target_node] = used + 1
        state.patches_applied += 1
        return True

    if patch.op is GraphPatchOp.REPLACE_BACKEND:
        if patch.target_node is None:
            return False
        for i, n in enumerate(blueprint.nodes):
            if n.node_id == patch.target_node:
                new_backend = patch.payload.get("backend", "llm")
                blueprint.nodes[i] = n.model_copy(update={"backend": new_backend})
                state.patches_applied += 1
                return True
        return False

    if patch.op is GraphPatchOp.REROUTE_TO_SIMPLE:
        solver = NodeSpec(node_id="fallback_solver", role="solver", backend="llm")
        blueprint.nodes.append(solver)
        blueprint.edges.append(EdgeSpec(source=patch.target_node or "", target=solver.node_id))
        state.patches_applied += 1
        return True

    if patch.new_node is not None:
        if any(n.node_id == patch.new_node.node_id for n in blueprint.nodes):
            return False
        blueprint.nodes.append(patch.new_node)
        blueprint.edges.extend(patch.new_edges)
        state.patches_applied += 1
        return True

    return False


__all__ = [
    "GraphPatch",
    "GraphPatchOp",
    "GateLimits",
    "evaluate_gates",
    "plan_patch",
    "apply_patch",
]
