---
name: specialist_assembly
type: meta-skill
description: Workflow topology from specialist_assembly pattern
domain_tags:
- specialist_collaboration
- multi_agent_science
capability_tags:
- specialist
- collaboration
- integration
roles:
- role: SpecialistRouter
  description: Pick specialist repos/agents and define the handoff contract between
    them.
  skill_tags:
  - specialist
  - collaboration
  - routing
  kind: router
- role: Integrator
  description: Integrate the specialist outputs into one coherent proposal or artifact
    bundle.
  skill_tags:
  - specialist
  - collaboration
  kind: agent
- role: Reviewer
  description: Judge whether the specialist collaboration is scientifically grounded
    and complete.
  skill_tags:
  - specialist
  - collaboration
  - evaluation
  kind: evaluator
- role: SpecialistRepair
  description: Replace or reconfigure one of the specialists after a grounded review
    failure.
  skill_tags:
  - specialist
  - collaboration
  kind: agent
- role: IntegratorRefined
  description: Integrate the specialist outputs into one coherent proposal or artifact
    bundle.
  skill_tags:
  - specialist
  - collaboration
  kind: agent
- role: ReviewerRefined
  description: Judge whether the specialist collaboration is scientifically grounded
    and complete.
  skill_tags:
  - specialist
  - collaboration
  - evaluation
  kind: evaluator
edges:
- src: specialist_router
  dst: specialist_a
- src: specialist_a
  dst: specialist_b
- src: specialist_b
  dst: integrator
- src: specialist_a
  dst: integrator
- src: integrator
  dst: reviewer
---

Auto-generated meta-skill from the specialist_assembly workflow pattern.
