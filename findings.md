# Findings

## Source documents
- `architecture.md` (655 lines) — method positioning, 8-module architecture, typed IR sketches, topology ladder L0–L7, 10 meta-skills, memory levels.
- `instructions.md` (1193 lines) — repo layout, dep list (pydantic≥2, typer, networkx, docker, etc.), phase-by-phase code stubs, meta-skill specs, Docker/adapter contracts, CLI design, milestones.
- `experiments.md` — present but out of scope per user instruction.
- `CLAUDE.md` — behavioral rules: simplicity-first, surgical changes, goal-driven, real-world tests (toy data OK for preliminary), evidence-based repair, `implement.md` must be maintained.

## Constraints carried from CLAUDE.md
- Framework phase allows toy data; final validation will need real datasets (handled later).
- Must update `implement.md` alongside code.
- Avoid over-abstraction; every line should trace to architecture or instructions.

## Architecture key decisions
1. **IR-first**: Pydantic models drive the compiler; YAML/Markdown skills are loaded into typed objects.
2. **Simplicity-first compilation**: complexity is a first-class penalty in the compilation objective — not an afterthought.
3. **Gates are local patches**, not global search — bounded by `max_activations`, `max_total_activations`, `max_same_node_repairs`.
4. **Backend federation**: `llm`, `mcp`, `python_sandbox`, `sandbox_repo`, `human_review` share one `execute()` contract.
5. **Memory is scoped**, not chat-history: `RunState`, `NodeScratch` (private), `Blackboard` (typed shared), `ArtifactStore`, `TraceStore`, `SkillMemory`, `RepoRegistry`.

## Dependencies
From `instructions.md` §2.1:
pydantic≥2, typer≥0.12, rich≥13, networkx≥3, jinja2≥3, pyyaml≥6, jsonschema≥4, tenacity≥8, httpx≥0.27, python-dotenv≥1, docker≥7, gitpython≥3, datasets≥2.20, evaluate≥0.4, numpy, pandas, scikit-learn, pytest≥8.

For framework-only phase, we can defer `datasets`, `evaluate`, `docker`, `gitpython` to optional extras to keep the install light.

## Non-obvious points to preserve
- "Save only `rationale_summary`; do not save full hidden reasoning" — profiler and reviewer must not persist chain-of-thought.
- "Reviewer must not override deterministic evaluator" — `reviewer_confidence = min(reviewer_confidence, evaluator_result.confidence)`.
- "Repo execution defaults to `--network none`" — the Docker run command builder must emit that flag.
- "Pin commit SHA" — manifests carry `commit_sha`; wrapper refuses to run without one.
- "Private chain-of-thought is not shared" — Blackboard visibility rules are enforced in code, not left as a convention.

## Topology ladder → trigger mapping (from architecture §4.3)
| Level | Trigger | Template |
|---|---|---|
| L0 | trivial QA, low risk | direct answer |
| L1 | single objective + tools | `SingleAgent + tools` |
| L2 | fixed sequential steps | prompt chain |
| L3 | distinct input types | router → specialist |
| L4 | multi-perspective | parallel specialists / vote |
| L5 | runtime-discovered subtasks | orchestrator-workers |
| L6 | clear grader + iteration | evaluator-optimizer loop |
| L7 | external repos / domain agents | repo federation |

## Meta-skill seed list
`simple_direct_answer`, `single_agent_tool_use`, `retrieval_grounded_qa`, `numeric_reading_comprehension`, `math_specialist_route`, `parallel_self_consistency`, `code_test_repair_loop`, `orchestrator_workers_research`, `evaluator_optimizer`, `sandbox_repo_execution`, `domain_agent_collaboration`, `human_review_for_high_risk`.

## Agent-skill seed list
`math_number_theory.yaml`, `math_algebra.yaml`, `code_programmer.yaml`, `hotpot_retriever.yaml`, `drop_numeric_reasoner.yaml`, `biodiscovery_agent.yaml`, `spatial_agent.yaml`.

## Test strategy for framework phase
- `tests/unit/`: schema round-trip, registry load, compiler output shape for GSM8K/HumanEval/HotpotQA/DROP profiles, gate trigger matrix, runtime DAG walk, memory scoping.
- `tests/integration/`: run a hand-written blueprint with MockLLM through the orchestrator; verify events.jsonl shape.
- Real-data tests deferred to experiment phase.
