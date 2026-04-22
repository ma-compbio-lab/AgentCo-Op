---
name: human_review_for_high_risk
kind: meta_skill
version: 0.1
tags: [human-in-the-loop, safety, approval]
complexity_level: 2
complexity_penalty: 0.5
task_signals:
  risk_level: [high]
topology_template:
  nodes:
    - {id: pre_review, role: human_review, backend: human_review}
    - {id: main_agent, role: specialist, backend: llm}
    - {id: post_review, role: human_review, backend: human_review}
  edges:
    - {source: pre_review, target: main_agent}
    - {source: main_agent, target: post_review}
gates:
  - {name: risk_escalation, trigger: risk_escalation, action: escalate_human, max_activations: 2}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "ensures human approval for destructive / external-side-effect actions"
---

# Intent
Gate high-risk tool use (network enable, external upload, file delete, unknown shell) behind human approval.

# When to use
- risk_level == high.
- Tool policy requires approval before and/or after execution.

# When not to use
- Low-risk offline tasks.
- Fully trusted internal pipelines.
