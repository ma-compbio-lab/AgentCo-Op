# Progress Log

## Session 1 — 2026-04-22 (framework implementation start)

### Objective
Implement the AgentCo-Op framework skeleton per `architecture.md` + `instructions.md`. No experiment configuration.

### Actions
- Read `architecture.md` (655 lines), `instructions.md` (1193 lines).
- Confirmed no existing `implement.md`; repo has only spec docs.
- Drafted task plan with 14 phases, success criteria, and out-of-scope list.
- Saved key constraints to `findings.md`.

### Files created so far
- `task_plan.md`
- `findings.md`
- `progress.md`

### Next
Phase 1: scaffold the `agentcoop/` package tree.

### Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
| Pydantic warning "schema shadows BaseModel attribute" on EvalContract | 1 | renamed to `answer_schema` |
| YAML parsed `version: 0.1` as float → MetaSkill validation failed | 1 | added `field_validator` to coerce version to str |
| Compiler picked L3 math route for trivial "What is 13*17?" | 1 | added `_DIFFICULTY_TARGET_LEVEL` penalty (0.35 per level above target) |
| DROP blueprint picked retrieval topology | 1 | added inverse hard-constraint: no retrieval node when retrieval_need < 0.3 |
| Profiler classified "What is 13*17" as "general" (no math hint match) | 1 | extended MATH_HINTS regex to match `\d+\s*[-+*/×÷]\s*\d+` and "what is \d+" |
| Code blueprint deadlocked (cycle: programmer ↔ sandbox_test ↔ repair_planner) | 1 | marked repair_planner → programmer as `condition: needs_repair` in meta-skill and added `_back_edges` cycle detector in runtime |
| `last_result` returned repair_planner output instead of formatter | 1 | added `_pick_final_result` that prefers sinks with formatter/integrator/reviewer role |
| Integrator treated "42" and "42.0" as disagreeing | 1 | normalized gsm8k_numeric through float() then drop trailing zeros |

### Session 1 — Completion
All 14 phases complete. 45 unit tests pass. Framework skeleton is ready; experiment configuration deferred as requested.

### Session 2 — Experiment configuration (2026-04-22)

Phases 16 – 24 complete.

