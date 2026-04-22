---
name: orchestrator_workers_research
kind: meta_skill
version: 0.1
tags: [research, orchestrator, open-ended]
complexity_level: 5
complexity_penalty: 0.55
task_signals:
  difficulty: [complex, open_ended]
  answer_type: [report, artifact]
  verification_available: [rubric, none]
  requires_retrieval: true
topology_template:
  nodes:
    - {id: orchestrator, role: router, backend: llm}
    - {id: worker_a, role: specialist, backend: llm}
    - {id: worker_b, role: specialist, backend: llm}
    - {id: integrator, role: integrator, backend: llm}
    - {id: finalizer, role: formatter, backend: llm}
  edges:
    - {source: orchestrator, target: worker_a}
    - {source: orchestrator, target: worker_b}
    - {source: worker_a, target: integrator}
    - {source: worker_b, target: integrator}
    - {source: integrator, target: finalizer}
gates:
  - {name: budget_near_limit, trigger: budget_near_limit, action: terminate_with_uncertainty, max_activations: 1}
  - {name: evidence_missing, trigger: evidence_missing, action: add_retrieval, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "suits tasks whose subtask set is discovered at runtime"
---

# Intent
Orchestrator dynamically decomposes open research tasks into typed worker subtasks.

# When to use
- Large codebases, open research, complex data analysis.
- Subtask set is not known up front.

# When not to use
- Task solvable by a fixed chain.
- Clear single-shot format.
