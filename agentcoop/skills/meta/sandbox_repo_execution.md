---
name: sandbox_repo_execution
kind: meta_skill
version: 0.1
tags: [repo, sandbox, docker, external-agent]
complexity_level: 7
complexity_penalty: 0.7
task_signals:
  domains: [repo, bio, spatial]
  answer_type: [artifact, report]
  verification_available: [rubric, deterministic_grader, none]
  requires_repo: true
topology_template:
  nodes:
    - {id: setup_inspector, role: planner, backend: llm}
    - {id: sandbox_builder, role: tool, backend: python_sandbox}
    - {id: smoke_test, role: tool, backend: python_sandbox}
    - {id: repo_adapter, role: sandbox_agent, backend: sandbox_repo}
    - {id: artifact_reviewer, role: reviewer, backend: llm}
  edges:
    - {source: setup_inspector, target: sandbox_builder}
    - {source: sandbox_builder, target: smoke_test}
    - {source: smoke_test, target: repo_adapter}
    - {source: repo_adapter, target: artifact_reviewer}
gates:
  - {name: tool_error, trigger: tool_error, action: replace_backend, max_activations: 2}
  - {name: risk_escalation, trigger: risk_escalation, action: escalate_human, max_activations: 1}
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "lets existing repos act as typed nodes with safety envelope"
evidence_refs:
  - "ADAS warns about model-generated code risks; uses containerized exec."
  - "OpenAI sandbox-agents docs on controlled environments."
---

# Intent
Wrap a GitHub repo as a sandboxed execution node with a typed adapter.

# When to use
- Task requires an existing specialized agent / library.
- Manifest, commit SHA, and Docker image exist or can be built.

# When not to use
- Task can be solved by an LLM-only pipeline.
- Repo installation is unstable or unaudited.
