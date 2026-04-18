---
name: math-scensemble-workflow
type: meta-skill
description: Topology guidance for math reasoning (MATH, GSM8K) — ScEnsemble with solver + challenger + programmer + selector, no review loop.
domain_tags:
  - math
  - symbolic_reasoning
  - arithmetic
  - word_problem
capability_tags:
  - self_consistency
  - program_of_thought
  - ensemble_arbitration
  - exact_answer
roles:
  - role: Solver
    kind: agent
    description: Chain-of-thought reasoning path A — derive the answer symbolically with step-by-step work.
    skill_tags: [chain_of_thought, exact_answer, symbolic_reasoning]
  - role: ChallengerSolver
    kind: agent
    description: Independent reasoning path B — approach the problem from a different angle to produce diverse evidence.
    skill_tags: [chain_of_thought, alternative_approach, diversity]
  - role: Programmer
    kind: agent
    description: Program-of-Thought reasoning path C — write Python/SymPy code that computes the answer exactly.
    skill_tags: [program_of_thought, code_generation, sympy]
  - role: ProgramExecutor
    kind: tool
    description: Executes the programmer's code in a sandbox and returns the observed answer.
    skill_tags: [execution, verification, sandbox]
  - role: Selector
    kind: agent
    description: Arbitrates among solver/challenger/executed answers via majority vote with code-exec tie-break.
    skill_tags: [ensemble_arbitration, majority_vote, selection]
edges:
  - { src: solver, dst: selector }
  - { src: solver_challenger, dst: selector }
  - { src: programmer, dst: program_exec }
  - { src: program_exec, dst: selector }
---

## When to use this topology

Pick this for competition-style math (MATH Level 5), grade-school word problems (GSM8K), or any exact-answer math benchmark where:
- The answer is a number, fraction, or closed-form expression.
- Code execution is a reliable oracle (SymPy / NumPy can check the answer).
- The model is weaker than GPT-4 class (self-consistency gives a large boost).

## Why this topology

- **ScEnsemble is the single highest-leverage technique** — 3 diverse paths + LLM voter measurably beat any single path (+11.7pp on MATH with gpt-4o-mini).
- **Challenger solver diversifies reasoning** — running two CoT solvers with different prompts catches errors that repeat within one chain.
- **Programmer verifies symbolically** — SymPy/NumPy provides a capability the LLM lacks for exact arithmetic and algebra.
- **Selector is LLM-based, not majority-vote-only** — when paths disagree, the selector can prefer executed code over garbled reasoning.
- **No review loop** — with gpt-4o-mini-class models, a re-solver reviewer scores 5–10% and reliably reverts correct answers. Keep the review subgraph disabled.

## Roles and edges

- `solver`, `solver_challenger`, `programmer`, `program_exec`, `selector` run as the 3-path ensemble.
- Solvers run concurrently; programmer output feeds the executor; selector takes all three candidates.

## When NOT to use

- Single-step arithmetic where even direct_answer suffices.
- Proof generation (LLMs cannot self-verify proofs reliably; this topology assumes exact-answer grading).
- Tasks where code execution is unavailable or unsafe.
