---
name: single_agent_tool_use
kind: meta_skill
version: 0.1
tags: [single-agent, tool-use, calculator, retrieval]
complexity_level: 1
complexity_penalty: 0.1
task_signals:
  difficulty: [simple, moderate]
  answer_type: [short_answer, report]
  verification_available: [exact, rubric, none]
  requires_tools: true
  requires_retrieval: false
topology_template:
  nodes:
    - {id: agent, role: specialist, backend: llm, tool_policy: {mode: "single-agent"}}
    - {id: tool_check, role: reviewer, backend: llm}
    - {id: formatter, role: formatter, backend: llm}
  edges:
    - {source: agent, target: tool_check}
    - {source: tool_check, target: formatter}
gates:
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
  - {name: tool_error, trigger: tool_error, action: retry_node, max_activations: 2}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "handles calc / light retrieval without router overhead"
evidence_refs:
  - "OpenAI practical-agents: maximize single-agent capability before decomposing."
---

# Intent
Single LLM agent holds a small tool set. Add a reviewer to catch schema drift.

# When to use
- Single objective with 1-6 tools (calculator, web fetch, small retriever).
- Tool descriptions do not overlap in confusing ways.

# When not to use
- > 6 tools with overlapping descriptions — prefer a router.
- Task spans multiple domains.
