---
name: math_specialist_route
kind: meta_skill
version: 0.1
tags: [math, gsm8k, math-benchmark, algebra, number-theory, combinatorics]
complexity_level: 3
complexity_penalty: 0.3
task_signals:
  domains: [math]
  answer_type: [short_answer]
  verification_available: [exact]
topology_template:
  nodes:
    - {id: math_router, role: router, backend: llm}
    - {id: specialist, role: specialist, backend: llm}
    - {id: verifier, role: reviewer, backend: llm}
    - {id: final_extractor, role: formatter, backend: llm}
  edges:
    - {source: math_router, target: specialist}
    - {source: specialist, target: verifier}
    - {source: verifier, target: final_extractor}
gates:
  - {name: verifier_fail, trigger: low_confidence, threshold: 0.5, action: retry_node, max_activations: 1}
  - {name: specialist_disagreement, trigger: specialist_disagreement, action: add_specialist, max_activations: 1}
  - {name: schema_invalid, trigger: schema_invalid, action: add_formatter, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "specialization lifts per-topic accuracy on MATH / GSM8K"
---

# Intent
Route math questions by topic (algebra / number theory / combinatorics / ...) then verify.

# When to use
- Task is math with a clearly extractable short answer.

# When not to use
- Simple arithmetic that a single agent handles fine.
- Open-ended proofs without a short-answer format.
