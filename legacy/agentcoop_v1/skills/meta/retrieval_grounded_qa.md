---
name: retrieval_grounded_qa
kind: meta_skill
version: 0.1
tags: [qa, retrieval, evidence, hotpotqa, multi-hop]
complexity_level: 3
complexity_penalty: 0.35
requires_verification: evidence_overlap
task_signals:
  domains: [qa, open-domain-qa, multi-hop]
  answer_type: [short_answer]
  verification_available: [rubric, exact]
  requires_retrieval: true
topology_template:
  nodes:
    - {id: query_planner, role: planner, backend: llm}
    - {id: retriever, role: retriever, backend: mcp}
    - {id: evidence_selector, role: specialist, backend: llm}
    - {id: answerer, role: solver, backend: llm}
    - {id: evidence_reviewer, role: reviewer, backend: llm}
    - {id: finalizer, role: formatter, backend: llm}
  edges:
    - {source: query_planner, target: retriever}
    - {source: retriever, target: evidence_selector}
    - {source: evidence_selector, target: answerer}
    - {source: answerer, target: evidence_reviewer}
    - {source: evidence_reviewer, target: finalizer}
gates:
  - {name: evidence_missing, trigger: evidence_missing, action: add_retrieval, max_activations: 2}
  - {name: answer_evidence_conflict, trigger: evidence_missing, action: retry_node, max_activations: 1}
  - {name: budget_near_limit, trigger: budget_near_limit, action: terminate_with_uncertainty, max_activations: 1}
memory_policy: isolated_scratch_plus_artifact_blackboard
expected_benefit: "reduces hallucination on evidence-sensitive QA"
evidence_refs:
  - "AFlow uses retrieval + evidence operators for HotpotQA."
---

# Intent
Use a retrieval-grounded workflow for multi-hop or evidence-sensitive QA.

# When to use
- Question requires facts not fully provided in the prompt.
- Multiple entities or bridging questions appear.

# When not to use
- Answer is already in the prompt.
- Simple arithmetic or coding.
