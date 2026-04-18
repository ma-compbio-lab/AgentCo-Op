---
name: squad-answer-extraction
type: agent-skill
description: SQuAD-style minimal-span answer formatting for extractive QA — strip articles, punctuation, units; never wrap in a sentence.
domain_tags:
  - qa
  - extraction
  - formatting
  - reading_comprehension
capability_tags:
  - span_extraction
  - squad_normalization
  - minimal_answer
shareable: true
---

## Output contract (hard rules)

1. **Return the minimal answer span** — typically 1–5 tokens. Never wrap the answer in a full sentence.
2. **Strip leading articles** (`a`, `an`, `the`) unless the article is part of a proper-noun title (e.g., keep "The Beatles").
3. **Strip units** from numeric answers — return `83`, not `83 years`, `42%`, or `$12`.
4. **No wrapping punctuation** — no leading/trailing quotes, periods, or brackets.
5. **Preserve exact casing from the passage** for proper nouns and entity names.
6. **Normalize whitespace** to single spaces; no leading or trailing whitespace.

## Decision flow

1. Read the question. Identify answer type (person, place, date, number, short phrase).
2. Locate supporting evidence in the passage.
3. Extract the shortest contiguous span that answers the question.
4. Apply the formatting rules above.
5. Return the span.

## Common failure modes to avoid

- **Sentence wrapping:** "The answer is X." → just `X`.
- **Redundant article:** "the Empire State Building" → `Empire State Building` (unless the article is part of the official name).
- **Unit contamination:** `23 years old` → `23`.
- **Paraphrasing:** use the passage's exact wording — F1 rewards lexical overlap.
- **Over-specifying:** "John Smith, the CEO" → `John Smith` if the question asked for a name.

## Grader alignment

This skill mirrors SQuAD F1 normalization: lowercase, strip articles (`a/an/the`), strip punctuation, collapse whitespace, and compute multiset token overlap. Outputs that violate the rules above will still be graded but lose F1 points to verbose or off-form predictions.
