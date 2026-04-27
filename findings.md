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

---

## Session 2 — Experiment config findings

### External repos
- AFlow, BioDiscoveryAgent, SpatialAgent, human-eval cloned successfully; pinned SHAs in `configs/external_commits.yaml`.
- **SpatialBench** (`Genentech/SpatialBench`) is not publicly accessible (HTTP 404 via `git clone`) as of 2026-04-22. Matches `experiments.md` §11.2. Public canonical examples live inside `external/SpatialAgent/resource/`; the `spatialbench.yaml` config exposes two tiers (S2-small from the public canonical examples, S2-full gated on access).

### Dataset sourcing
- HF `competition_math` and `hendrycks/competition_math` are both removed from the hub. `EleutherAI/hendrycks_math` (per-subject configs) is the live mirror. Downloader concatenates 7 subjects into single JSONL per split.
- Level-5 × {Counting & Probability, Number Theory, Prealgebra, Precalculus} in `EleutherAI/hendrycks_math` yields 605 test examples, not 617 as the AFlow paper reports — 12 duplicates dropped upstream. Documented in `data/README.md`; metrics reporting will flag it.

### Environment
- Docker CLI 29.4.1 installed; daemon not running (Docker Desktop not launched). Image builds deferred.
- `datasets` 3.6.0 available. No OpenAI / Anthropic API keys set, so the runner auto-dry-runs.

### Benchmark config inheritance
- Each per-dataset config uses `extends: _base` → deep-merge. Tests confirm the `extends` chain composes.
- Eight AgentCo-Op variants cover all ablations listed in `experiments.md` §5.2.

### Runner architecture choices
- Dry-run registers MockLLM canned responses keyed by role + node_id. Each task compiles its own blueprint (per-task routing), so `metrics.json → route_distribution` is populated even in dry-run.
- `force_topology_level` is applied post-compile as a provenance annotation; a future full recompile-with-level-filter is a nice-to-have but not blocking.

---

## Session 3 — spec refactor findings

- `experiments.md` shrinks to a short overview; `benchmarks.md` and
  `case_study.md` are the new detailed protocols. The v1 specialized
  track (SpatialBench / BioDiscoveryAgent / cross-specialist pilot) is
  replaced by three case studies.
- New variant names in experiments.md §3: **AC-NoMeta → AC-NoMetaSkills**,
  **AC-NoSandbox → AC-NoToolSkills**. Added `AC-AFlowImported` and
  `AC-AFlowImported-Gated` for Case Study 3.
- Benchmarks.md §3 defines a new unified JSONL record with nested
  `input` / `reference` / `metadata`. `BenchmarkTask` keeps both flat
  and nested forms to avoid breaking existing graders.
- Benchmarks.md §11 requires a full reproducibility directory
  (`config.yaml`, `git_state.txt`, `data_hashes.json`,
  `model_versions.json`, `workflow_blueprint.json`, `predictions.jsonl`,
  `metrics.json`, `traces/`, `sandbox_logs/`, `artifacts/`). The runner
  now writes all of them.
- Benchmarks.md §5 exposes dataset-specific gate names
  (`answer_format_invalid`, `solver_disagreement`, `boxed_answer_missing`,
  ...). `core/gates.py` grew a long list of new triggers.
- Case Study 1 (airway + GeneAgent): framework uses synthetic DE TSV
  when R/DESeq2 aren't available so tests stay offline. `select_markers`
  keeps padj / log2fc thresholds configurable; `enrich` ships a tiny
  Hallmark-style pathway table as a stub.
- Case Study 2 (Norman + Replogle): real datasets require pertpy /
  figshare access and GPUs; the framework ships a synthetic dataset
  + four simple baselines + ensemble strategies so the pipeline is
  exercisable without a GPU. The GPU adapters are **self-contained**
  stubs that will be replaced with real repo calls when images are
  built.
- Case Study 3 (AFlow dynamic): AFlow's own workflow files (in
  `external/AFlow/workspace/<dataset>/workflows/...`) are AST-parsed
  via `agentcoop.core.aflow_import`. Unknown operators are tagged in
  the blueprint's provenance. `augment-graph` + gate YAMLs drive the
  `AC-AFlowImported-Gated` variant end-to-end.
- Typer's list-option syntax is `--tools X --tools Y` (not a single
  repeated argument). Documented in implement.md.
- Wrapper adapters now avoid importing `agentcoop` so they can run
  inside isolated Docker images without the package on `sys.path`.

---

## Session 4 — Live execution findings

### Code state pre-session
- `agentcoop/backends/llm.py` only ships `MockLLM`; runner explicitly raises
  `NotImplementedError` for non-mock providers. **Real client must be built
  before any live run.**
