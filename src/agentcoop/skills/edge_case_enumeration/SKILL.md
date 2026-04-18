---
name: edge-case-enumeration
type: agent-skill
description: Before returning code, mentally enumerate standard edge cases (empty, single, boundary, unicode, overflow, negative, duplicate).
domain_tags:
  - coding
  - testing
  - program_synthesis
capability_tags:
  - edge_cases
  - robustness
  - pre_submission_check
shareable: true
---

## Enumerate these edge cases for every function

### Inputs

- **Empty:** `[]`, `""`, `{}`, `0`, `None` (if allowed). Does the function still make sense?
- **Single element:** `[x]`, `"a"`. Often triggers off-by-one bugs in loops or windowing.
- **Two elements:** minimum non-trivial size — catches bugs that a single-element case masks.
- **Duplicates:** `[1,1,1]`. Does the function handle ties, repeated keys, or repeated values?
- **All same / all different** — opposite extremes often trigger different code paths.

### Numeric

- **Zero** as input or intermediate value (division by zero, empty accumulator).
- **Negative** numbers when the problem only stated "number" — read the spec carefully.
- **Floats vs ints:** integer division (`//` vs `/` in Python), rounding direction.
- **Large inputs:** will `n*(n+1)/2` overflow? (In Python, no — but in C-style grading it may.)
- **Boundary:** minimum and maximum allowed values in the spec.

### Strings

- **Empty string.**
- **Single character.**
- **Whitespace only** (`"  "`, `"\t\n"`).
- **Unicode:** non-ASCII characters, combining marks, emoji — string length in chars vs bytes.
- **Case sensitivity** — does the problem require case-insensitive comparison?

### Collections

- **Order sensitivity:** does the function rely on insertion order? (Dicts in Python 3.7+ preserve insertion order.)
- **Nested containers** — lists of lists, dicts of dicts.
- **Mutability** — if the function modifies input, is that acceptable?

## When to stop enumerating

For HumanEval/MBPP-style problems, check the edge cases above quickly — if any would break your implementation, fix it before returning. Don't over-engineer for cases the spec explicitly excludes.
