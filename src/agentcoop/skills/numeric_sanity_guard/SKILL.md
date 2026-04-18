---
name: numeric-sanity-guard
type: agent-skill
description: Programmer discipline — before printing FINAL_ANSWER, assert it is within the physically/combinatorially valid range for the question; emit "INVALID" on violation.
domain_tags:
  - math
  - drop
  - counting
  - probability
  - validation
capability_tags:
  - range_check
  - sanity_assertion
  - invalid_detection
shareable: true
---

## The rule

After computing the answer but before printing it, check that the value is **type-consistent with the question**:

- **"How many ... ?"** → answer must be a non-negative integer.
- **"What is the probability ... ?"** → answer must be in `[0, 1]`.
- **"What percent ... ?"** → answer must be in `[0, 100]`.
- **Times, durations, counts, ages** → must be non-negative.
- **Differences of scores / quantities** asked as absolute → non-negative.

## Pattern

```python
result = compute()

def _valid_count(x): return getattr(x, "is_integer", False) and x >= 0
def _valid_prob(x):  return 0 <= x <= 1
def _valid_pct(x):   return 0 <= x <= 100

if not _valid_count(result):    # replace with the right predicate
    print("INVALID")
else:
    FINAL_ANSWER = result
    print(FINAL_ANSWER)
```

## Why this matters

The downstream selector is instructed to prefer executed answers over reasoning-path answers. But if the executed answer is `-44640` or `-14.0` or `1.7` for a probability, it is *worse* than no answer — it poisons the selection.

Printing the literal string `INVALID` signals the executor harness that the program produced no usable answer, so the selector falls back to solver/challenger candidates.

## Anti-patterns (real failures observed)

- Counting problem that returned `-44640` because inclusion-exclusion was miscoded — selector trusted it.
- Probability problem that returned `1.7` because a conditional was mis-applied — selector trusted it.
- Cupcake division that returned `-14.0` — selector trusted it.

All three would have been caught by a single range assertion.
