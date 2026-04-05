---
name: repo_transfer_validate
type: meta-skill
description: Workflow topology from repo_transfer_validate pattern
domain_tags:
- scientific_transfer
- repo_transfer
capability_tags:
- repo_search
- artifact_validation
- transfer
roles:
- role: RepoScout
  description: Search for the most suitable external repo or workflow and summarize
    how it should be adapted.
  skill_tags:
  - repo_search
  - artifact_validation
  kind: agent
- role: Validator
  description: Check whether the transferred repo execution produced the required
    artifact bundle and scientific behavior.
  skill_tags:
  - repo_search
  - artifact_validation
  - evaluation
  kind: evaluator
- role: Refiner
  description: Revise repo selection or analysis configuration after validation feedback.
  skill_tags:
  - repo_search
  - artifact_validation
  kind: agent
- role: ValidatorRefined
  description: Check whether the transferred repo execution produced the required
    artifact bundle and scientific behavior.
  skill_tags:
  - repo_search
  - artifact_validation
  - evaluation
  kind: evaluator
edges:
- src: repo_scout
  dst: repo_runner
- src: repo_runner
  dst: validator
---

Auto-generated meta-skill from the repo_transfer_validate workflow pattern.
