---
name: constraint-quantifier-scan
type: agent-skill
description: Solver discipline — before finalizing, re-scan the question for quantifier and constraint words; verify the candidate respects each.
domain_tags:
  - math
  - word_problem
  - gsm8k
  - reading
capability_tags:
  - constraint_check
  - quantifier_parsing
  - re_read
shareable: true
---

## The two-pass protocol

**Pass 1 (drafting):** solve the problem and produce a candidate answer.

**Pass 2 (re-scan):** before emitting the answer, mechanically re-read the question looking for:

### Quantifier words

| Phrase in question | Implication |
|---|---|
| "integer" (unqualified) | may be negative — check both signs |
| "positive integer" / "natural number" | must be `≥ 1` |
| "non-negative" | must be `≥ 0` |
| "distinct" | all values in the answer must be different |
| "at least N" | boundary case is `N`, not `N+1` |
| "more than N" | strictly greater, boundary is `N+1` |
| "at most N" | strictly `≤ N` |
| "between A and B" | check whether endpoints are inclusive |

### English-to-algebra quantifier rewrites

Before computing, rewrite every quantified phrase in strict algebra:

- "A less than B" → `B - A` (NOT `A - B`).
- "A more than B" → `B + A`.
- "twice as many X as Y" → `X = 2 * Y`.
- "P% more than X" → `X * (1 + P/100)`.
- "half as many X as Y" → `X = Y / 2`.
- "half of X" → `X / 2` (not `X * 2`).
- "three times as fast as" → rate relationship, not an additive one.

Write these rewrites down in `output.reasoning_steps` before plugging in numbers.

### Numeric-token audit

After drafting, list every number and qualifier mentioned in the question:
- Mark which ones you used in the computation.
- If any are unused, they are probably constraints, not flavor. Restart.

Common overlooked tokens: tax rates applied to a subset ("only on non-food"), halving of a dimension ("6-inch is half of a foot-long"), second multipliers in a chain ("1/4 as big as … which is half of …").

## Anti-patterns

- "Bakery: 40 less than seven times as many" → solver computed `7*70 - 40 = 450` but emitted `430` because of transcription slip.
- "Subway 6-inch" treated as full-size → missed the halving.
- "Tax on non-food items only" → solver taxed everything.