- Cloned AFlow, BioDiscoveryAgent, SpatialAgent, human-eval into `external/` and pinned SHAs.
- SpatialBench not publicly accessible; `spatialbench.yaml` carries both tiers with a gated marker.
- Downloaded GSM8K, MATH (via `EleutherAI/hendrycks_math`), MBPP sanitized, HotpotQA distractor, DROP, HumanEval; hashes written to `data/data_hashes.json`.
- MATH level-5 × 4-category subset = 605 (12 shy of AFlow's 617); documented in `data/README.md`.
- Built AFlow-aligned JSONL splits (seed=42, 20/80 val/test, HotpotQA+DROP 1000 cap).
- Implemented 6 dataset loaders + 6 deterministic graders (GSM8K numeric, MATH boxed+sympy, HumanEval/MBPP pytest sandbox, HotpotQA F1, DROP multi-span F1/EM).
- Wrote 11 benchmark configs with `extends: _base` deep-merge: per-dataset overlays, `aflow_aligned.yaml` sweep, `biodiscovery.yaml`, `spatialbench.yaml`, `cross_specialist_pilot.yaml`.
- Wired `agentcoop benchmark` CLI end-to-end: compile → execute → grade → emit `predictions.csv` + `metrics.json`. Auto-dry-run when no API key is set.
- `scripts/build_repo_images.sh` + `scripts/smoke_repo_wrappers.py` ready. Docker daemon offline; builds deferred.
- Manifests + agent-skill YAMLs now carry real pinned SHAs.
- 53 unit tests pass (was 45; added 8 for config loading, grader shape, dry-run runner, external commits).

### Errors this session
| Error | Attempt | Resolution |
|-------|---------|------------|
| HF `hendrycks/competition_math` dataset removed from hub | 1 | Switched to `EleutherAI/hendrycks_math` per-subject configs |
| SpatialBench repo returns 404 on clone | 1 | Accepted as gated; documented S2-small fallback to SpatialAgent public examples |
| MATH level-5 × 4-category count = 605 (expected 617) | 1 | Documented 12-row upstream duplicate drop in `data/README.md` and `findings.md` |
| Docker daemon not running | 0 | Skipped actual image builds; wrote scripts + manifests so they run as soon as daemon is up |
| `scripts/smoke_repo_wrappers.py` couldn't import `agentcoop` | 1 | Added `sys.path.insert(0, REPO)` before imports |
| Typer option `--variant` used `-v` short form; caller tried `--variants` | 1 | Documented correct flag usage |

### Session 1 — Phase 15: Git commit + push
Split the work into 3 logical commits on `clean-dev`:
- `858683f [scaffold]` — pyproject, configs, docker base, .gitignore (9 files, +164).
- `0f7d8ad [feat]` — agentcoop package: IR, compiler, runtime, gates, skills, backends, wrappers (55 files, +4349).
- `94ff027 [test+docs]` — 45 unit tests + implement.md + planning files (18 files, +928).

Pushed to `origin/clean-dev` (ffac7c5..94ff027). Remote: git@github.com:Eurekashen/AgentCo-Op.git.

---

## Session 3 — experiments.md v2 + benchmarks.md + case_study.md (2026-04-22)

Phases 26 – 36 complete. 82 unit tests pass.

### Removals (outdated v1 specialized track)
- Deleted `configs/benchmarks/{biodiscovery,spatialbench,cross_specialist_pilot}.yaml`.
- Deleted `agentcoop/wrappers/{biodiscovery,spatialagent}/` (adapters + Dockerfile + manifest).
- Deleted `agentcoop/skills/agents/{biodiscovery_agent,spatial_agent}.yaml`, `agentcoop/skills/meta/domain_agent_collaboration.md`.
- Deleted `tests/unit/test_wrappers.py`.

### Additions
- `agentcoop/benchmarks/{bio,perturb}.py` — Case Study 1 & 2 helpers.
- `agentcoop/core/{aflow_import,augment_graph,gate_analysis}.py` — Case Study 3 machinery.
- `agentcoop/wrappers/{geneagent,gears,scgpt,scfoundation,geneformer}/` — new case-study wrappers.
- `configs/case_studies/{case1_airway,case2_norman_replogle,case3_aflow_import}.yaml`.
- `configs/gates/{code_runtime,math,bio,perturb}_gates.yaml`.
- `configs/skills/{code_debugging,python_testing,math_skills}.yaml`.
- `scripts/{case1_airway_de.R, case1_synthetic_de.py}`.
- `configs/external_commits.yaml` rewritten for the new repos.

### Changes
- `agentcoop/benchmarks/common.py` — nested `BenchmarkTask` (`input/reference/metadata`).
- `agentcoop/benchmarks/aflow_splits.py` — emits nested JSONL.
- `agentcoop/benchmarks/runner.py` — new `runs/{dataset}/{method}/{timestamp}/` layout with `predictions.jsonl`, `workflow_blueprint.json`, `git_state.txt`, `model_versions.json`, `data_hashes.json`, `traces/`, `sandbox_logs/`, `artifacts/`.
- `agentcoop/core/gates.py` — 28 new trigger branches.
- `agentcoop/cli.py` — new subcommands under `bio`, `aflow`, `perturb`, `data`, plus `evaluate` / `analyze-gates`.
- `configs/benchmarks/_base.yaml` — 9-variant matrix matching experiments.md §3.

### Verification
- Full `agentcoop benchmark --dry-run` pipeline validated end-to-end on GSM8K.
- CS1 pipeline validated offline end-to-end (synthetic DE → enrich → GeneAgent stub).
- CS2 pipeline validated: synthetic dataset → four baselines → evaluator → three ensemble strategies.
- CS3 pipeline validated: AFlow MBPP graph → augment-graph → 6 gates attached, skills + tools applied.
- All 5 wrapper adapter stubs return ok=True on canned requests.

### Errors this session
| Error | Attempt | Resolution |
|-------|---------|------------|
| Original `test_registry_loads_all_skills` expected 12 meta + 7 agent skills | 1 | Updated asserts to 11 + 5 after v1 skill-card removal |
| `test_meta_retrieval_repo` expected `biodiscovery_agent` in top agents | 1 | Rewrote to assert `sandbox_repo_execution` meta-skill is top for repo-heavy profiles |
| GPU-adapter stubs failed to import `agentcoop.benchmarks.perturb` from subprocess | 1 | Made adapters self-contained (no `agentcoop` import); Docker images would likewise lack the package |
| Typer `--tools` option rejected `--tools a b c` syntax | 1 | Documented `--tools a --tools b --tools c` convention |
| AFlow `CustomCodeGenerate` / `SelfConsistency` operators unmapped | 1 | Extended OPERATOR_MAP with 7 additional operators |

Files delivered:
- Package `agentcoop/` with core, skills (12 meta + 7 agent), 5 backends, memory, wrappers (bio + spatial).
- `pyproject.toml`, `README.md`, `configs/{models,budgets,safety}.yaml`, `docker/base-python.Dockerfile`, `.gitignore`.
- `tests/unit/` with 9 test modules covering schema, tracing/cost, memory, backends, skills, profiler, compiler, gates, integrator/reviewer, runtime, wrappers.
- `implement.md` as the single entry point for future sessions.
