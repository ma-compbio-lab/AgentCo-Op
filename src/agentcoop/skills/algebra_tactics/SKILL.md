---
name: algebra-tactics
type: agent-skill
description: Concrete tactics for algebraic manipulation — factoring, equation solving, substitution, systems of equations.
domain_tags:
  - math
  - algebra
  - prealgebra
  - symbolic_reasoning
capability_tags:
  - factoring
  - equation_solving
  - substitution
  - systems
shareable: true
---

## Algebra tactics to try, in priority order

1. **Recognize the canonical form first.** Is it linear, quadratic, polynomial, rational, or transcendental? The form dictates the technique.

2. **Factor before expanding.** If the expression has common factors, difference of squares, sum/difference of cubes, or a visible quadratic form, factor first — it often reveals the answer directly.

3. **Substitute to reduce.** If a repeated sub-expression appears (e.g., `u = x^2 + 1`), substitute it to turn a messy equation into a tractable one. Solve for `u`, then back-substitute.

4. **For systems of equations:**
   - 2 unknowns: substitution if one variable is isolated; elimination otherwise.
   - ≥3 unknowns: write in matrix form and row-reduce (use SymPy `linsolve` if coding).
   - Look for symmetry (e.g., `x + y` and `xy` as Vieta's) before grinding.

5. **For quadratics:** try factoring; if the discriminant is a non-square integer, use the quadratic formula and keep the radical exact.

6. **Rationalize when a denominator has a radical.** Multiply numerator and denominator by the conjugate.

7. **Check each candidate solution** by substituting back into the original equation. This catches extraneous roots from squaring or multiplying by variable expressions.

8. **Preserve exact form.** Keep fractions, radicals, and `pi` — never collapse to a decimal unless the problem asks for one.
