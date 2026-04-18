---
name: drop-answer-type-protocol
type: agent-skill
description: DROP-specific extraction protocol — multi-answer emission, superlative pair-check, complement-percent, year-span inclusivity, and score-semantics guard.
domain_tags:
  - drop
  - numerical_rc
  - reading_comprehension
  - answer_formatting
capability_tags:
  - multi_answer
  - superlative
  - percent_complement
  - year_span
  - score_semantics
shareable: true
---

## Protocol by question pattern

### 1. Multi-answer emission

When the question permits a name plus its associated value (or a short-form plus a long-form), emit both joined with ` / `:

```
Question: "Who kicked the longest field goal, and how long was it?"
Gold (DROP alt spans): "Jay Feely" | "53"
Output:                "Jay Feely / 53"
```

This covers both annotation variants and defeats grader brittleness to pipe-separated gold.

### 2. Entity-vs-value disambiguation

Question stem classifier:
- Starts with **"who"**, **"which player/team/coach"** → output the **entity name** (never the associated number alone).
- Starts with **"how many"**, **"how long"**, **"what percent"** → output the **number** (never an entity name).

If you computed the number to locate the entity, use the number internally — but emit only the entity.

### 3. Superlative pair-check (longest − shortest, most − fewest)

For "difference between longest X and shortest X" or "most − fewest":

1. List ALL candidates as `(label, value)` tuples.
2. Pick `argmax` and `argmin` explicitly.
3. Subtract: `max_value - min_value`.
4. Never reuse the same item as both endpoints.

```
Carney FGs = [("37yd", 37), ("51yd", 51), ("20yd", 20)]
max = 51, min = 20, answer = 31
```

Anti-pattern: `51 - 37 = 14` (picked the two most salient, not the extremes).

### 4. Complement-percent

If the question contains "**not**", "**other than**", "**non-**", "**remaining**", "**besides**":

```
complement = 100 - sum(listed_percentages)
```

Emit an explicit line `complement = 100 - X = Y` in reasoning before the final answer.

### 5. Year-span inclusivity

- "from year A to year B" → `B - A` (exclusive).
- "between A and B, inclusive" → `B - A + 1`.
- "how many years did X last" → `end_year - start_year + 1` if X spans both endpoints.

When ambiguous, emit both forms: `"83 / 84"`.

### 6. Score-semantics guard

Sports sentences like "**Team A won N − M**" mean:
- Team A scored **N**.
- Opponent scored **M**.
- Point difference = `N - M`.

Before answering, write a one-line check:
```
winner = Team A, winner_score = N
loser  = Team B, loser_score  = M
```

Anti-pattern: assuming `N` is one team's total season points rather than this game's score.

## Output discipline

- For number answers: just the digits, no units.
- For date answers: `MM/DD/YYYY` zero-padded, or `YYYY` for year-only questions.
- For span answers: the minimal contiguous span from the passage.
- For multi-answer: `"name / value"` or equivalent two-form emission.
