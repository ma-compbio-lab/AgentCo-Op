---
name: entity-type-gate
type: agent-skill
description: QA solver discipline — classify the expected answer type from the question before answering; discard any drafted answer whose type doesn't match.
domain_tags:
  - qa
  - hotpotqa
  - drop
  - reading_comprehension
capability_tags:
  - answer_type_routing
  - type_check
  - question_classification
shareable: true
---

## Classification protocol

Before reading the passages, read the question stem and classify the expected answer type:

| Question starts with / contains | Expected answer type |
|---|---|
| "who", "which person", "which player/coach/director" | **person** (span) |
| "which team", "which team/company/organization" | **organization** (span) |
| "which country/city/state" | **place** (span) |
| "when", "in what year", "what year" | **date** / **year** |
| "how long", "how many years/days" | **duration** (number) |
| "how many" | **count** (number) |
| "how much" | **amount** (number, often with unit) |
| "what percent", "what percentage" | **percent** (number) |
| "what kind of", "what type of" | **category** (short noun phrase) |
| "which of the two" | **disambiguation** (pick one name) |

## Type-mismatch check

After drafting an answer:

1. Classify the type of your drafted answer (person name, year, number, noun phrase).
2. If it doesn't match the expected type from the question stem, **discard and re-answer**.

## Anti-patterns (real failures)

- "When did Attlee serve as PM?" → draft: `"Clement Attlee"` (type: person). Expected: date/year. **Re-answer.**
- "Who kicked the longest field goal?" → draft: `"53"` (type: number). Expected: person. **Re-answer.**
- "How long is the river?" → draft: `"Nile"` (type: place). Expected: duration/length. **Re-answer.**
- "Which airport is closest?" → draft: picked the larger airport by attention bias. Re-check with the "closest" criterion.

## When the question asks for an entity-value pair

For DROP-style questions that *describe* an entity in terms of a value (e.g., "who scored the longest TD?"), the answer is the **entity name**, not the value. If you computed the value correctly, use it to locate the entity in the passage — but emit only the entity.

## Output discipline

- Record the classified answer type in `output.answer_type`.
- Mention the type-check step in `output.reasoning` so its invocation is visible in traces.
