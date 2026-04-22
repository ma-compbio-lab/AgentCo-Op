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

Files delivered:
- Package `agentcoop/` with core, skills (12 meta + 7 agent), 5 backends, memory, wrappers (bio + spatial).
- `pyproject.toml`, `README.md`, `configs/{models,budgets,safety}.yaml`, `docker/base-python.Dockerfile`, `.gitignore`.
- `tests/unit/` with 9 test modules covering schema, tracing/cost, memory, backends, skills, profiler, compiler, gates, integrator/reviewer, runtime, wrappers.
- `implement.md` as the single entry point for future sessions.
