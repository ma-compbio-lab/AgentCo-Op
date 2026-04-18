---
name: python-sympy-programmer
type: agent-skill
description: SymPy-first Python idioms for the Programmer role — exact arithmetic, symbolic answers, FINAL_ANSWER contract.
domain_tags:
  - math
  - coding
  - sympy
  - program_of_thought
capability_tags:
  - sympy
  - exact_arithmetic
  - code_generation
  - final_answer_contract
shareable: true
---

## Output contract

1. **Write a single Python script**, no functions wrapping the solve logic (unless decomposition genuinely helps).
2. **Assign the answer to `FINAL_ANSWER` and `print(FINAL_ANSWER)`.** The executor harness reads the last line of stdout.
3. **No markdown code fences.** Return raw Python in `output.program`.
4. **Do not import anything that is not installed.** Available: `sympy`, `numpy`, Python stdlib (`math`, `fractions`, `itertools`, `functools`, `collections`, `datetime`, `re`).

## SymPy idioms

- **Exact constants:**
  ```python
  from sympy import Rational, pi, sqrt, E, I, Symbol, symbols
  x = Symbol('x')
  ```
- **Solve equations:**
  ```python
  from sympy import solve, Eq
  solve(Eq(x**2 - 2, 0), x)        # returns [-sqrt(2), sqrt(2)]
  ```
- **Simplify / expand / factor:**
  ```python
  from sympy import simplify, expand, factor, trigsimp
  ```
- **Calculus:**
  ```python
  from sympy import diff, integrate, limit, series
  diff(sin(x), x)                  # cos(x)
  integrate(1/x, x)                # log(x)
  ```
- **Linear algebra:**
  ```python
  from sympy import Matrix
  Matrix([[1,2],[3,4]]).det()
  ```
- **Number theory:**
  ```python
  from sympy import gcd, lcm, isprime, factorint, mod_inverse, totient
  ```

## Discipline

- Keep symbolic — use `Rational(1,3)` not `1/3`, `sqrt(2)` not `math.sqrt(2)`.
- Never output a decimal unless the question asks for one — use `str(expr)` or `latex(expr)` for the final printout.
- For counting/combinatorics problems, prefer `itertools` enumeration when feasible (correctness > cleverness).
- If you must recover from a prior error, re-read the executor's error message and change the approach — do not just re-run the same code.

## Example skeleton

```python
from sympy import Symbol, solve, Eq, simplify

x = Symbol('x')
solutions = solve(Eq(x**3 - 8, 0), x)
FINAL_ANSWER = simplify(solutions[0])
print(FINAL_ANSWER)
```
