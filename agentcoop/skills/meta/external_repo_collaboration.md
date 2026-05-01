---
name: external_repo_collaboration
kind: meta_skill
version: 0.1
tags: [external_agents, sandbox, repo, collaboration, case_study_1, generic]
complexity_level: 7
complexity_penalty: 0.6
task_signals:
  domains: [biology, code, data_analysis]
  answer_type: [report, multi_artifact]
  verification_available: [evaluator_gate, deterministic_grader]
  repo_execution_need: [high]
topology_template:
  nodes:
    - {id: repo_profiler, role: extractor, backend: python_sandbox}
    - {id: sandbox_builder, role: tool, backend: python_sandbox}
    - {id: agent_registry, role: extractor, backend: python_sandbox}
    - {id: planner, role: planner, backend: llm}
    - {id: upstream_agent, role: specialist, backend: sandbox_repo}
    - {id: broker, role: tool, backend: python_sandbox}
    - {id: downstream_agent, role: specialist, backend: sandbox_repo}
    - {id: integrator, role: integrator, backend: llm}
    - {id: reporter, role: formatter, backend: llm}
  edges:
    - {source: repo_profiler, target: sandbox_builder}
    - {source: sandbox_builder, target: agent_registry}
    - {source: agent_registry, target: planner}
    - {source: planner, target: upstream_agent}
    - {source: upstream_agent, target: broker}
    - {source: broker, target: downstream_agent}
    - {source: downstream_agent, target: integrator}
    - {source: integrator, target: reporter}
gates:
  - {name: G0_repo_profile, trigger: tool_error, action: retry_node, max_activations: 1, scope: node, node_ids: [repo_profiler]}
  - {name: G1_sandbox_build, trigger: tool_error, action: retry_node, max_activations: 1, scope: node, node_ids: [sandbox_builder]}
  - {name: G2_data_schema, trigger: schema_invalid, action: add_specialist, max_activations: 1, scope: node, node_ids: [upstream_agent]}
  - {name: G3_handoff, trigger: schema_invalid, action: retry_node, max_activations: 1, scope: node, node_ids: [broker]}
  - {name: G4_downstream_quality, trigger: low_confidence, threshold: 0.4, action: retry_node, max_activations: 1, scope: node, node_ids: [downstream_agent]}
  - {name: G5_integration_evidence, trigger: evidence_missing, action: add_reviewer, max_activations: 1, scope: node, node_ids: [integrator]}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: |
  Bind two arbitrary external GitHub repositories as specialised execution
  backends, hand typed artifacts between them through an artifact broker,
  and synthesise the results with an LLM-backed integrator. Generic across
  repo pairs (TissueAgent × GeneAgent is the inaugural case).
evidence_refs:
  - "case_study_1.md (TissueAgent × GeneAgent external collaboration)"
  - "architecture.md §4.3 L7 sandbox_repo_execution"
  - "Anthropic upstream/downstream agent collaboration pattern"
---

# Intent
Compile an upstream/downstream workflow between two external sandboxed
agents whose code lives in independent GitHub repositories. AgentCo-Op
handles the cloning, environment build, sandboxing, typed handoff, and
final synthesis.

# When to use
- The user supplies two (or more) GitHub URLs and a downstream task.
- The repositories expose specialised capabilities that complement each
  other (e.g. a domain-specific data agent + a knowledge-grounded
  interpretation agent).
- Reproducibility, sandbox isolation, and auditable artifact provenance
  matter.

# When not to use
- The task can be solved by a single LLM call (use simple_direct_answer).
- All required code is already in the AgentCo-Op repository (use one of
  the L0–L6 meta-skills).
- The user only wants to call a packaged tool with no environment work
  (use single_agent_tool_use).
