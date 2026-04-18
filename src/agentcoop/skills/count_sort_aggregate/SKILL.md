---
name: count-sort-aggregate
type: agent-skill
description: Program-of-Thought idioms for counting / sorting / aggregating over passage-derived entities (DROP-style).
domain_tags:
  - numerical
  - reading_comprehension
  - drop
  - aggregation
capability_tags:
  - enumeration
  - filtering
  - aggregation
  - comparison
shareable: true
---

## Extraction step

1. **Read the passage once** and list every entity that matches the question's target type (player, event, year, amount, etc.) along with its numeric/temporal attribute.
2. **Materialize as a Python list of tuples or dicts.** Example:
   ```python
   touchdowns = [
       ("Brady", 12),
       ("Manning", 9),
       ("Rodgers", 15),
   ]
   ```
3. Keep variable names descriptive so the code reads like the passage.

## Operation idioms

### Count with a condition

```python
count = sum(1 for _, yards in throws if yards > 20)
```

### Sum / min / max

```python
total = sum(y for _, y in records)
max_val = max(y for _, y in records)
min_player = min(records, key=lambda r: r[1])[0]
```

### Sort and pick by rank

```python
records_sorted = sorted(records, key=lambda r: r[1], reverse=True)
top_name = records_sorted[0][0]
second_name = records_sorted[1][0]
```

### Difference between two values

```python
diff = abs(a - b)            # when magnitude is asked
signed = a - b               # when "how much more than b was a" is asked
```

### Percentage

```python
pct = 100 * part / whole     # round if the grader expects an integer percentage
```

## Output discipline

- Assign the scalar answer to `FINAL_ANSWER` and `print(FINAL_ANSWER)`.
- Do not wrap numbers as strings, do not include units, do not print a sentence.
- For span answers (e.g., "which player scored most"), return just the player name as a string — no surrounding explanation.

## Anti-patterns

- **Hidden magic numbers from the passage** — prefer naming them in a dict so the computation is auditable.
- **Silent type coercion** — don't mix `int` and `float` unless the question calls for a non-integer answer.
- **Over-generalization** — write the code for the specific passage; don't try to build a generic DROP solver.
