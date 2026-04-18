---
name: bridging-inference
type: agent-skill
description: Multi-hop QA tactic — explicitly identify the bridge entity and chain evidence across passages before answering.
domain_tags:
  - qa
  - multi_hop
  - reading_comprehension
  - reasoning
capability_tags:
  - bridge_entity
  - evidence_chaining
  - entity_linking
shareable: true
---

## Multi-hop answering protocol

1. **Decompose the question into sub-questions.** A 2-hop question typically has the form "What is property P of entity X, where X is defined by Y?". Split into:
   - Q1: find the bridge entity X (using Y).
   - Q2: find property P of X.

2. **Name the bridge entity explicitly.** State "the bridge entity is …" before reasoning about the final answer. If the question has two bridges (3-hop), name both.

3. **Quote the supporting sentence(s) for each hop.** For each sub-question, extract the exact passage snippet that answers it. Don't paraphrase — exact phrasing reduces hallucination.

4. **Check the chain is consistent.** The entity that answers Q1 must literally match the entity referenced in Q2 (surface form or unambiguous alias). If it does not, the chain is broken — re-search.

5. **Prefer the passage's exact wording** for the final span (improves F1 against gold).

## Common failure modes

- **Skipping the bridge:** going straight from `Y` to the answer without identifying `X`. This is the #1 cause of multi-hop errors.
- **Entity drift:** answering about a similarly-named entity (e.g., picking "John Smith (actor)" when the question referenced "John Smith (senator)"). Double-check with surrounding context.
- **Paraphrase mismatch:** answering with a synonym that the grader's normalization won't recognize. Use the passage's literal words.
- **Unsupported inference:** if neither passage supplies a required hop, say so rather than guessing.

## Output discipline

Return the minimal answer span only. Keep the chain in `output.reasoning` but do not include it in `output.final_answer`. See `squad-answer-extraction` for formatting rules.
