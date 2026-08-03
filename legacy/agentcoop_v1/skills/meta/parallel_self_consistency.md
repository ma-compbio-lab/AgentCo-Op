---
name: parallel_self_consistency
kind: meta_skill
version: 0.1
tags: [parallel, voting, self-consistency, math]
complexity_level: 4
complexity_penalty: 0.55
task_signals:
  difficulty: [complex, open_ended]
  answer_type: [short_answer]
  verification_available: [exact, deterministic_grader]
topology_template:
  nodes:
    - {id: solver_a, role: solver, backend: llm}
    - {id: solver_b, role: solver, backend: llm}
    - {id: solver_c, role: solver, backend: llm}
    - {id: voter, role: integrator, backend: llm}
  edges:
    - {source: solver_a, target: voter}
    - {source: solver_b, target: voter}
    - {source: solver_c, target: voter}
gates:
  - {name: specialist_disagreement, trigger: specialist_disagreement, action: add_reviewer, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "only pays off on hard tasks with cheap solvers and reliable vote"
---

# Intent
Run multiple independent solvers then vote / verify.

# When to use
- Hard reasoning task where per-sample accuracy is low but an automatic verifier exists.
- Budget allows N independent runs.

# When not to use
- Evidence-sensitive QA (majority voting amplifies hallucinations).
- Tasks with deterministic single-path solvers.
