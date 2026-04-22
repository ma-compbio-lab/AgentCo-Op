---
name: numeric_reading_comprehension
kind: meta_skill
version: 0.1
tags: [drop, reading-comprehension, arithmetic]
complexity_level: 2
complexity_penalty: 0.2
task_signals:
  domains: [qa, reading-comprehension, drop]
  answer_type: [short_answer]
  verification_available: [exact, rubric]
topology_template:
  nodes:
    - {id: passage_reader, role: extractor, backend: llm}
    - {id: span_extractor, role: extractor, backend: llm}
    - {id: op_classifier, role: specialist, backend: llm}
    - {id: numeric_reasoner, role: solver, backend: llm}
    - {id: answer_formatter, role: formatter, backend: llm}
  edges:
    - {source: passage_reader, target: span_extractor}
    - {source: span_extractor, target: op_classifier}
    - {source: op_classifier, target: numeric_reasoner}
    - {source: numeric_reasoner, target: answer_formatter}
gates:
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
  - {name: arithmetic_mismatch, trigger: low_confidence, threshold: 0.5, action: retry_node, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "reduces DROP errors from numeric-op misclassification"
---

# Intent
DROP-style numeric reading comprehension: extract spans, classify the operation, execute it, format answer.

# When to use
- Task involves passage + numeric question.
- Deterministic numeric answer is expected.

# When not to use
- Generic QA with no numeric operation.
- Pure math olympiad questions.
