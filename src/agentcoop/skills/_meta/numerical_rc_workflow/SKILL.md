---
name: numerical-rc-workflow
type: meta-skill
description: Topology guidance for numerical reading comprehension (DROP) — reason + program-of-thought + selector, with answer-type classification.
domain_tags:
  - reading_comprehension
  - numerical
  - drop
  - discrete_reasoning
capability_tags:
  - program_of_thought
  - answer_type_routing
  - passage_grounding
  - ensemble_arbitration
roles:
  - role: Solver
    kind: agent
    description: Careful passage reader — classifies answer type (number / date / span), extracts relevant values, reasons step by step.
    skill_tags: [passage_grounding, answer_type_routing, span_extraction]
  - role: ChallengerSolver
    kind: agent
    description: Independent reading path — approaches the passage with a different extraction strategy for diversity.
    skill_tags: [passage_grounding, alternative_approach]
  - role: Programmer
    kind: agent
    description: PoT path — extracts numbers/dates/entities to variables and computes the answer with Python.
    skill_tags: [program_of_thought, count_sort_aggregate, date_arithmetic]
  - role: ProgramExecutor
    kind: tool
    description: Runs the programmer's code in a sandbox.
    skill_tags: [execution, sandbox]
  - role: Selector
    kind: agent
    description: Arbitrates candidates with type-aware rules — prefer code for numbers, prefer solver for text spans.
    skill_tags: [ensemble_arbitration, answer_type_routing]
edges:
  - { src: solver, dst: selector }
  - { src: solver_challenger, dst: selector }
  - { src: programmer, dst: program_exec }
  - { src: program_exec, dst: selector }
---

## When to use this topology

Pick this for numerical reading-comprehension benchmarks (DROP, TAT-QA) where the answer requires discrete reasoning — counting, arithmetic, date differences, span selection — over a provided passage.

## Why this topology

- **Answer-type classification is the first critical step** — DROP graders penalize format mismatches (unit suffixes, sentence wrapping, wrong date format).
- **PoT is essential for numeric sub-tasks** — pure LLM chain-of-thought underperforms on multi-step arithmetic and date calculations.
- **But code is bad for span answers** — programs rarely reconstruct exact textual spans; selector must route based on answer type.
- **Selector must know the type** — it prefers `executed_answer` for numbers, `solver_answer` for spans.

## Roles and edges

Same skeleton as `math-scensemble-workflow` but with passage grounding and answer-type routing baked into role prompts.

## When NOT to use

- Pure math without passage context (use `math-scensemble-workflow`).
- Multi-hop QA over entities (use `qa-multihop-workflow`).
- Extractive QA without computation (use `qa-extractive-workflow`).
