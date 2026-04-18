---
name: calculus-tactics
type: agent-skill
description: Concrete tactics for calculus — derivatives (chain/product/quotient), integration (u-sub, parts, partial fractions), limits (L'Hopital).
domain_tags:
  - math
  - calculus
  - precalculus
  - analysis
capability_tags:
  - differentiation
  - integration
  - limits
  - series
shareable: true
---

## Differentiation

1. **Identify the outer operation first.** Is it a sum, product, quotient, composition, or power? That picks the rule.
2. **Chain rule for compositions:** `d/dx f(g(x)) = f'(g(x)) * g'(x)`. Track `g(x)` as a named inner function to avoid algebra errors.
3. **Product rule:** `(uv)' = u'v + uv'`. Quotient rule:`(u/v)' = (u'v - uv') / v^2`. If the quotient can be rewritten as a product with a negative power, product rule is less error-prone.
4. **Implicit differentiation:** differentiate both sides with respect to `x`, treat `y` as `y(x)`, apply chain rule, solve for `dy/dx`.
5. **Logarithmic differentiation** for products/quotients of many factors or variable exponents: take `ln` of both sides first.

## Integration

1. **Try the direct antiderivative first** (power rule, `e^x`, `sin/cos`, `1/x` → `ln|x|`).
2. **u-substitution:** pick `u` so that `du` already appears (up to a constant) elsewhere in the integrand.
3. **Integration by parts:** `∫u dv = uv − ∫v du`. LIATE heuristic for choosing `u`: Log, Inverse-trig, Algebraic, Trig, Exponential.
4. **Partial fractions** for rational functions where the denominator factors. Check degree of numerator vs denominator first; do polynomial division if needed.
5. **Trig identities** (double-angle, Pythagorean) can simplify before integration.
6. **Definite integral symmetry:** on a symmetric interval, odd integrands vanish; even integrands double.

## Limits

1. **Plug in first** — if the limit is not indeterminate, you are done.
2. **Indeterminate `0/0` or `∞/∞`:** L'Hopital's rule (differentiate top and bottom). Verify the indeterminate form first; applying L'Hopital to a non-indeterminate form gives wrong answers.
3. **Series expansion** for limits near a point — keep enough terms to cancel the indeterminate part.
4. **Squeeze theorem** when bounded by two functions with the same limit.

## Discipline

- Keep `pi`, `e`, and radicals in symbolic form.
- Sanity-check with a numeric value at one point of the domain.
- When delegating to SymPy, prefer `sympy.diff`, `sympy.integrate`, `sympy.limit`, `sympy.series` and keep the expression symbolic throughout.
