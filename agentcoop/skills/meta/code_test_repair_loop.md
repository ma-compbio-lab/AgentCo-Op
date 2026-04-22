---
name: code_test_repair_loop
kind: meta_skill
version: 0.1
tags: [code, humaneval, mbpp, unit-tests, repair]
complexity_level: 6
complexity_penalty: 0.45
task_signals:
  domains: [code]
  answer_type: [program]
  verification_available: [unit_test, deterministic_grader]
topology_template:
  nodes:
    - {id: parser, role: extractor, backend: llm}
    - {id: programmer, role: programmer, backend: llm}
    - {id: sandbox_test, role: tool, backend: python_sandbox}
    - {id: repair_planner, role: reviewer, backend: llm}
    - {id: formatter, role: formatter, backend: llm}
  edges:
    - {source: parser, target: programmer}
    - {source: programmer, target: sandbox_test}
    - {source: sandbox_test, target: repair_planner, condition: "not ok"}
    - {source: repair_planner, target: programmer, condition: "needs_repair"}
    - {source: sandbox_test, target: formatter, condition: "ok"}
gates:
  - {name: test_failure, trigger: test_failure, action: retry_node, max_activations: 3}
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
  - {name: tool_error, trigger: tool_error, action: replace_backend, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "test-driven repair fits clearly-graded coding tasks"
evidence_refs:
  - "AFlow programmer/test/review/revise operators."
  - "Anthropic evaluator-optimizer pattern."
---

# Intent
Programmer → Sandbox Tests → Repair loop for executable coding tasks.

# When to use
- Deterministic tests or grader exist.
- Task is implement / fix / modify a function.

# When not to use
- Conceptual explanation of code.
- No sandbox permission and high risk side effects.
