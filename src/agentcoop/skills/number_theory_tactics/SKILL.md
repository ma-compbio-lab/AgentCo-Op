---
name: number-theory-tactics
type: agent-skill
description: Concrete tactics for number theory — gcd/lcm, modular arithmetic, primes, Fermat/Euler, Chinese remainder theorem.
domain_tags:
  - math
  - number_theory
  - modular_arithmetic
  - discrete_math
capability_tags:
  - gcd_lcm
  - mod_arithmetic
  - primes
  - fermat_euler
  - crt
shareable: true
---

## Core tactics, in priority order

1. **Reduce everything modulo the relevant modulus early.** Don't compute huge powers — reduce after every multiplication (`(a*b) mod n`).

2. **GCD / LCM via Euclidean algorithm.** `gcd(a,b) = gcd(b, a mod b)` until remainder is 0. `lcm(a,b) = a*b / gcd(a,b)`. For 3+ numbers, fold.

3. **Fermat's little theorem:** if `p` is prime and `gcd(a,p)=1`, then `a^(p-1) ≡ 1 (mod p)`. Use to reduce exponents modulo `p-1`.

4. **Euler's theorem (generalized Fermat):** `a^φ(n) ≡ 1 (mod n)` when `gcd(a,n)=1`. Use to reduce exponents mod `φ(n)`.

5. **Chinese Remainder Theorem:** to solve `x ≡ r1 (mod n1), x ≡ r2 (mod n2)` with coprime `n1,n2`, combine into a unique solution mod `n1*n2`. Generalizes to more moduli.

6. **Modular inverse:** `a^(-1) mod n` exists iff `gcd(a,n)=1`. Compute via extended Euclidean, or as `a^(φ(n)-1) mod n`.

7. **Prime factorization before anything divisibility-related.** Factor the modulus into prime powers; handle each independently, then combine with CRT.

8. **Counting divisors:** if `n = p1^a1 * p2^a2 * ... * pk^ak`, then number of divisors = `(a1+1)(a2+1)...(ak+1)`; sum of divisors = `prod((p^(a+1)-1)/(p-1))`.

9. **Parity and pigeonhole** — before doing heavy computation, check whether simple parity or a counting argument already determines the answer.

10. **Preserve exact integer arithmetic.** Never introduce floating-point. When coding, use Python `int` (arbitrary precision) or SymPy's `Integer`/`Rational`.
