---
name: spec-example-cross-check
type: agent-skill
description: Coder discipline — extract every concrete example from the docstring and verify the chosen formula reproduces each one before writing code.
domain_tags:
  - coding
  - humaneval
  - mbpp
  - spec_reading
capability_tags:
  - example_driven
  - spec_interpretation
  - pre_code_check
shareable: true
---

## The protocol

1. **Scan the docstring (and problem prompt) for concrete examples.** Look for:
   - `>>>` doctests
   - `==>` or `->` mappings
   - `assert f(x) == y` lines
   - "For example, given ... the answer is ..." prose

2. **Tabulate `(input → output)` pairs.** Write them as a list in a comment block:
   ```
   # Examples:
   # f([1,3], [2,4]) == 1    (intersection length)
   # f(1, 3)  == "two"       ???
   ```

3. **Derive the arithmetic relation from the examples.** If the prose says one thing and the examples say another, the examples win — they are ground truth.

4. **Test every chosen formula against every example.** For HumanEval/127 `intersection`: the prose says "intersection length of two closed intervals"; example says `(1,3) ∩ (2,4) → 1`. A `length = end - start + 1` formula would give `3 - 2 + 1 = 2`, not `1`. So the correct formula is `end - start`, not the "closed interval +1" variant.

5. **Only then start writing code.** If you cannot reconcile the examples with a single formula, re-read the prose.

## Anti-patterns (real failures)

- HumanEval/127 `intersection`: model used closed-interval-length formula ignoring the example.
- HumanEval/89 `encrypt`: prose says "shift down by two multiplied to two places" (= 4); model used shift=2 ignoring the example that requires shift=4.
- MBPP/0141 `geometric_sum`: model started series at `1/2^n` instead of `1/2^1`; the visible example would have disproved this.
- HumanEval/130 `tri`: model indexed `i+1` which didn't exist yet; example would have caught it.

## When examples conflict with prose

**Examples win.** Hidden tests are derived from the same examples used in the prompt. The prose description is a guide; the examples are the contract.

## Self-check at the end

Mentally execute your code on every example in the docstring. If any mismatch, fix — don't submit.
