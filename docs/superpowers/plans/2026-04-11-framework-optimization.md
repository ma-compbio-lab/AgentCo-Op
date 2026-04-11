# Framework Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement three generalizable framework improvements: review-as-verification, adaptive repair with retry-before-rewrite, and graceful degradation for contract violations.

**Architecture:** Add `ReviewPolicy` and `RetryPolicy` enums to the IR schema, implement policy-aware execution logic in the executor, add partial output extraction in validation, and update pattern builders to use the new policies.

**Tech Stack:** Python 3.11, Pydantic v2, pytest

---

### Task 1: Add ReviewPolicy and RetryPolicy enums to IR schema

**Files:**
- Modify: `src/agentcoop/ir/schema.py` (add enums + SubgraphSpec fields)
- Modify: `tests/test_ir_schema.py` (add tests)

- [ ] **Step 1: Write failing tests for new schema fields**

Add to `tests/test_ir_schema.py`:

```python
from agentcoop.ir.schema import ReviewPolicy, RetryPolicy, SubgraphSpec, NodeSpec, NodeKind, EdgeSpec


def test_review_policy_defaults_to_verify_then_correct() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.agent, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
    )
    assert sg.review_policy == ReviewPolicy.verify_then_correct


def test_retry_policy_defaults_to_retry_then_escalate() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.agent, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
    )
    assert sg.retry_policy == RetryPolicy.retry_then_escalate


def test_subgraph_accepts_explicit_policies() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.agent, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
        review_policy=ReviewPolicy.re_solve,
        retry_policy=RetryPolicy.escalate_immediately,
        max_retries=2,
    )
    assert sg.review_policy == ReviewPolicy.re_solve
    assert sg.retry_policy == RetryPolicy.escalate_immediately
    assert sg.max_retries == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ir_schema.py -k "test_review_policy or test_retry_policy or test_subgraph_accepts" -v`
Expected: FAIL with ImportError for ReviewPolicy/RetryPolicy

- [ ] **Step 3: Implement the enums and SubgraphSpec fields**

In `src/agentcoop/ir/schema.py`, add after the `SkillPromptMode` enum (line ~62):

```python
class ReviewPolicy(str, Enum):
    verify_then_correct = "verify_then_correct"
    re_solve = "re_solve"


class RetryPolicy(str, Enum):
    retry_then_escalate = "retry_then_escalate"
    escalate_immediately = "escalate_immediately"
```

In `SubgraphSpec` class (line ~309), add three fields after `default_enabled`:

```python
    review_policy: ReviewPolicy = Field(default=ReviewPolicy.verify_then_correct)
    retry_policy: RetryPolicy = Field(default=RetryPolicy.retry_then_escalate)
    max_retries: int = Field(default=1, ge=0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ir_schema.py -k "test_review_policy or test_retry_policy or test_subgraph_accepts" -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 182 passed

- [ ] **Step 6: Commit**

```bash
git add src/agentcoop/ir/schema.py tests/test_ir_schema.py
git commit -m "feat: add ReviewPolicy and RetryPolicy enums to SubgraphSpec"
```

---

### Task 2: Implement extract_partial_outputs in validation.py

**Files:**
- Modify: `src/agentcoop/runtime/validation.py`
- Create: `tests/test_partial_outputs.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_partial_outputs.py`:

```python
from agentcoop.runtime.validation import extract_partial_outputs


def test_extract_from_text_field_with_embedded_json() -> None:
    outputs = {"text": '{"output": {"final_answer": "42", "notes": "computed"}}'}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "42"
    assert result["_degraded"] is True


def test_extract_from_nested_dict() -> None:
    outputs = {"wrapper": {"inner": {"final_answer": "7", "extra": "data"}}}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "7"
    assert result["_degraded"] is True


def test_returns_original_when_no_schema() -> None:
    outputs = {"text": "hello"}
    result = extract_partial_outputs(outputs, None)
    assert result == {"text": "hello"}
    assert "_degraded" not in result


