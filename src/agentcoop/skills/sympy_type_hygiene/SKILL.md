---
name: sympy-type-hygiene
type: agent-skill
description: Programmer discipline — cast numeric literals to SymPy types before mixing, to avoid AttributeError / TypeError on gcd, simplify, is_prime, etc.
domain_tags:
  - math
  - sympy
  - type_safety
capability_tags:
  - sympy_types
  - defensive_coding
  - runtime_stability
shareable: true
---

## The rule

**Every numeric literal that will flow into a SymPy function must be a SymPy `Integer` or `Rational`, not a Python `int`/`float`.**

Mixing raw Python numbers with SymPy expressions triggers real bugs:
- `sp.gcd(6, 10)` — works, but `sp.gcd(*lst)` with `lst: list[int]` may fail on some versions.
- `sp.is_prime(n)` — works on `int`, but `n.is_prime` attribute fails.
- Passing a plain `int` where SymPy expects an `Expr`: `AttributeError: 'int' object has no attribute 'is_commutative'`.

## Defensive coding pattern

```python
from sympy import Integer, Rational, sympify, gcd, simplify, binomial

# Before: crashes on some inputs
result = gcd(a, b, c)

# After: always safe
result = gcd(*(Integer(x) for x in (a, b, c)))

# Fractions
p, q = 152, 225
frac = Rational(p, q)          # never 152/225 (Python division)

# Binomial / factorial
ways = binomial(Integer(n), Integer(k))
```

## When mixing NumPy or Python math

- Do not pass `numpy.int64` to SymPy. Wrap with `int(x)` first, then `Integer(x)`.
- Do not do arithmetic with `math.pi` — use `sympy.pi`.
- Do not do `1/2` as an exponent — use `Rational(1,2)` or `sqrt(...)`.

## Quick conversion helper

```python
from sympy import sympify
def to_sym(x):
    return sympify(x, rational=True)
```

Call `to_sym(x)` on every incoming value before SymPy operations when you're unsure.

## Anti-pattern

```python
# Will raise "'int' object has no attribute 'is_commutative'"
expr = simplify(42)        # wrong — 42 is a Python int here
expr = simplify(Integer(42))  # correct
```
