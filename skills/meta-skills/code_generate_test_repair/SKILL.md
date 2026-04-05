---
name: code_generate_test_repair
type: meta-skill
description: Workflow topology from code_generate_test_repair pattern
domain_tags:
- coding
- program_synthesis
capability_tags:
- code_generation
- testing
- repair
- execution
roles:
- role: Coder
  description: Write the requested implementation.
  skill_tags:
  - code_generation
  - testing
  kind: agent
- role: Reviewer
  description: Diagnose why the candidate code failed the public tests.
  skill_tags:
  - code_generation
  - testing
  - evaluation
  kind: evaluator
- role: Reviser
  description: Rewrite the implementation using reviewer feedback and test results.
  skill_tags:
  - code_generation
  - testing
  kind: agent
- role: Rewriter
  description: Escalate to a fresh implementation when minimal repair stalls or still
    fails tests.
  skill_tags:
  - code_generation
  - testing
  kind: agent
edges:
- src: coder
  dst: public_test_runner
---

Auto-generated meta-skill from the code_generate_test_repair workflow pattern.
