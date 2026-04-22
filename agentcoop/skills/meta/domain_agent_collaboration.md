---
name: domain_agent_collaboration
kind: meta_skill
version: 0.1
tags: [federation, multi-repo, spatial, bio, collaboration]
complexity_level: 7
complexity_penalty: 0.9
task_signals:
  domains: [bio, spatial, cross-domain]
  answer_type: [report, artifact]
  verification_available: [rubric, none]
  requires_repo: true
topology_template:
  nodes:
    - {id: high_planner, role: planner, backend: llm}
    - {id: agent_a, role: sandbox_agent, backend: sandbox_repo}
    - {id: agent_b, role: sandbox_agent, backend: sandbox_repo}
    - {id: domain_integrator, role: integrator, backend: llm}
    - {id: reproducibility_reviewer, role: reviewer, backend: llm}
  edges:
    - {source: high_planner, target: agent_a}
    - {source: agent_a, target: agent_b}
    - {source: agent_b, target: domain_integrator}
    - {source: domain_integrator, target: reproducibility_reviewer}
gates:
  - {name: tool_error, trigger: tool_error, action: replace_backend, max_activations: 1}
  - {name: risk_escalation, trigger: risk_escalation, action: escalate_human, max_activations: 1}
  - {name: budget_near_limit, trigger: budget_near_limit, action: terminate_with_uncertainty, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "enables upstream-to-downstream specialization across repos"
---

# Intent
Coordinate two domain-specialized agents (e.g. SpatialAgent → BioDiscoveryAgent) with an integrator and reviewer.

# When to use
- Task genuinely needs both upstream and downstream specialization.
- Each agent has a vetted manifest.

# When not to use
- Task needs only one of the specialized systems.
- Repo dependencies conflict and cannot be isolated.
