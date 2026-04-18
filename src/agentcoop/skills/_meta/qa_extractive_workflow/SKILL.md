---
name: qa-extractive-workflow
type: meta-skill
description: Topology guidance for extractive / short-answer QA — single-agent direct-answer pattern with strict SQuAD-style formatting.
domain_tags:
  - qa
  - knowledge
  - extractive
  - reading_comprehension
capability_tags:
  - span_extraction
  - direct_answer
  - short_answer
roles:
  - role: Solver
    kind: agent
    description: Reads the passage and returns the minimal answer span with SQuAD-normalized formatting.
    skill_tags: [span_extraction, short_answer, squad_normalization]
---

## When to use this topology

Pick this for single-hop extractive QA (SQuAD, NaturalQuestions short-answer, TriviaQA) where the answer is a contiguous passage span and the passage is provided in context.

## Why this topology

- **One solver is optimal** — adding a reviewer or selector introduces drift without evidence that it helps short-answer extraction.
- **Formatting is the bottleneck** — F1 is dominated by article stripping, punctuation removal, and avoiding sentence wrapping.
- **No code execution needed** — answer is textual, not numerical.

## Roles and edges

- `solver` (agent) — reads passage, extracts minimal span, applies SQuAD-style normalization before returning.

## When NOT to use

- Multi-hop questions (use `qa-multihop-workflow`).
- Numerical reasoning over passages (use `numerical-rc-workflow`).
- Generative/explanation answers (use a different pattern with a longer answer contract).
