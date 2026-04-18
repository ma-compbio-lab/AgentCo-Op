---
name: code-test-repair-workflow
type: meta-skill
description: Topology guidance for code-gen benchmarks (HumanEval, MBPP) — generate → test → review → repair with escalation rewrite.
domain_tags:
  - coding
  - program_synthesis
  - python
capability_tags:
  - code_generation
  - test_driven_repair
  - minimal_diff
  - escalation
roles:
  - role: Coder
    kind: agent
    description: Writes the initial implementation from the task spec, preserving the required function signature.
    skill_tags: [code_generation, signature_preservation, simple_first]
  - role: PublicTestRunner
    kind: tool
    description: Executes the candidate against visible tests in a sandbox and returns pass/fail + failing-assertion detail.
    skill_tags: [test_execution, sandbox, assertion_capture]
  - role: Reviewer
    kind: evaluator
    description: Reads the failing assertion and identifies the smallest real bug without rewriting unrelated code.
    skill_tags: [bug_localization, minimal_diff, review]
  - role: Reviser
    kind: agent
    description: Applies the reviewer's identified fix — minimal, targeted edit that preserves passing behavior.
    skill_tags: [minimal_diff, repair, preserve_behavior]
  - role: Rewriter
    kind: agent
    description: Escalation rewrite when iterative repair fails — re-derives the function from the failing call rather than editing.
    skill_tags: [escalation, rewrite, fresh_approach]
edges:
  - { src: coder, dst: public_test_runner }
  - { src: public_test_runner, dst: reviewer }
  - { src: reviewer, dst: reviser }
  - { src: reviser, dst: rewriter }
---

## When to use this topology

Pick this for code-generation benchmarks with a runnable test oracle (HumanEval, MBPP, APPS, LiveCodeBench) where visible tests drive the repair loop.

## Why this topology

- **Tests are the strongest verifier** — executable assertions beat LLM self-judgment for correctness.
- **Minimal-diff repair beats full rewrite** — the reviser should preserve all passing behavior and only touch the code implicated by the failing assertion.
- **Escalation rewriter is the last resort** — when the same approach keeps failing, switch strategies by re-deriving the function from the failing call rather than editing.
- **Review is safe here (unlike math)** — the reviewer's job is to read a concrete failing assertion, not to re-verify correctness, so it does not regress like the math re-solver.
- **No majority voting** — tests give a ground-truth signal; voting adds noise.

## Roles and edges

- `coder` → `public_test_runner` → (`reviewer` → `reviser` | escalate to `rewriter`).

## When NOT to use

- Tasks without executable tests (reviewer has no failure signal; fall back to direct_answer or self-consistency).
- Long multi-file refactors (this topology assumes single-function tasks).
- Tasks where the visible tests are trivial but hidden tests differ (rewriter cannot see hidden tests; do not game the public set).
