---
name: combinatorics-tactics
type: agent-skill
description: Concrete tactics for counting — permutations, combinations, stars-and-bars, inclusion-exclusion, pigeonhole, recurrences.
domain_tags:
  - math
  - combinatorics
  - counting
  - discrete_math
  - probability
capability_tags:
  - permutations
  - combinations
  - stars_and_bars
  - inclusion_exclusion
  - pigeonhole
  - recurrences
shareable: true
---

## Framework: classify before counting

1. **Ordered or unordered?**
   - Ordered ⇒ permutations: `n!/(n-k)!`.
   - Unordered ⇒ combinations: `C(n,k) = n!/(k!(n-k)!)`.

2. **With or without replacement?**
   - With replacement, ordered: `n^k`.
   - With replacement, unordered: stars-and-bars `C(n+k-1, k)`.

3. **Are there constraints?** Constraints almost always imply inclusion-exclusion or complementary counting.

## Tactics by pattern

- **Stars and bars:** number of ways to put `k` identical items into `n` distinct bins is `C(n+k-1, k)`. Use for "non-negative integer solutions to `x1 + x2 + ... + xn = k`".

- **Complementary counting:** count the complement when the direct count is messy. `|A| = |universe| - |not A|`.

- **Inclusion-exclusion:** for overlapping sets, `|A ∪ B ∪ C| = |A|+|B|+|C| - |A∩B|-|A∩C|-|B∩C| + |A∩B∩C|`. Extend alternating signs for more sets.

- **Pigeonhole:** if `n+1` items go into `n` boxes, some box has ≥2. Useful to prove existence, not to count.

- **Bijection:** map the objects you want to count to a set with known size. Often the cleanest path to a closed form.

- **Recurrences:** for counting problems indexed by `n`, try to express `f(n)` in terms of smaller `f(n-1), f(n-2), ...`. Solve the recurrence (characteristic equation) or just iterate.

- **Fix one element:** if symmetry lets you fix the position of one object, divide by the resulting symmetry factor.

## Probability tactics

- `P(event) = favorable / total`. Compute both with the same counting technique (both ordered, or both unordered) to avoid mismatch.
- `P(A and B) = P(A) * P(B|A)`. `P(A or B) = P(A) + P(B) - P(A and B)`.
- **Expected value by linearity:** `E[X+Y] = E[X]+E[Y]` even when `X` and `Y` are dependent. Decompose into indicator variables.

## When to switch to code

If `n` is small (≤ a few dozen) and the combinatorial structure is complex, enumerate with Python `itertools` and count — correctness is higher than hand-derivation.
