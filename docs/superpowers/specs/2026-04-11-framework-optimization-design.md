# Framework Optimization Design Spec

**Date:** 2026-04-11
**Status:** Approved
**Scope:** Generalizable framework improvements to review, repair, and degradation handling

## Background

Benchmark evaluation revealed three systemic framework weaknesses:
1. Review subgraphs are destructive — the reviser re-solves from scratch and produces worse answers (10% vs selector's 41%)
2. Repair loops lack escalation intelligence — the rewriter doesn't know what was already tried, repeating failed approaches
3. Contract violations lose recoverable data — partial outputs are discarded entirely instead of extracting usable fields

These are framework-level issues, not benchmark-specific. The fixes must generalize to any workflow using review subgraphs, repair loops, or LLM nodes.

## Change 1: Review-as-Verification Architecture

### Problem
The reviewer acts as a "re-solver" — it ignores the proposed answer and produces a new one from scratch. The reviser then overwrites the selector's answer unconditionally. This destroys correct answers because the reviewer has less evidence than the selector.

### Design
Add a `review_policy` field to `SubgraphSpec` in `ir/schema.py`:

```python
class ReviewPolicy(str, Enum):
    verify_then_correct = "verify_then_correct"  # New default
    re_solve = "re_solve"                        # Current behavior
```

**`verify_then_correct` behavior:**
- Reviewer receives the proposed answer + all upstream evidence
- Reviewer outputs `{verdict: "accept" | "reject", correction: "...", reason: "..."}`
- If `verdict == "accept"`: reviser is skipped, selector's answer is preserved
- If `verdict == "reject"`: reviser runs with the rejection reason and original answer, making a targeted correction

**`re_solve` behavior:**
- Current behavior, unchanged. Reviewer and reviser run unconditionally.

### Implementation
- `ir/schema.py`: Add `review_policy: ReviewPolicy = ReviewPolicy.verify_then_correct` to `SubgraphSpec`
- `executor.py`: In the execution loop, after reviewer runs with `verify_then_correct` policy:
  - Check reviewer output for `verdict` field
  - If `"accept"`, mark reviser as skipped (add to `executed` set with forwarded outputs)
  - If `"reject"`, run reviser normally with reviewer's correction context
- `reason_execute_select.py`: Update reviewer prompt template to output verdict-based JSON. Update reviser prompt to expect correction context.
- `direct_answer.py`: Same pattern for its review subgraph.
- Default all existing review subgraphs to `verify_then_correct`.

### Backward Compatibility
- Existing configs with no `review_policy` field get `verify_then_correct` (the new default)
- Configs can explicitly set `re_solve` to preserve old behavior

## Change 2: Adaptive Repair with Retry-Before-Rewrite

### Problem
The repair loop escalates directly from reviser failure to a full rewrite. The rewriter produces a "fresh implementation" but often makes the same mistake because it doesn't know what was already tried.

### Design
Add a `retry_policy` field to `SubgraphSpec`:

```python
class RetryPolicy(str, Enum):
    retry_then_escalate = "retry_then_escalate"  # New default
    escalate_immediately = "escalate_immediately"  # Current behavior
```

**`retry_then_escalate` behavior:**
- When the repair subgraph's re-test fails, before activating the escalation subgraph:
  1. Re-invoke the originating node (e.g., `coder`) with failure context appended to its inputs
  2. Re-run the test on the retry output
  3. If retry passes, done. If retry fails, proceed to escalation (rewriter).

**Attempt history tracking:**
- Add `include_prior_attempts: true` meta flag on escalation nodes (rewriter)
- When assembling inputs for a node with this flag, the executor collects all prior outputs from nodes in the repair chain and injects them as `prior_attempts: [{node_id, outputs, failure_info}, ...]`
- This gives the rewriter visibility into what was already tried

### Implementation
- `ir/schema.py`: Add `retry_policy: RetryPolicy = RetryPolicy.retry_then_escalate` to `SubgraphSpec`
- `executor.py`:
  - Track repair chain membership via subgraph metadata
  - On repair subgraph test failure + `retry_then_escalate`: re-queue originating node with augmented inputs (add `retry_context` with failure details)
  - On retry failure: proceed to escalation subgraph normally
  - When assembling inputs for nodes with `include_prior_attempts: true`: collect prior attempt outputs
- `code_generate_test_repair.py`: Wire `retry_policy` on `sg_fix`, set `include_prior_attempts: true` on rewriter node meta
- Max retry count: 1 (to avoid infinite retry loops). Configurable via `max_retries` on the subgraph.

### Backward Compatibility
- Existing configs default to `retry_then_escalate` (new behavior)
- Set `escalate_immediately` to preserve old behavior

## Change 3: Graceful Degradation for Contract Violations

### Problem
When output validation fails, the node's output is stored as-is (often `{"text": "...raw JSON string..."}`). Downstream nodes receive incomplete inputs, leading to cascading failures. The raw text often contains the required fields — they're just not at the expected nesting level.

### Design

**Partial output extraction:**
- New function `extract_partial_outputs(outputs: dict, schema: dict) -> dict` in `runtime/validation.py`
- When output validation fails, this function:
  1. Checks if `outputs` has a `text` field containing a JSON string — if so, parse it and try to extract required fields
  2. Recursively searches nested dicts for fields listed in schema's `required` array
  3. Returns a dict with whatever required fields were found, plus `_degraded: true` marker

**Degradation-aware confidence:**
- When a node receives inputs containing `_degraded: true`, the executor applies a confidence penalty (configurable, default 0.8x multiplier)
- This makes downstream evaluators (selectors, reviewers) naturally less trusting of answers derived from degraded data

### Implementation
- `runtime/validation.py`: Add `extract_partial_outputs()` function
- `executor.py`: After output validation fails and violations are recorded, call `extract_partial_outputs()` to enrich the stored outputs. Apply confidence penalty when inputs contain degraded markers.
- No changes to pattern builders — this is purely executor-level logic.

### Backward Compatibility
- Fully backward compatible. Partial extraction only runs when validation already failed. Degradation marker is additive.

## Files Modified

| File | Changes |
|------|---------|
| `src/agentcoop/ir/schema.py` | Add `ReviewPolicy`, `RetryPolicy` enums; add fields to `SubgraphSpec` |
| `src/agentcoop/runtime/executor.py` | Review policy logic, retry logic, attempt history assembly, degradation confidence penalty |
| `src/agentcoop/runtime/validation.py` | Add `extract_partial_outputs()` |
| `src/agentcoop/workflows/patterns/reason_execute_select.py` | Verifier-style reviewer prompt, review_policy on subgraph |
| `src/agentcoop/workflows/patterns/code_generate_test_repair.py` | retry_policy on sg_fix, include_prior_attempts on rewriter |
| `src/agentcoop/workflows/patterns/direct_answer.py` | review_policy on subgraph if applicable |
| `src/agentcoop/conf/experiment/math_v4.yaml` | New config using verify_then_correct |
| `src/agentcoop/conf/experiment/humaneval_v3.yaml` | New config using retry_then_escalate |
| `tests/` | New tests for review policy, retry policy, partial output extraction |

## What We're NOT Doing

- No benchmark-specific prompt hacks or handler changes
- No model-specific workarounds
- No new node types or execution modes
- No changes to the skill system or synthesis backend

## Testing

1. All 182 existing tests must pass
2. New unit tests:
   - `test_review_policy_verify_then_correct`: verifier accepts → reviser skipped
   - `test_review_policy_verify_reject`: verifier rejects → reviser runs with correction
   - `test_review_policy_re_solve`: backward compat, old behavior preserved
   - `test_retry_policy_retry_then_escalate`: retry succeeds → no escalation
   - `test_retry_policy_retry_fails`: retry fails → escalation runs with attempt history
   - `test_extract_partial_outputs`: recovers fields from nested/stringified JSON
   - `test_degradation_confidence_penalty`: degraded inputs reduce confidence
3. Integration: Re-run MATH (486) and HumanEval (131) full test sets

## Expected Impact

- MATH: +3-6pp from review-as-verification + partial output recovery
- HumanEval: +2-4pp from retry-before-rewrite + attempt history
- All workflows: more robust handling of LLM output failures