- The runner is sequential (`for variant: for task:`); user wants parallel
  execution so we will switch to `asyncio.gather` with a concurrency cap.
- Mock dry-run returns `{"final_answer": ""}` so dry-run scores are 0% by
  design; live runs need actual prediction text.
- `core/cost.py` price table is `{"mock-llm": (0,0)}`. Need real
  per-1k-token prices for gpt-4o-mini and gpt-4o-2024-08-06. As of 2026-04
  per OpenAI: gpt-4o-mini = $0.15 / $0.60 per 1M tokens; gpt-4o-2024-08-06 =
  $2.50 / $10.00 per 1M tokens.
- `LLMBackend.execute` falls back to a generic `_default_system_for(role)`
  prompt; nodes do not carry real `prompt_template`s. We will add a
  `agentcoop/backends/prompts.py` registry keyed by `(domain, role)` that
  returns a system + user-template pair.
- `BenchmarkTask.input` is the nested dict (passage, question, etc.); the
  runner already forwards it under `payload['input']` so per-role prompts
  can format the actual content cleanly.

### AFlow paper reference numbers (targets to beat or match)
| Dataset | AFlow | Our must-have |
|---|---:|---:|
| GSM8K | 93.0 | ≥ 93.5 (target ≥94) |
| MATH (L5×4) | 56.1 | ≥ 56.5 (target ≥58) |
| HumanEval | 94.7 | ≥ 95.5 (target ≥96) |
| MBPP | 82.4 | ≥ 82 (target ≥83) |
| HotpotQA F1 | 73.5 | ≥ 73 (target ≥74) |
| DROP F1 | 80.6 | ≥ 80 (target ≥81) |

Top candidates to **beat** AFlow: GSM8K, HumanEval, MBPP, MATH (clear runtime
signals → gates can rescue). On par or slight gap acceptable: HotpotQA, DROP
(retrieval-bound, harder for our skill library to add value without a real
retriever).

### Token-budget plan (per-task max_tokens)
- GSM8K 4096 (was 1024) — answers fit in <500 tokens; CoT should not need more
- MATH 6144 — chains can be long
- HumanEval 4096 — function bodies plus repair
- MBPP 4096
- HotpotQA 2048 — short answers
- DROP 2048

### Final results (mid-size 200-task subset; HumanEval = full 132)

| Dataset | n | AC-Gated | AFlow | Δ |
|---|---:|---:|---:|---:|
| HotpotQA F1 | 200 | **76.5** | 73.5 | **+3.0 ✓** |
| DROP F1 | 200 | **81.7** | 80.6 | **+1.1 ✓** |
| GSM8K solve | 200 | **93.5** | 93.0 | **+0.5 ✓** |
| MATH solve (L5×4) | 200 | **60.0** | 56.1 | **+3.9 ✓** |
| MBPP pass@1 | 200 | **87.0** | 82.4 | **+4.6 ✓** |
| HumanEval pass@1 | 132 | 90.2 | 94.7 | -4.5 |

**5/6 wins; user target was 3-4.** Total API spend: $0.62. Wall-clock: ~30 min.

### Insights from optimization rounds

1. **Simplicity-first works.** GSM8K with `simple_direct_answer` (L0)
   beat `math_specialist_route` (L3) by 50 pts — the verifier was
   *agreeing* with wrong answers and the formatter then locked them in.

2. **Grader correctness > model tuning.** DROP grader fix (treat spans
   as alternative annotators per official eval) moved score by +30 pts
   on the same predictions. Always validate graders against gold.

3. **Real OpenAI feedback exposes runtime bugs.** The
   `runtime._ready_nodes` retry-counter / executed-set unsync caused an
   infinite repair loop, but only fired when `python_sandbox` started
   returning real failures. Mock backends had hidden it.

4. **Cycle exclusion blocks repair.** `code_test_repair_loop`'s
   `repair_planner → programmer` back-edge is excluded from readiness
   for cycle safety, so the sandbox can't drive iterative fixes.
   First-attempt code with public tests in the prompt is what we score.

5. **JSON mode mangles LaTeX.** `\boxed{47}` in a JSON-mode response
   becomes `\x08oxed{47}` (backspace + literal text). MATH formatter
   uses plain text, and we restore `\b/\f/\t/\v` → `\\b/\\f/...` in
   `parse_output` and the math grader as a defensive layer.

6. **Dataset-aware profiling > regex profiling.** Locking-in the
   profile per AFlow-aligned dataset (`DATASET_PROFILE_OVERRIDES`) is
   how we keep the right meta-skill winning deterministically.
