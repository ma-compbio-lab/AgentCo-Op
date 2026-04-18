---
name: answer-form-faithful
type: agent-skill
description: Programmer discipline — preserve the exact form (fraction, radical, integer) demanded by the problem; never collapse to a decimal.
domain_tags:
  - math
  - symbolic_reasoning
  - exact_answer
capability_tags:
  - exact_form
  - sympy
  - fraction_preservation
shareable: true
---

## Detect the required form

Before writing code, scan the problem for form-dictating phrases:

- **"common fraction" / "simplest form" / "in lowest terms"** → answer must be `p/q` with `gcd(p,q)=1`.
- **"simplest radical form" / "exact"** → preserve radicals: `sqrt(2)`, not `1.4142...`.
- **"integer"** → the answer is a whole number; any decimal output is wrong.
- **"in terms of pi"** → keep `pi` symbolic.
- **"nearest integer" / "to the nearest tenth"** → decimal is expected, but only to the stated precision.

## Tactics

1. **Never call `float()` on the final answer** when an exact form is required.
2. Wrap the final value with a simplifier that preserves the required form:
   ```python
   from sympy import nsimplify, Rational, radsimp, sqrtdenest, simplify
   FINAL_ANSWER = nsimplify(result, rational=True)        # for fractions
   FINAL_ANSWER = radsimp(sqrtdenest(simplify(result)))   # for radicals
   ```
3. Print with `str(FINAL_ANSWER)` or `sp.latex(FINAL_ANSWER)` — never `print(float(FINAL_ANSWER))`.
4. If the gold answer uses `\dfrac` or `\frac`, emit a `p/q` string — the grader normalizes fraction macros, but only if the numerator/denominator are recognizable.

## Anti-patterns (seen in real failures)

- `FINAL_ANSWER = 0.69888...` when gold is `\frac{152}{225}`.
- `FINAL_ANSWER = 1.4142` when gold is `\sqrt{2}`.
- `FINAL_ANSWER = Rational(1,3).evalf()` — defeats the point of Rational.
- Returning `12*sqrt(3) - 12` without `simplify` when gold is `12(\sqrt{3}-1)`.

## Self-check before printing

```python
assert not isinstance(FINAL_ANSWER, float), "Exact form required but got float"
```
