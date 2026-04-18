---
name: executed-answer-sanity-gate
type: agent-skill
description: Selector discipline — reject the programmer's executed answer when it is type-inconsistent with the question, before applying majority vote.
domain_tags:
  - ensemble
  - selection
  - arbitration
  - validation
capability_tags:
  - answer_type_check
  - sanity_gate
  - executed_answer
shareable: true
---

## Pre-vote sanity checks

Before the normal majority-vote rules, run these gates on `executed_answer`:

1. **Is the executed answer the literal string `INVALID`, `None`, empty, or an error trace?** → exclude it entirely.
2. **Does the question ask for a count, duration, or "how many"?** → the executed answer must be a non-negative integer-like value. Reject negatives and fractional values.
3. **Does the question ask for a probability?** → reject values outside `[0, 1]`.
4. **Does the question ask for a percent?** → reject values outside `[0, 100]`.
5. **Does the question demand an exact form ("common fraction", "simplest radical form", "in terms of pi")?** → reject a bare decimal. If the executed answer is `0.333...`, it must lose to any reasoning path that produced `1/3`.
6. **Does the answer have a wildly different magnitude (>100×) from the solver/challenger?** → treat as suspicious; prefer the reasoning-path majority.

## Decision flow (post-gate)

1. Gate `executed_answer`. If it fails, exclude it from the vote.
2. Apply the standard `ensemble-arbitration` rules on the remaining candidates.
3. Record in `output.arbitration_notes` which gates fired and why the executed answer was excluded.

## Anti-patterns (real cases)

- Selector picked `-44640` (broken inclusion-exclusion) over solver's `540` on a "ways to seat cars" problem.
- Selector picked `0.6988...` over solver's `\frac{152}{225}` when the problem said "common fraction".
- Selector picked `-14.0` (cupcakes) over solver's `8`.

A single type-consistency gate on the executed answer would have prevented all three.

## Output contract

- Never output a value that failed its type gate.
- If ALL candidates fail type gates, emit the reasoning candidate with the highest confidence anyway — do not output `INVALID` as a final answer; the harness expects a best-effort prediction.
