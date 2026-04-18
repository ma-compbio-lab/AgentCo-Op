---
name: qa-multihop-workflow
type: meta-skill
description: Topology guidance for multi-hop QA (e.g., HotpotQA) — direct-answer pattern with bridge-entity reasoning and minimal-span formatting.
domain_tags:
  - qa
  - knowledge
  - multi_hop
  - reading_comprehension
capability_tags:
  - bridge_entity
  - evidence_chaining
  - span_extraction
  - direct_answer
roles:
  - role: Solver
    kind: agent
    description: Reads all supporting passages, chains evidence across entities, and returns the minimal answer span.
    skill_tags: [bridge_entity, span_extraction, evidence_chaining]
---

## When to use this topology

Pick this meta-skill when the task is a multi-hop QA benchmark (HotpotQA, 2Wiki, MuSiQue) where the answer is a short span (person, place, date, entity type) and the supporting text is already in the prompt.

## Why this topology

- **Single solver is enough** — retrieval is not required; all passages are provided.
- **Bridge-entity tracking beats free-form CoT** — explicitly identifying the intermediate entity is the highest-leverage step.
- **No review loop** — weak re-solver models (gpt-4o-mini class) regress good spans when asked to re-verify.
- **Formatting discipline matters as much as reasoning** — F1 against gold spans collapses if the answer contains articles, units, or a trailing sentence.

## Roles and edges

- `solver` (agent) — reads passages, performs 2–4 hop chain, returns exact span.

No edges (single-node topology). If you need retrieval, upgrade to a decomposition topology instead.

## When NOT to use

- Single-hop factual QA (overkill; use a direct_answer pattern without bridge-entity prompts).
- Open-ended or numerical RC (use `numerical-rc-workflow` instead).
- Benchmarks where retrieval is required at run time (this skill assumes in-prompt context).
