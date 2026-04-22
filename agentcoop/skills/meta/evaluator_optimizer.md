---
name: evaluator_optimizer
kind: meta_skill
version: 0.1
tags: [iterative, evaluator, optimizer, refinement]
complexity_level: 6
complexity_penalty: 0.5
task_signals:
  difficulty: [moderate, complex]
  answer_type: [program, short_answer, report]
  verification_available: [deterministic_grader, unit_test, rubric]
topology_template:
  nodes:
    - {id: proposer, role: solver, backend: llm}
    - {id: evaluator, role: reviewer, backend: llm}
    - {id: optimizer, role: specialist, backend: llm}
  edges:
    - {source: proposer, target: evaluator}
    - {source: evaluator, target: optimizer}
    - {source: optimizer, target: evaluator}
gates:
  - {name: low_confidence, trigger: low_confidence, threshold: 0.5, action: retry_node, max_activations: 2}
  - {name: budget_near_limit, trigger: budget_near_limit, action: terminate_with_uncertainty, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "converges when a clear evaluator exists; otherwise adds cost"
---

# Intent
Iterate a candidate answer against an evaluator with bounded loop count.

# When to use
- Clear grader / tests / rubric.
- Improvement correlates with more iterations.

# When not to use
- No evaluator → iteration only adds cost.
- Cheap one-shot solvers already high-accuracy.
