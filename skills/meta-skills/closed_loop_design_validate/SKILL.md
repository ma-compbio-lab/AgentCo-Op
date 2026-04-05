---
name: closed_loop_design_validate
type: meta-skill
description: Workflow topology from closed_loop_design_validate pattern
domain_tags:
- panel_design
- design_optimization
capability_tags:
- closed_loop
- optimization
- validation
roles:
- role: Planner
  description: Plan a constrained design-and-validation workflow and choose the initial
    configuration.
  skill_tags:
  - closed_loop
  - optimization
  kind: agent
- role: RepairController
  description: Update the design configuration using validation failures and constraints.
  skill_tags:
  - closed_loop
  - optimization
  kind: agent
edges:
- src: planner
  dst: design_runner
- src: design_runner
  dst: validation_runner
---

Auto-generated meta-skill from the closed_loop_design_validate workflow pattern.
