---
name: simple_direct_answer
kind: meta_skill
version: 0.1
tags: [simple, direct, low-complexity]
complexity_level: 0
complexity_penalty: 0.0
task_signals:
  difficulty: [trivial, simple]
  answer_type: [short_answer, ranking]
  verification_available: [exact, rubric, none]
  requires_tools: false
  requires_retrieval: false
  requires_repo: false
topology_template:
  nodes:
    - {id: solver, role: solver, backend: llm}
    - {id: formatter, role: formatter, backend: llm}
  edges:
    - {source: solver, target: formatter}
gates:
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
  - {name: low_confidence, trigger: low_confidence, threshold: 0.3, action: retry_node, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "avoids multi-agent overhead on easy tasks"
evidence_refs:
  - "Anthropic: start from maximum single-agent capability before splitting."
  - "OpenAI practical-agents: multi-agent only when tool/logic complexity justifies."
---

# Intent
Force low-complexity tasks into a single-call direct answer. Protects against over-compilation.

# When to use
- difficulty is trivial or simple.
- tool_need < 0.2 and retrieval_need < 0.2 and repo_execution_need == 0.
- risk_level == low.

# When not to use
- Task needs fresh information retrieval.
- Answer must be backed by evidence or unit tests.
- Task requires repo execution or complex reasoning chains.
