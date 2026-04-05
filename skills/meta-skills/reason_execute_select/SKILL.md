---
name: reason_execute_select
type: meta-skill
description: Workflow topology from reason_execute_select pattern
domain_tags:
- math
- symbolic_reasoning
capability_tags:
- reasoning
- tool_execution
- verification
- selection
roles:
- role: Solver
  description: Produce a direct reasoning-based answer.
  skill_tags:
  - reasoning
  - tool_execution
  kind: agent
- role: Programmer
  description: Write a compact Python program or symbolic computation that can verify
    the answer.
  skill_tags:
  - reasoning
  - tool_execution
  kind: agent
- role: Selector
  description: Choose the final answer using reasoning and execution evidence.
  skill_tags:
  - reasoning
  - tool_execution
  - evaluation
  kind: evaluator
- role: Reviewer
  description: Reconcile the direct solver and the executed program path.
  skill_tags:
  - reasoning
  - tool_execution
  - evaluation
  kind: evaluator
- role: Reviser
  description: Emit the final corrected answer after review.
  skill_tags:
  - reasoning
  - tool_execution
  kind: agent
edges:
- src: solver
  dst: selector
- src: programmer
  dst: program_exec
- src: program_exec
  dst: selector
---

Auto-generated meta-skill from the reason_execute_select workflow pattern.
