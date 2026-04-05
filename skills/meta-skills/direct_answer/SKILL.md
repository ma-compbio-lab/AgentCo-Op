---
name: direct_answer
type: meta-skill
description: Workflow topology from direct_answer pattern
domain_tags:
- qa
- knowledge
- reasoning
capability_tags:
- direct_answer
- choice
- review
roles:
- role: Solver
  description: Answer the task directly with a concise, structured response.
  skill_tags:
  - direct_answer
  - choice
  kind: agent
- role: Reviewer
  description: Audit the direct answer and decide whether it should be revised.
  skill_tags:
  - direct_answer
  - choice
  - evaluation
  kind: evaluator
- role: Reviser
  description: Emit the final answer after reviewer critique.
  skill_tags:
  - direct_answer
  - choice
  kind: agent
edges: []
---

Auto-generated meta-skill from the direct_answer workflow pattern.