def test_returns_original_when_no_required_fields() -> None:
    outputs = {"text": "hello"}
    schema = {"type": "object"}
    result = extract_partial_outputs(outputs, schema)
    assert result == {"text": "hello"}


def test_preserves_existing_fields_and_adds_recovered() -> None:
    outputs = {"confidence": 0.5, "text": '{"final_answer": "99"}'}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "99"
    assert result["confidence"] == 0.5
    assert result["_degraded"] is True


def test_handles_truncated_json_gracefully() -> None:
    outputs = {"text": '{"final_answer": "42", "notes": "trunc'}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    # Should still try to recover final_answer via regex or partial parse
    assert "_degraded" not in result or result.get("final_answer") is not None


def test_handles_completely_missing_fields() -> None:
    outputs = {"text": "no json here at all"}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert "final_answer" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_partial_outputs.py -v`
Expected: FAIL with ImportError for extract_partial_outputs

- [ ] **Step 3: Implement extract_partial_outputs**

In `src/agentcoop/runtime/validation.py`, add:

```python
import json
import re
from typing import Any, Dict, Mapping, Optional


def extract_partial_outputs(
    outputs: Dict[str, Any],
    schema: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Attempt to recover required fields from malformed node outputs.

    When output validation fails, this function tries to extract whatever
    required fields exist — from embedded JSON strings, nested dicts, etc.
    Returns the enriched outputs with a ``_degraded: True`` marker if any
    fields were recovered from non-standard locations.
    """
    if not schema or not isinstance(schema, Mapping):
        return outputs

    required = schema.get("required", [])
    if not required or not isinstance(required, list):
        return outputs

    # Check which required fields are already present at top level
    missing = [field for field in required if field not in outputs]
    if not missing:
        return outputs

    recovered: Dict[str, Any] = {}

    # Strategy 1: Parse JSON from "text" field
    text_value = outputs.get("text")
    if isinstance(text_value, str):
        parsed = _try_parse_json(text_value)
        if isinstance(parsed, dict):
            _collect_fields(parsed, missing, recovered)

    # Strategy 2: Recursively search nested dicts
    still_missing = [f for f in missing if f not in recovered]
    if still_missing:
        _collect_fields(outputs, still_missing, recovered)

    if not recovered:
        return outputs

    result = dict(outputs)
    result.update(recovered)
    result["_degraded"] = True
    return result


def _try_parse_json(text: str) -> Any:
    """Try to parse JSON from a string, handling common wrapping patterns."""
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting outermost braces
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _collect_fields(
    source: Any,
    fields: list,
    target: Dict[str, Any],
    max_depth: int = 5,
) -> None:
    """Recursively search source dict for required fields."""
    if max_depth <= 0 or not isinstance(source, Mapping):
        return
    for field in fields:
        if field in target:
            continue
        if field in source and source[field] is not None:
            target[field] = source[field]
    still_missing = [f for f in fields if f not in target]
    if not still_missing:
        return
    for value in source.values():
        if isinstance(value, Mapping):
            _collect_fields(value, still_missing, target, max_depth - 1)
            still_missing = [f for f in fields if f not in target]
            if not still_missing:
                return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_partial_outputs.py -v`
Expected: PASS (or fix the truncated JSON test if needed)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/agentcoop/runtime/validation.py tests/test_partial_outputs.py
git commit -m "feat: add extract_partial_outputs for graceful degradation"
```

---

### Task 3: Implement review policy logic in executor

**Files:**
- Modify: `src/agentcoop/runtime/executor.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing test for verify_then_correct — accept case**

Add to `tests/test_runtime.py`:

```python
from agentcoop.ir.schema import (
    AtomicCondition,
    ConditionOp,
    ReviewPolicy,
    SubgraphSpec,
)


class ReviewerAcceptsLLMClient:
    """Reviewer returns verdict=accept, reviser should be skipped."""

    def complete(self, model, messages, *, response_format=None):
        content = messages[-1]["content"]
        if "Node ID: reviewer" in content:
            return {
                "content": '{"output":{"verdict":"accept","reason":"answer looks correct"},"confidence":0.95,"summary":"accepted"}',
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }
        if "Node ID: reviser" in content:
            # Should NOT be called when reviewer accepts
            return {
                "content": '{"output":{"final_answer":"WRONG-REVISER-RAN"},"confidence":0.5,"summary":"bad"}',
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }
        # Default for solver/selector
        return {
            "content": '{"output":{"final_answer":"42","needs_review":true},"confidence":0.3,"summary":"ok"}',
            "usage": {"prompt_tokens": 4, "completion_tokens": 4},
        }


def test_review_policy_verify_then_correct_accept() -> None:
    """When reviewer verdict is 'accept', reviser should be skipped."""
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="review-accept", title="Review", description="Desc"),
        budget=BudgetSpec(max_total_usd=1.0),
        base_nodes=[
            NodeSpec(node_id="solver", kind=NodeKind.agent, role="Solver",
                     model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
        ],
        base_edges=[],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_review",
                purpose="Review",
                review_policy=ReviewPolicy.verify_then_correct,
                nodes=[
                    NodeSpec(node_id="reviewer", kind=NodeKind.evaluator, role="Reviewer",
                             model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
                    NodeSpec(node_id="reviser", kind=NodeKind.agent, role="Reviser",
                             model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
                ],
                edges=[
                    EdgeSpec(edge_id="e_solver_reviewer", src="solver", dst="reviewer"),
                    EdgeSpec(edge_id="e_reviewer_reviser", src="reviewer", dst="reviser"),
                ],
                entry_nodes=["reviewer"],
                exit_nodes=["reviser"],
                default_enabled=False,
            ),
        ],
        gates=[
            GateSpec(
                gate_id="review_gate",
                trigger=TriggerExpr(any_of=[
                    AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="solver"),
                ]),
                enable_subgraphs=["sg_review"],
                max_activations=1,
            ),
        ],
    )

    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=ReviewerAcceptsLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    node_ids_executed = [t.node_id for t in report.traces]
    assert "reviewer" in node_ids_executed
    # Reviser should be skipped (status="skipped") when reviewer accepts
    reviser_traces = [t for t in report.traces if t.node_id == "reviser"]
    assert len(reviser_traces) == 1
    assert reviser_traces[0].status == "skipped"
```

- [ ] **Step 2: Write failing test for verify_then_correct — reject case**

Add to `tests/test_runtime.py`:

```python
class ReviewerRejectsLLMClient:
    """Reviewer returns verdict=reject, reviser should run with correction."""

    def complete(self, model, messages, *, response_format=None):
        content = messages[-1]["content"]
        if "Node ID: reviewer" in content:
            return {
                "content": '{"output":{"verdict":"reject","correction":"99","reason":"calculation error"},"confidence":0.8,"summary":"rejected"}',
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }
        if "Node ID: reviser" in content:
            return {
                "content": '{"output":{"final_answer":"99"},"confidence":0.9,"summary":"corrected"}',
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
            }
        return {
            "content": '{"output":{"final_answer":"42","needs_review":true},"confidence":0.3,"summary":"ok"}',
            "usage": {"prompt_tokens": 4, "completion_tokens": 4},
        }


def test_review_policy_verify_then_correct_reject() -> None:
    """When reviewer verdict is 'reject', reviser should run normally."""
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="review-reject", title="Review", description="Desc"),
        budget=BudgetSpec(max_total_usd=1.0),
        base_nodes=[
            NodeSpec(node_id="solver", kind=NodeKind.agent, role="Solver",
                     model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
        ],
        base_edges=[],
        subgraphs=[
            SubgraphSpec(
                subgraph_id="sg_review",
                purpose="Review",
                review_policy=ReviewPolicy.verify_then_correct,
                nodes=[
                    NodeSpec(node_id="reviewer", kind=NodeKind.evaluator, role="Reviewer",
                             model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
                    NodeSpec(node_id="reviser", kind=NodeKind.agent, role="Reviser",
                             model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
                ],
                edges=[
                    EdgeSpec(edge_id="e_solver_reviewer", src="solver", dst="reviewer"),
                    EdgeSpec(edge_id="e_reviewer_reviser", src="reviewer", dst="reviser"),
                ],
                entry_nodes=["reviewer"],
                exit_nodes=["reviser"],
                default_enabled=False,
            ),
        ],
        gates=[
            GateSpec(
                gate_id="review_gate",
                trigger=TriggerExpr(any_of=[
                    AtomicCondition(field="node.node_id", op=ConditionOp.eq, value="solver"),
                ]),
                enable_subgraphs=["sg_review"],
                max_activations=1,
            ),
        ],
    )

    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=ReviewerRejectsLLMClient(), allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    report = executor.execute_blueprint(blueprint)

    node_ids_executed = [t.node_id for t in report.traces]
    assert "reviewer" in node_ids_executed
    assert "reviser" in node_ids_executed
    reviser_traces = [t for t in report.traces if t.node_id == "reviser"]
    assert reviser_traces[0].status == "success"
    assert reviser_traces[0].outputs.get("final_answer") == "99"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -k "test_review_policy" -v`
Expected: FAIL (ReviewPolicy import works from Task 1, but executor doesn't implement the policy yet)

- [ ] **Step 4: Implement review policy logic in executor**

In `src/agentcoop/runtime/executor.py`, add a helper method to `BlueprintExecutor`:

```python
def _get_review_policy_for_node(
    self,
    node_id: str,
    blueprint: WorkflowBlueprint,
    active_subgraphs: Set[str],
) -> Optional[ReviewPolicy]:
    """Return the review policy if this node is a reviewer in a verify_then_correct subgraph."""
    for sg in blueprint.subgraphs:
        if sg.subgraph_id not in active_subgraphs:
            continue
        if sg.review_policy != ReviewPolicy.verify_then_correct:
            continue
        if node_id in sg.entry_nodes:
            # This node is the reviewer (entry) of a verify_then_correct subgraph
            return sg.review_policy
    return None

def _should_skip_for_review_policy(
    self,
    node_id: str,
    blueprint: WorkflowBlueprint,
    active_subgraphs: Set[str],
    node_results: Mapping[str, NodeExecutionResult],
) -> bool:
    """Check if this node (reviser) should be skipped because the reviewer accepted."""
    for sg in blueprint.subgraphs:
        if sg.subgraph_id not in active_subgraphs:
            continue
        if sg.review_policy != ReviewPolicy.verify_then_correct:
            continue
        if node_id in sg.exit_nodes and node_id not in sg.entry_nodes:
            # This is the reviser (exit node, not entry node)
            for entry_id in sg.entry_nodes:
                entry_result = node_results.get(entry_id)
                if entry_result and entry_result.outputs.get("verdict") == "accept":
                    return True
    return False
```

Then in the main execution loop (around line 162, after `_should_skip_node`), add:

```python
                if not self._should_skip_node(node, inputs) and self._should_skip_for_review_policy(
                    node_id, blueprint, active_subgraphs, node_results
                ):
                    # Reviewer accepted — skip the reviser and forward reviewer outputs
                    reviewer_outputs = {}
                    for sg in blueprint.subgraphs:
                        if sg.review_policy == ReviewPolicy.verify_then_correct and node_id in sg.exit_nodes:
                            for entry_id in sg.entry_nodes:
                                entry_result = node_results.get(entry_id)
                                if entry_result:
                                    reviewer_outputs = dict(entry_result.outputs)
                                    break
                    result = NodeExecutionResult(
                        outputs=reviewer_outputs,
                        trace={"skipped_by_review_policy": "reviewer_accepted"},
                        confidence=node_results.get(sg.entry_nodes[0], NodeExecutionResult()).confidence,
                    )
                    # (handle same as skip — add to executed, record trace with status="skipped")
```

Integrate this into the existing skip logic block (lines 162-198). The skip handling for review policy should produce a trace with `status="skipped"` and forward the reviewer's outputs.

Add imports at top of executor.py:

```python
from agentcoop.ir.schema import ReviewPolicy
```

- [ ] **Step 5: Also integrate partial output extraction into executor**

In the output validation section (around line 249-257), after recording contract violations, add:

```python
                if output_violations and node.io.output_schema:
                    from agentcoop.runtime.validation import extract_partial_outputs
                    enriched = extract_partial_outputs(result.outputs, node.io.output_schema)
                    if enriched is not result.outputs:
                        result = result.model_copy(update={"outputs": enriched})
                        node_results[node_id] = result
```

Add degradation-aware confidence in `_assemble_inputs` or when reading upstream results: if any input value is a dict with `_degraded: True`, apply the confidence penalty after node execution:

```python
                # After result is computed, apply degradation penalty
                if any(
                    isinstance(v, Mapping) and v.get("_degraded")
                    for v in inputs.values()
                ):
                    degraded_confidence = result.confidence * 0.8
                    result = result.model_copy(update={"confidence": degraded_confidence})
                    node_results[node_id] = result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -k "test_review_policy" -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/agentcoop/runtime/executor.py src/agentcoop/runtime/validation.py tests/test_runtime.py
git commit -m "feat: implement review-as-verification and graceful degradation in executor"
```

---

### Task 4: Implement retry-before-rewrite and attempt history in executor

**Files:**
- Modify: `src/agentcoop/runtime/executor.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing test for attempt history assembly**

Add to `tests/test_runtime.py`:

```python
def test_assemble_inputs_includes_prior_attempts() -> None:
    """Nodes with include_prior_attempts meta should receive prior attempt data."""
    executor = make_stub_executor()
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="attempt-history", title="History", description="Desc"),
        budget=BudgetSpec(max_total_usd=1.0),
        base_nodes=[
            NodeSpec(node_id="coder", kind=NodeKind.agent, role="Coder",
                     model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini")),
            NodeSpec(node_id="rewriter", kind=NodeKind.agent, role="Rewriter",
                     model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                     meta={"include_prior_attempts": True}),
        ],
        base_edges=[EdgeSpec(edge_id="e1", src="coder", dst="rewriter")],
    )

    # Simulate coder having already run
    coder_result = NodeExecutionResult(
        outputs={"completion": "def foo(): pass", "notes": "first try"},
        confidence=0.7,
    )
    node_results = {"coder": coder_result}
    active_edges = blueprint.base_edges
    parent_map = {"coder": set(), "rewriter": {"coder"}}

    inputs = executor._assemble_inputs(blueprint, "rewriter", parent_map, active_edges, node_results)
    assert "prior_attempts" in inputs
    assert len(inputs["prior_attempts"]) >= 1
    assert inputs["prior_attempts"][0]["node_id"] == "coder"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime.py::test_assemble_inputs_includes_prior_attempts -v`
Expected: FAIL (prior_attempts not injected yet)

- [ ] **Step 3: Implement attempt history in _assemble_inputs**

In `_assemble_inputs` method of `BlueprintExecutor` (around line 428), before the return:

```python
        # Inject prior attempt history for nodes that request it
        node_spec = blueprint.node_map().get(node_id)
        if node_spec and node_spec.meta.get("include_prior_attempts"):
            prior_attempts = []
            for prev_id, prev_result in node_results.items():
                if prev_id == node_id:
                    continue
                prior_attempts.append({
                    "node_id": prev_id,
                    "outputs": dict(prev_result.outputs) if prev_result.outputs else {},
                    "confidence": prev_result.confidence,
                    "failure_type": prev_result.failure_type.value if prev_result.failure_type else "none",
                })
            if prior_attempts:
                payload["prior_attempts"] = prior_attempts

        return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime.py::test_assemble_inputs_includes_prior_attempts -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/agentcoop/runtime/executor.py tests/test_runtime.py
git commit -m "feat: add prior attempt history injection for repair escalation nodes"
```

---

### Task 5: Update pattern builders to use new policies

**Files:**
- Modify: `src/agentcoop/workflows/patterns/reason_execute_select.py`
- Modify: `src/agentcoop/workflows/patterns/code_generate_test_repair.py`
- Modify: `src/agentcoop/workflows/patterns/direct_answer.py`

- [ ] **Step 1: Update reason_execute_select review subgraph**

In `src/agentcoop/workflows/patterns/reason_execute_select.py`, add import:

```python
from agentcoop.ir.schema import ReviewPolicy
```

Update the `_reason_execute_review_subgraph` function. Change the reviewer's default prompt to be verification-style:

```python
        system_prompt=cfg.get("reviewer_system_prompt")
        or (
            "You are verifying a proposed answer for an exact-answer task. "
            "You receive the selector's proposed final_answer plus all solver, programmer, and execution evidence. "
            "First, check if the proposed answer is correct by cross-referencing the evidence. "
            "Return JSON with: "
            "output.verdict: 'accept' if the answer looks correct, 'reject' if you found an error; "
            "output.correction: if rejecting, your corrected answer (omit if accepting); "
            "output.reason: brief explanation of your decision; "
            "confidence: 0..1; summary: short string."
        ),
        io=IOContract(output_schema={"type": "object", "required": ["verdict"]}),
```

Update the reviser prompt to expect correction context:

```python
        system_prompt=cfg.get("reviser_system_prompt")
        or (
            "You are the final revision node. The reviewer rejected the proposed answer and provided a correction. "
            "Use the reviewer's correction and reason, plus all available evidence, to emit the final corrected answer. "
            "Return JSON with output.final_answer, output.solution_outline, confidence, and summary."
        ),
```

Add `review_policy=ReviewPolicy.verify_then_correct` to the SubgraphSpec constructor:

```python
    return SubgraphSpec(
        subgraph_id="sg_review",
        purpose="Review disagreements between reasoning and execution paths.",
        review_policy=ReviewPolicy.verify_then_correct,
        nodes=[reviewer, reviser],
        ...
    )
```

- [ ] **Step 2: Update code_generate_test_repair rewriter node**

In `src/agentcoop/workflows/patterns/code_generate_test_repair.py`, add `include_prior_attempts: True` to rewriter meta:

```python
    rewriter = NodeSpec(
        node_id="rewriter",
        ...
        meta={
            "include_prior_attempts": True,
            "output_change_guard": {
                ...
            }
        },
    )
```

- [ ] **Step 3: Update direct_answer review subgraph**

In `src/agentcoop/workflows/patterns/direct_answer.py`, add import and `review_policy` to the SubgraphSpec:

```python
from agentcoop.ir.schema import ReviewPolicy
```

Add `review_policy=ReviewPolicy.verify_then_correct` to the SubgraphSpec constructor in the review subgraph section.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/agentcoop/workflows/patterns/reason_execute_select.py \
        src/agentcoop/workflows/patterns/code_generate_test_repair.py \
        src/agentcoop/workflows/patterns/direct_answer.py
git commit -m "feat: wire review_policy and attempt history into pattern builders"
```

---

### Task 6: Create optimized experiment configs

**Files:**
- Create: `src/agentcoop/conf/experiment/math_v4.yaml`
- Create: `src/agentcoop/conf/experiment/humaneval_v3.yaml`

- [ ] **Step 1: Create math_v4.yaml**

Copy `math_v3.yaml` as the base. Key changes:
- Re-enable review with verify_then_correct (remove the empty prompts, restore threshold to 0.30)
- Update `preferred_answer_nodes` to `["reviser", "selector", "solver"]` (reviser is back but now acts as targeted corrector)
- Update reviewer prompt to verification style
- Keep the improved programmer and selector prompts from v3

```yaml
# In workflow_design section:
  task_profile:
    preferred_review_style: gate
    review_threshold: 0.30

# In preferred_answer_nodes:
  preferred_answer_nodes:
    - reviser
    - selector
    - solver

# In pattern_overrides.reason_execute_select:
      reviewer_system_prompt: |
        You are verifying a proposed answer for a MATH competition problem.
        You receive the selector's proposed final_answer plus solver reasoning and program execution evidence.
        Check if the proposed answer is correct:
        - Cross-reference the answer against the execution result and solver reasoning
        - Verify the answer format matches what the problem asks for (exact form, integer, fraction, etc.)
        - If both solver and program agree with the proposed answer, verdict should be "accept"
        Return JSON with:
        - output.verdict: "accept" or "reject"
        - output.correction: your corrected answer (only if rejecting)
        - output.reason: brief explanation
        - confidence: 0..1
        - summary: short string
      reviser_system_prompt: |
        You are the final MATH revision node.
        The reviewer rejected the proposed answer and provided a specific correction with reasoning.
        Use the reviewer's correction and reason to emit the final corrected answer.
        Preserve exact symbolic form whenever the task asks for it.
        Return JSON with:
        - output.final_answer
        - output.solution_outline
        - confidence: 0..1
        - summary: short string
```

- [ ] **Step 2: Create humaneval_v3.yaml**

Copy `humaneval_v2.yaml` as the base. Key changes:
- Add `include_prior_attempts` note in the rewriter prompt
- Update rewriter prompt to reference `prior_attempts` in inputs

```yaml
# In pattern_overrides.code_generate_test_repair:
      rewriter_system_prompt: |
        You are the HumanEval escalation rewrite node.
        The earlier repair path either made no effective change or still failed tests.
        IMPORTANT: Check inputs.prior_attempts for what was already tried.
        Do NOT repeat the same approach that already failed.
        Use the original task, all failure details (public_failure_summary, public_failing_assertion,
        public_failing_call, public_assertion_operator, public_expected_repr, reviewer notes,
        retest_failure_summary, retest_failing_assertion, retest_failing_call, etc.)
        to produce a fresh implementation that directly addresses the observed failure.
        Re-derive the intended behavior from the failing call instead of lightly editing the previous code.
        Preserve the required function name and signature exactly.
        Return JSON with:
        - output.completion
        - output.notes
        - confidence: 0..1
        - summary: short string
        Do not use markdown fences.
```

- [ ] **Step 3: Verify configs load**

Run:
```bash
python3 -c "
from agentcoop.config import load_hydra_config
cfg = load_hydra_config(overrides=['experiment=math_v4', 'model=openai_gpt4o_mini'])
print(f'math_v4: review_threshold={cfg.workflow_design.task_profile.review_threshold}')
cfg2 = load_hydra_config(overrides=['experiment=humaneval_v3', 'model=openai_gpt4o_mini'])
print(f'humaneval_v3: loaded OK')
"
```
Expected: Both configs load without error

- [ ] **Step 4: Commit**

```bash
git add src/agentcoop/conf/experiment/math_v4.yaml src/agentcoop/conf/experiment/humaneval_v3.yaml
git commit -m "feat: add math_v4 and humaneval_v3 experiment configs with new policies"
```

---

### Task 7: Integration testing and full benchmark run

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass (182 + new tests)

- [ ] **Step 2: Run MATH smoke test (20 tasks)**

Run: `agentcoop-exp math --experiment math_v4 --subset smoke --model openai_gpt4o_mini --base-dir runs-bench-v4`
Expected: Completes without errors, outputs solve_rate

- [ ] **Step 3: Run HumanEval smoke test (20 tasks)**

Run: `agentcoop-exp humaneval --experiment humaneval_v3 --subset smoke --model openai_gpt4o_mini --base-dir runs-bench-v4`
Expected: Completes without errors, outputs pass_at_1

- [ ] **Step 4: Run MATH full test (486 tasks)**

Run: `agentcoop-exp math --experiment math_v4 --subset test --model openai_gpt4o_mini --base-dir runs-bench-v4`
Expected: solve_rate >= 39% (v3 baseline), target 42-45%

- [ ] **Step 5: Run HumanEval full test (131 tasks)**

Run: `agentcoop-exp humaneval --experiment humaneval_v3 --subset test --model openai_gpt4o_mini --base-dir runs-bench-v4`
Expected: pass_at_1 >= 89% (v2 baseline), target 91-93%

- [ ] **Step 6: Commit all changes and push**

```bash
git add -A
git commit -m "chore: finalize framework optimization v4"
git push origin main
```
