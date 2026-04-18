---
name: ceiling-floor-discipline
type: agent-skill
description: Detect phrases that imply discrete rounding ("part thereof", "each full X", "up to N") and apply math.ceil / math.floor instead of bare division.
domain_tags:
  - math
  - gsm8k
  - drop
  - word_problem
  - rounding
capability_tags:
  - ceiling
  - floor
  - discrete_units
  - rounding
shareable: true
---

## Trigger phrases

| Phrase | Rule |
|---|---|
| "per hour or part thereof" | `math.ceil(hours)` |
| "each full X" / "every complete Y" | `math.floor(value / X)` |
| "round up to the nearest N" | `math.ceil(value / N) * N` |
| "how many boxes needed to hold N items" | `math.ceil(N / capacity)` |
| "at most N" / "up to N" with fractional result | `math.floor` if counting items |
| "whole hours", "whole dollars" | `math.floor` |
| "the nearest N" (no up/down) | `round(x / N) * N` — banker's rounding; confirm the convention |

## Code pattern

```python
import math

# "Patty's Plumbing charges per hour or part thereof at $X"
billable_hours = math.ceil(actual_hours)
cost = billable_hours * hourly_rate

# "How many 24-packs are needed for 50 people?"
packs = math.ceil(50 / 24)
```

## Why this matters

Rounding errors are silent: the numeric answer is plausible but off by a small amount. Graders score them as wrong with no partial credit. Seen in real failures:
- Patty's Plumbing: actual 2.25h → should bill 3h at $205; without ceil returned `$151.25`.
- Loyalty-card reward tiers: need `floor(points / tier_size)` to count earned rewards.
- Packaging: `ceil(items / capacity)` to count packages.

## Self-check

After computing, ask: "Is the answer a whole count of discrete units?" If yes, and your computation involved division, you almost certainly need `math.ceil` or `math.floor`.
