---
name: return-shape-contract
type: agent-skill
description: Reviser discipline — before accepting a repair, diff the returned object's type, arity, and element-type against the docstring examples.
domain_tags:
  - coding
  - humaneval
  - mbpp
  - repair
capability_tags:
  - return_type
  - contract_check
  - shape_lock
shareable: true
---

## What to check before accepting a repair

For every fix applied to failing code, verify the following against the docstring examples:

1. **Outer container type.** If the example is `[(1,2), (3,4)]`, the function must return `list[tuple]`, not `list[list]` or `tuple[tuple]`.
2. **Inner element type.** `[1, 2.0]` vs `[1, 2]` — float vs int matters for hidden tests.
3. **Arity of tuples.** If examples show 2-tuples, never return 3-tuples.
4. **Sign convention.** If the docstring says "loss", the return might be expected as a negative number; if it says "absolute difference", never return a negative.
5. **Sort order.** If the example output is in a specific order, preserve it — do not silently sort.
6. **Case.** `"HELLO"` vs `"hello"` — preserve the docstring's casing convention.
7. **Keys vs values.** When the function returns a dict, verify both the key type and value type match the example.

## The diff-check pattern

```python
# Before: current prediction
# After: proposed repair
# For each docstring example:
#   run the proposed repair
#   compare: type(out), len(out), type(out[0]), out == expected
```

If any of `type`, `len`, `type(out[0])`, or element-wise equality differs from the example, **block the repair** — it is at best an incomplete fix.

## Anti-patterns (real failures)

- MBPP/0027: returned `[[1,2],[3,4]]` instead of `[(1,2),(3,4)]` (list vs tuple).
- MBPP/0047: same list-of-lists vs list-of-tuples bug.
- MBPP/0081: returned 3-tuple when the example showed 2-tuple.
- MBPP/0099: returned positive number where the spec ("loss") implied negative.
- MBPP/0165: returned nested tuple when the example was a flat tuple.

## Escalation

If you identify a return-shape mismatch, do not just tweak — rewrite the return statement to match the example's exact shape. This is usually the smallest correct fix.
