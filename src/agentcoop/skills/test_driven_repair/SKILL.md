---
name: test-driven-repair
type: agent-skill
description: Repair a failing implementation using the failing-assertion signal — minimal targeted diff that preserves all passing behavior.
domain_tags:
  - coding
  - debugging
  - repair
  - program_synthesis
capability_tags:
  - minimal_diff
  - assertion_driven
  - bug_localization
  - preserve_behavior
shareable: true
---

## Repair protocol

1. **Read the failing-assertion detail carefully.** The runner surfaces the failing call, expected value, actual value, operator, and (if applicable) exception type. This is the ground truth of what is wrong.

2. **State the bug in one sentence** before editing. "The function returns X when it should return Y because the loop bound is off by one." If you cannot state the bug, you are not ready to fix it.

3. **Locate the minimal culprit.** Identify the smallest line, expression, or condition that needs to change. Prefer single-token edits (`<` → `<=`, `n` → `n-1`) over rewriting whole blocks.

4. **Preserve all unrelated code paths.** Any branch, helper, or side effect that is not implicated by the failing assertion must remain untouched. Collateral rewrites cause new regressions.

5. **Mentally re-simulate the failing call** after the edit. Walk through with the failing input values and confirm the new code produces the expected output.

6. **Re-check the visible examples** in the problem statement. Your fix must still pass every example that was already passing.

## Anti-patterns

- **Full rewrite on a narrow bug:** don't discard working code.
- **Over-generalization:** don't add handling for hypothetical edge cases that the test suite never exercises.
- **Signature change:** the function name and parameter list are fixed by the spec — never rename or reorder.
- **Debug prints or logging** left in the output.
- **Changing a helper that passing tests already rely on** without verifying the passing tests still pass.

## Output discipline

- Return the complete corrected function, not a diff or a snippet.
- Preserve the exact signature.
- No markdown code fences.
- Briefly note the bug and the fix in `output.notes` (one sentence each).
