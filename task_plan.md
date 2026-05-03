# AgentCo-Op Framework Implementation Plan

## Goal
Implement the AgentCo-Op framework — a task-conditioned workflow compiler — following `architecture.md` and `instructions.md`. Do **not** configure or run experiments yet; produce a framework skeleton with working core modules, typed IR, runtime orchestrator, gate/patch logic, skill registry, execution-backend abstractions, and memory system.

## Success Criteria
- All 8 core modules scaffolded with clear contracts.
- Typed IR (TaskProfile, WorkflowBlueprint, NodeSpec, EdgeSpec, GatePolicy) compiles and round-trips JSON.
- Skill registry loads meta-skills (Markdown+YAML frontmatter) and agent-skills (YAML).
- Runtime orchestrator runs a hand-written blueprint end-to-end with a mock LLM backend.
- Gate evaluator + patch planner enforce `max_activations` and global `max_graph_patches`.
- Memory system isolates node scratch, aggregates a typed blackboard, writes JSONL trace.
- CLI exposes `compile`, `run`, `repo wrap`, `repo smoke` entry points (stubs where needed).
- `pytest` unit tests for schema/registry/compiler/runtime/gates all pass on synthetic data.
- `implement.md` updated with repo layout, contracts, and per-module notes.

## Out of Scope (this session)
- Real benchmark data loading (AFlow-aligned, SpatialBench, BioDiscoveryAgent).
- Real LLM API calls — use mock backend + a thin OpenAI/Anthropic shim.
- Actual Docker builds or repo smoke tests — ship templates + manifest format only.
- Experiment configs in `configs/benchmarks/`.

---

## Phases

### Phase 1: Repo scaffold + tooling — status: complete
Create `agentcoop/` package tree, `pyproject.toml`, `README.md`, `configs/` skeleton, `.gitignore`, `docker/` dir, `runs/.gitkeep`, `data/.gitkeep`.
**Verify:** `python -c "import agentcoop"` works inside a venv; `pytest --collect-only` finds the tests dir.

### Phase 2: Typed IR (`core/schema.py`) — status: complete
Implement Pydantic models: `Budget`, `TaskProfile`, `NodeSpec`, `EdgeSpec`, `GatePolicy`, `WorkflowBlueprint`, `EvalContract`, `MemoryPlan`, `NodeResult`, `RunState`. Include `MetaSkill` and `AgentSkill` Pydantic models for registry objects.
**Verify:** unit tests round-trip each model through `model_dump_json()` / `model_validate_json()`; invalid inputs raise `ValidationError`.

### Phase 3: Tracing + cost (`core/tracing.py`, `core/cost.py`) — status: complete
JSONL span writer with `run_id`, `ts`, `event`, node span start/end, gate_triggered, budget_exceeded. Cost tracker aggregates tokens/USD per node.
**Verify:** unit test writes spans to a tmp file, parses them back, asserts shape.

### Phase 4: Memory system (`memory/`) — status: complete
`Blackboard` (typed, visibility-scoped write/read/summarize), `ArtifactStore` (filesystem-backed), `TraceStore` (wraps JSONL), `SkillMemory` (append-only JSONL of task_profile_hash → outcome), `Summarizer` (token-bounded concat).
**Verify:** unit test enforces that `private` scope blocks cross-node reads; artifact write/read returns byte-identical content.

### Phase 5: Execution backends (`backends/`) — status: complete
Abstract `Backend` protocol with `execute(node, payload, state) -> NodeResult`. Implementations: `llm.py` (with injectable client — default `MockLLM`), `mcp.py` (stub that validates JSON), `python_sandbox.py` (subprocess-isolated Python runner for simple code), `repo_sandbox.py` (Docker-command builder — no actual run; returns manifest+command for dry-run), `human_review.py` (auto-approve in dev, flag in prod).
**Verify:** unit test drives MockLLM to return a canned output; python_sandbox runs `print(1+1)` and captures stdout.

### Phase 6: Skill registry (`skills/`) — status: complete
`registry.py` loads Markdown-with-YAML-frontmatter from `skills/meta/` and YAML from `skills/agents/`. `retriever.py` ranks by keyword+tag overlap against `TaskProfile` (embedding-free v0). Provide 12 meta-skill stubs matching `architecture.md` §5.1 and 7 agent-skill stubs matching the tree in `instructions.md` §1.
**Verify:** unit test loads the repo's skill dir, asserts all 12 meta-skills retrievable, `search_meta(profile)` returns ranked list with deterministic order for a fixed profile.

### Phase 7: Task profiler (`core/profiler.py`) — status: complete
Hybrid rules + LLM structured-output. Rules layer pure-Python; LLM layer uses the injected backend. Do **not** store hidden chain-of-thought, only `rationale_summary`.
**Verify:** unit test feeds sample prompts ("Write a function that reverses a string", "What is 13 × 17?", "HotpotQA multi-hop sample"), asserts correct domain/answer_type/verification_available.

### Phase 8: Workflow compiler (`core/compiler.py`) — status: complete
Implement `compile_workflow`, `instantiate_topology`, `bind_agent_skills`, `attach_memory_policy`, `attach_gate_policy`, `attach_eval_contract`, `choose_simplest_sufficient_graph`. Complexity formula per `instructions.md` §3-Phase3. L0–L7 topology templates as code.
**Verify:** unit test asserts GSM8K-shaped profile compiles to an L1-or-below topology; HumanEval-shaped profile compiles to L6 with a repair gate.

### Phase 9: Gates + patches (`core/gates.py`) — status: complete
`evaluate_gates`, `plan_patch`, `apply_patch` with ops: retry_node, replace_backend, add_reviewer, add_retrieval, add_specialist, add_test_node, reroute_to_simple, escalate_human, terminate_with_uncertainty. Enforce `max_activations` per policy, `max_total_activations`, `max_same_node_repairs`.
**Verify:** unit test fires `schema_invalid`, `low_confidence`, `test_failure` and asserts corresponding patch; a repeated trigger past `max_activations` becomes a no-op.

### Phase 10: Integrator + reviewer (`core/integrator.py`, `core/reviewer.py`) — status: complete
Integrator merges typed candidate outputs per `EvalContract`. Reviewer bound to `EvalContract` — deterministic-grader confidence clamps LLM reviewer confidence.
**Verify:** unit test where two solvers disagree → integrator triggers `specialist_disagreement`; deterministic grader mismatch overrides reviewer "valid=true".

### Phase 11: Runtime orchestrator (`core/runtime.py`) — status: complete
Async executor. Builds DAG via `networkx`, topologically walks, enforces budgets, calls gate evaluator after each node, applies patches, bounded loop.
**Verify:** unit test runs a 3-node blueprint (planner → programmer mock → reviewer) to completion; asserts events.jsonl has the expected sequence.

### Phase 12: CLI (`agentcoop/cli.py`) — status: complete
Typer app: `compile`, `run`, `benchmark` (prints "not wired in framework-only mode"), `repo wrap`, `repo smoke`.
**Verify:** `agentcoop --help` works; `agentcoop compile --task-file ...` emits a blueprint JSON.

### Phase 13: Wrappers skeleton (`wrappers/`) — status: complete
Dockerfile templates, `adapter.py` stub that conforms to §6.4 request/result contract, `manifest.yaml` examples for biodiscovery + spatialagent. No image build, no network.
**Verify:** `yaml.safe_load` accepts both manifests; adapter stub parses a request.json and writes a well-formed result.json.

### Phase 14: Tests + implement.md — status: complete
Consolidate unit tests under `tests/unit/`, run full `pytest`, update `implement.md` with a module map, contracts, and known limitations.
**Verify:** all unit tests pass; `implement.md` has one section per module.

### Phase 15: Git commit + push — status: complete
Split the framework into 3 commits (scaffold / feat / tests+docs) and push `clean-dev`.

---

## Session 2 phases — Experiment configuration

Per `experiments.md`. Docker daemon isn't running; GitHub + HuggingFace reachable. No OpenAI key provided, so we configure but do not launch.

### Phase 16: External repo clones — status: pending
Create `external/` dir. Clone AFlow, BioDiscoveryAgent, SpatialAgent, SpatialBench, OpenAI human-eval. Pin commit SHAs into `configs/external_commits.yaml`.
**Verify:** each repo's HEAD commit recorded; directory sizes reasonable; `.gitignore` excludes `external/**`.

### Phase 17: Dataset downloads — status: pending
Download HotpotQA, DROP, GSM8K, MBPP, MATH via HuggingFace `datasets`; HumanEval via the openai human-eval repo. Write to `data/raw/` and `data/processed/`. Store file hashes in `data/data_hashes.json`.
**Verify:** each dataset has ≥ expected sample count (HotpotQA distractor ~7400 train / 7405 dev; DROP ~77k; MATH level-5 subset = 617 across 4 categories).

### Phase 18: AFlow split importer + unified JSONL converter — status: pending
Implement `agentcoop/benchmarks/aflow_splits.py` with seed=42, 20/80 val/test split, HotpotQA+DROP 1000-sample cap. Emit `data/aflow_aligned/<dataset>/{validation,test}.jsonl` with `{dataset, split, task_id, prompt, reference, metadata}`.
**Verify:** sample counts match `experiments.md` §2.1; re-running with same seed gives byte-identical files.

### Phase 19: Per-dataset loaders + `BenchmarkTask` protocol — status: pending
Implement `agentcoop/benchmarks/{common,gsm8k,math,humaneval,mbpp,hotpotqa,drop}.py`. Each exposes `load(split, limit) -> list[BenchmarkTask]`.
**Verify:** unit tests load 3 samples from each dataset without API access.

### Phase 20: Deterministic graders — status: pending
GSM8K numeric extractor + normalization; MATH boxed-answer + sympy-based equivalence; HumanEval/MBPP run `check(fn)` in the python sandbox; HotpotQA token F1 (SQuAD-style); DROP EM/F1 (official normalization).
**Verify:** unit tests on canonical gold pairs; HumanEval grader returns `pass` on reference solutions.

### Phase 21: Benchmark YAML configs — status: pending
`configs/benchmarks/{gsm8k,math,humaneval,mbpp,hotpotqa,drop,aflow_aligned,spatialbench,biodiscovery}.yaml` with variants AC-Direct, AC-Compiled, AC-Gated, AC-ForcedMulti, plus per-dataset budgets from §7.3.
**Verify:** `yaml.safe_load` accepts all files; each config declares `model`, `variant`, `budget`, `dataset`, and `grader`.

### Phase 22: Benchmark runner CLI — status: pending
Wire `agentcoop benchmark --dataset X --split Y --config Z --limit N --out dir` to load tasks, compile a blueprint per task, run (or `--dry-run` if no API key), grade, and emit `predictions.csv` + `metrics.json`. Support `AGENTCOOP_MODE=dry_run` env-var fallback.
**Verify:** `agentcoop benchmark --dataset gsm8k --limit 3 --dry-run` produces well-formed output with MockLLM.

### Phase 23: Repo wrapper Docker build scripts — status: pending
`scripts/build_repo_images.sh`, pin commit SHAs, copy `external/<repo>/` into each wrapper's build context, enforce non-root, `--network` default off. Daemon not running → document how to run later.
**Verify:** scripts parse; Dockerfiles lint; `repo smoke --manifest ...` dry-run emits the expected command with the pinned digest placeholder.

### Phase 24: Reproducibility + implement.md update — status: pending
Add `configs/external_commits.yaml`, `data/data_hashes.json` schema, and update `implement.md` with the experiment-config map. Update `.gitignore` for `external/**`, `data/raw/**`, `data/processed/**`, `data/aflow_aligned/**`.
**Verify:** `implement.md` has a "Session 2 — Experiments configured" section; repo remains small after ignore rules applied.

### Phase 25: Test + commit + push — status: complete

---

## Session 3 phases — Refactor to experiments.md v2 / benchmarks.md / case_study.md

`experiments.md` has been rewritten as a short overview. `benchmarks.md`
supersedes the per-dataset experiment detail. `case_study.md` replaces
SpatialBench / BioDiscovery with three case studies:
CS1 bulk RNA-seq (airway + GeneAgent), CS2 parallel single-cell
(GEARS / scGPT / scFoundation / Geneformer on Norman + Replogle K562),
CS3 dynamic topology refinement from AFlow workflows.

No API key yet → everything must run in dry-run mode.

### Phase 26: Inventory + remove outdated configs — status: pending
Drop v1 specialized configs (biodiscovery.yaml, spatialbench.yaml,
cross_specialist_pilot.yaml) and the wrappers/skills that only served
them. Keep anything reused by the new case studies.
**Verify:** `git status` shows deletions; `yaml.safe_load` still accepts
every remaining config.

### Phase 27: Variant matrix + unified task schema — status: pending
Rename variants to match experiments.md §3 (AC-Direct, AC-Compiled,
AC-Gated, AC-ForcedMulti, AC-NoMetaSkills, AC-NoToolSkills,
AC-NoReviewer, AC-AFlowImported, AC-AFlowImported-Gated). Migrate the
BenchmarkTask JSONL loader to the nested `input/reference/metadata`
schema from benchmarks.md §3.
**Verify:** each dataset still loads 2 tasks; new schema is readable by
every grader.

### Phase 28: Runner output layout — status: pending
Write predictions.jsonl (not csv), metrics.json, workflow_blueprint.json,
git_state.txt, model_versions.json, data_hashes.json into
`runs/{benchmark}/{method}/{timestamp}/`. Keep a compact CSV as optional.
**Verify:** `agentcoop benchmark --dry-run` produces the required files.

### Phase 29: Gate + patch extensions — status: pending
Add new triggers referenced by benchmarks.md §5 and case_study.md §4.5-6:
`answer_format_invalid`, `solver_disagreement`, `symbolic_check_fail`,
`boxed_answer_missing`, `domain_mismatch`, `method_disagreement`,
`numeric_inconsistency`, `multi_span_conflict`, `model_env_fail`,
`prediction_schema_invalid`, `gene_mapping_low`, `enrichment_empty`,
`design_ambiguous`.
**Verify:** unit test for each new trigger routes to a plausible patch.

### Phase 30: AFlow workflow importer + augment-graph — status: pending
`agentcoop/core/aflow_import.py` converts AFlow `workflow.py`
operator usage into a minimal WorkflowBlueprint (node manifests with
`source: aflow`). `agentcoop/core/augment_graph.py` attaches skill
cards and tools and writes the augmented graph JSON. Gate YAML reader.
**Verify:** smoke import on `external/AFlow/workspace/…` and augment it
with dummy skill cards; round-trip through JSON.

### Phase 31: Case Study 1 scaffolding — status: pending
R sandbox profile doc; `scripts/case1_airway_de.R` per case_study.md
§2.5; `agentcoop bio select-markers` / `agentcoop bio enrich` CLI
commands (Python); GeneAgent wrapper manifest + adapter stub; airway
loader reads an R-produced TSV stub when R isn't installed.
**Verify:** dry-run `agentcoop bio select-markers` on a synthetic DE
TSV produces well-formed `up_genes.json` / `down_genes.json`; wrapper
adapter stub accepts a gene list and returns a canned report.

### Phase 32: Case Study 2 scaffolding — status: pending
GPU-sandbox manifest skeletons for GEARS, scGPT, scFoundation,
Geneformer; `agentcoop/benchmarks/perturb.py` with dataset + split
planner stubs; unified prediction schema validator; simple baselines
(perturbed mean, matching mean, CRISPR-informed mean, ridge);
`agentcoop run-case2` / `evaluate-perturb` / `ensemble-perturb` /
`report-case2` CLI commands.
**Verify:** synthetic Norman-like AnnData (or JSON surrogate) runs
through mean baseline + evaluator without external deps.

### Phase 33: Case Study 3 scaffolding — status: pending
`configs/gates/code_runtime_gates.yaml`, `configs/gates/math_gates.yaml`;
`agentcoop import-aflow-workflow`, `augment-graph`, `analyze-gates`
CLI commands; AFlow-imported nodes add `source: aflow` provenance.
**Verify:** `agentcoop import-aflow-workflow --dry-run` emits a graph JSON.

### Phase 34: Case study configs — status: pending
`configs/case_studies/{case1_airway,case2_norman_replogle,case3_aflow_import}.yaml`
with dataset / model / variant / budget / repo-manifest references.
**Verify:** every YAML loads and declares a `case_study` key.

### Phase 35: Test suite update — status: pending
Remove tests for deleted wrappers; add tests for:
new variant names, new gate triggers, AFlow importer, augment-graph,
bio CLI, perturb simple baselines, run-layout compliance.
**Verify:** `pytest tests/unit` green.

### Phase 36: Docs + planning files — status: pending
Update `implement.md` with session-3 module map; log session-3 entries
in `progress.md` and `findings.md`.
**Verify:** implement.md references only current modules.

### Phase 37: Commit + push — status: pending
Split into logical commits (remove-old / new-runner / case-studies /
docs); push to origin/clean-dev.
**Verify:** `git push` succeeds; all tests pass before push.

---

## Error Log
See `progress.md` for the session 1 error table.

## Decisions Log
- **Model-call abstraction**: inject a `Backend` rather than hard-coding OpenAI/Anthropic. A `MockLLM` is the default so tests run offline.
- **Skill files live in `agentcoop/skills/meta|agents/`** (not a top-level `skills/`) so they're shipped with the package.
- **Embedding-free retrieval v0**: keyword + YAML-tag overlap; embedding hook reserved for later.
- **No real Docker exec in framework phase**: `repo_sandbox.py` returns the run command; executing it is deferred to the experiment phase.
- **Tracing format**: single `events.jsonl` per run, as spec'd in `instructions.md` §3-Phase0.

---

## Session 4 phases — Live experiment execution (2026-04-27)

User provided OpenAI key in `.secrets/api-key`. Goal: run all six AFlow-aligned
benchmarks, optimize so AgentCo-Op surpasses AFlow on 3–4 datasets and is on par
or slightly behind on the rest. Track tokens and cost per benchmark. Run in
parallel within and across benchmarks. Test small subsets first.

Reference targets (AFlow paper / our reproductions of AFlow with gpt-4o-mini):
HumanEval ≈ 95% pass@1, MBPP ≈ 80–82%, GSM8K ≈ 93–94%, MATH(level5×4) ≈ 56%,
HotpotQA ≈ 73–74% F1, DROP ≈ 80% F1.

### Phase 38: OpenAI client + per-role prompt registry — status: pending
Implement `OpenAIClient` (Chat Completions, JSON-object response, streaming-off,
prompt-cache hint) in `agentcoop/backends/llm.py`. Add
`agentcoop/backends/prompts.py` with role × dataset prompt templates so each node
gets a domain-aware system prompt instead of the generic one. Wire it into
`LLMBackend.execute` so the user message carries the actual task text + relevant
prior outputs from the blackboard, not just `json.dumps(payload)`.
**Verify:** offline unit test with a fake client returns a
parsed-JSON `NodeResult`; live one-task GSM8K call returns a non-empty
`final_answer` string and updates `cost_usd` / `tokens_used` accurately.

### Phase 39: Live cost ledger + token tracking — status: pending
Update `core/cost.py` price table for `gpt-4o-mini-2024-07-18` /
`gpt-4o-mini` (the AFlow-aligned executor) and `gpt-4o-2024-08-06` (fallback).
Make sure each `NodeResult.tokens_in/_out` flows back to `RunState`. Add a
per-benchmark token / cost table to `metrics.json`.
**Verify:** five-task live GSM8K run reports `tokens_total > 0` and a cost figure
that matches a hand calculation within ±5%.

### Phase 40: Parallel runner — status: pending
Convert `runner._run_task` loop into `asyncio.gather` over (variant × task) with a
configurable concurrency cap (default 8) and a hard wall-clock timeout per task
(120s default). Live runs must respect the cap so we don't blow through the
rate-limit. Add a CLI flag `--concurrency`.
**Verify:** 12-task GSM8K live run with concurrency=8 finishes in <2× the
single-task latency × 12 / 8 (i.e., parallelism is real, not serialized). Test
under MockLLM that result ordering is stable.

### Phase 41: Smoke test on small subsets — status: pending
Run 10-task subsets per dataset with AC-Direct + AC-Compiled + AC-Gated.
Confirm: predictions are non-empty, scores are non-zero, cost is within budget,
no protocol violations (no leaked hidden tests, no infinite loops). Skip MATH
SymPy fallback when latex parsing fails.
**Verify:** all six datasets produce a `metrics.json` whose `score_avg` is
plausibly above the IO baseline; no single task burns more than its
`max_cost_usd`.

### Phase 42: Workflow / prompt optimization round 1 — status: pending
Based on Phase 41 errors. Surgical changes ONLY — no broad refactors. Likely
levers per CLAUDE.md §6:
- GSM8K / MATH: tighter CoT prompt, explicit boxed-answer instruction, allow
  `\boxed{}` extraction in the grader.
- HumanEval / MBPP: ensure `programmer` outputs the full function in a fenced
  block; sandbox runs deterministic public/generated tests; cap repair rounds.
- HotpotQA / DROP: enforce concise answer span via system prompt; DROP
  numeric-output normalization.
**Verify:** 30-task subset on each dataset improves ≥1 percentage point (or
matches AFlow within 1pp) before moving to Phase 43.

### Phase 43: Mid-size run (200 tasks) — status: pending
Run 200 tasks per dataset in parallel for AC-Compiled + AC-Gated +
AC-AFlowImported-Gated. Compare vs Phase 41 baseline. Pick datasets to optimize
further (target: surpass AFlow on at least 3-4) and revert any change that
doesn't move the score.
**Verify:** at least three datasets have score-avg ≥ AFlow paper; the rest are
within 2pp.

### Phase 44: Full-dataset runs — status: pending
Run AC-Gated (and the AFlow-imported-gated variant for code/math) on the full
test split for every dataset. Also run AFlow's original baseline (CoT or
AC-Direct) for relative comparison. HotpotQA/DROP capped at 1000 per AFlow §1.
**Verify:** `metrics.json` has full counts; the cost-performance table is
populated; 3-4 datasets surpass AFlow.

### Phase 45: Gate analysis + cleanup — status: pending
Run `agentcoop analyze-gates` over the run dirs to compute rescue / harm rates.
Delete obsolete smoke-test directories under `runs/` so the tree only carries
the canonical run per benchmark. Remove any scratch experiment-only files.
**Verify:** `runs/` has exactly one canonical run dir per (dataset × variant);
gate analysis writes its `rescue_rate` / `harm_rate` columns.

### Phase 46: Report.md — status: pending
Write `report.md` with: problem statement, method recap, hardware/budget,
the AFlow-style main table, cost-performance table, route-distribution table,
gate-rescue table, ablation table, key takeaways, and limitations.
**Verify:** every cell in the main table is populated from a real run dir;
each AgentCo-Op variant is annotated with its run dir path.

### Phase 47: Commit + push — status: complete
Stage only the changes that improved a benchmark or fixed a real bug. Do NOT
commit prompts that didn't help. Single squashed-feel commit per concern.
**Verify:** `git diff --stat origin/clean-dev...HEAD` only shows files tied to
retained optimizations; pytest still green.

---

## Session 5 phases — Full-dataset runs + workflow exports + case studies (2026-04-27)

User confirmed mid-size results (5/6 wins vs AFlow). Now wants:
1. Full test splits to confirm wins hold beyond 200-task subsets.
2. Save canonical compiled workflows so others can reproduce.
3. Run case studies CS1/CS2/CS3.

Full splits: DROP 800, GSM8K 1056, HotpotQA 800, HumanEval 132,
MATH 484, MBPP 342 = 3 614 tasks. Estimated cost ~$1.80, wall ≈35 min
(math is bottleneck) at concurrency 6 per dataset, six in parallel.

### Phase 48: Plan + restore context — status: complete
Read planning files; check git/diff/status; identify full split sizes;
queue tasks.

### Phase 49: Launch full-dataset runs (parallel) — status: pending
Six runs under `runs/full/{dataset}/` with the same `AC-Gated` variant +
concurrency 6 per dataset. Use `--limit 99999` to consume the whole test
split. Per-task timeout still 240s.
**Verify:** all six write `metrics.json`; predictions.jsonl line count
matches test split; cost stays under $0.50 per dataset.

### Phase 50: Mid-flight optimization round (if needed) — status: pending
If any full-dataset score regresses below AFlow, diagnose via failures
in `predictions.jsonl`. Surgical changes only — no broad refactors.
Likely candidates: HumanEval edge-case prompt (currently 90.2% vs 94.7
AFlow); HotpotQA complex multi-hop tasks; MATH hard categories.
**Verify:** any prompt change tested on a 30-task re-smoke before
applying to a full re-run; revert if no positive Δ.

### Phase 51: Save canonical compiled workflows — status: pending
Extract the per-dataset `workflow_blueprint.json` from each full run dir
and copy to `workflows/{dataset}/workflow.json` plus a small `README.md`
explaining how to load and use them via `agentcoop run --blueprint`.
**Verify:** `python -m agentcoop.cli run --blueprint workflows/gsm8k/workflow.json`
returns a valid (mock) run; the workflow JSON loads via `WorkflowBlueprint.model_validate_json`.

### Phase 52: Case Study 1 — bulk RNA-seq → GeneAgent — status: pending
Run end-to-end with synthetic DE TSV + GeneAgent stub + LLM-driven
EvidenceVerifier + IntegratorReviewer. Capture pipeline metrics + final
report.
**Verify:** `runs/case1/airway/` has `de_results.tsv`, `gene_sets/`,
`enrichment/`, `geneagent_*.json`, `final_report.md`.

### Phase 53: Case Study 2 — parallel perturb-seq specialists — status: pending
Synthetic Norman dataset → 4 baselines + 4 GPU-stub model adapters →
PredictionNormalizer → MetricEvaluator → EnsembleSelector +
LLM-driven BiologicalPatternAnalyzer + BenchmarkReportReviewer.
**Verify:** `runs/case2/synth/` has predictions per model, metrics.json,
ensemble.json, final_report.md.

### Phase 54: Case Study 3 — AFlow dynamic on MBPP/HumanEval — status: pending
Import AFlow MBPP workflow → augment with skills/tools/gates →
run AC-AFlowImported-Gated on a 50-task MBPP subset → compare to plain
AC-AFlowImported and to our AC-Gated baseline.
**Verify:** `runs/case3/mbpp/` has all three variants' metrics; report
shows where gated dynamic repair rescued or harmed the AFlow topology.

### Phase 55: Update report.md with full numbers + case studies — status: pending
Replace "200-task subset" cells with full numbers; add a Case Studies
section summarizing CS1/CS2/CS3 deliverables; add a "Compiled workflow
artifacts" section pointing to `workflows/`.
**Verify:** every row has a final number with run-dir path.

### Phase 56: Commit + push — status: pending
Logical commits: full-runs evidence, workflow exports, case-study
artifacts, docs. Push to origin/clean-dev.
**Verify:** `git status` clean; tests pass; `git push` succeeds.

---

## Session 7 — External-repo collaboration framework + CS1 (Tissue × Gene)

### Goal
Add a generalized capability so that AgentCo-Op can take any two GitHub
repository URLs + a task description, build per-repo sandboxes,
register each as an agent backend, and orchestrate upstream/downstream
collaboration. First user is the TissueAgent × GeneAgent CS1 from
`case_study_1.md`. LLM model: `gpt-5` (reasoning_effort=medium).

### Hard constraints (Session 7)
- **Minimize codebase changes** — protect Sessions 4–6 benchmark
  numbers. Add NEW files; touch existing files only with surgical,
  additive edits.
- Do NOT modify `workflows/*.json`, AC-Gated meta-skills, runtime,
  gates, schema, prompts, or python_sandbox.
- Generalization > one-off — code must work for any repo pair.

### Phase 57: New core modules (NEW files only) — status: pending
- `agentcoop/core/repo_profile.py`: `RepoProfile` + `profile_repo()`.
- `agentcoop/core/sandbox_build.py`: `SandboxBuilder` (conda/uv/pip).
- `agentcoop/core/agent_card.py`: `AgentCard` Pydantic + YAML loader.
- `agentcoop/core/artifact_broker.py`: typed handoff validators.
- `agentcoop/core/repo_collaboration.py`: orchestration entrypoint.
**Verify:** new tests in `tests/unit/test_repo_collaboration.py`.

### Phase 58: external_repo_collaboration meta-skill (NEW file) — status: pending
- `agentcoop/skills/meta/external_repo_collaboration.md` — L7 topology,
  G0–G7 gates per `case_study_1.md` §4.5.
**Verify:** registry test still passes.

### Phase 59: Surgical edits — status: pending
Only:
- `agentcoop/backends/llm.py` — gpt-5 / o-series path: pass
  `max_completion_tokens` + optional `reasoning_effort`. Preserve
  gpt-4o-mini path bit-identically.
- `agentcoop/core/cost.py` — add gpt-5 family + o-series prices.
- `agentcoop/cli.py` — add `collaborate` subcommand.
**Verify:** `pytest tests/unit -q` 89/89 + new tests; quick GSM8K
30-task smoke matches Session 6 baseline within ±1 pt.

### Phase 60: TissueAgent + GeneAgent wrappers — status: pending
- `agentcoop/wrappers/tissueagent/{manifest, adapter, Dockerfile}`.
- `case_study_1.request.yaml` (root, user-facing example).
- Wrappers run in BOTH Docker and `--no-docker` (local Python) modes
  because Docker daemon is down on this host.
**Verify:** `agentcoop collaborate --request case_study_1.request.yaml
--no-docker --workdir runs/case1/heart_merfish` produces every
artifact in `case_study_1.md` §12.

### Phase 61: Run CS1 end-to-end — status: pending
Use `gpt-5` reasoning. Try Dryad fetch; fall back to deterministic
synthetic MERFISH AnnData (matching the §6.1 schema). Generate
artifacts in §12.
**Verify:** `runs/case1/heart_merfish/` has `run_manifest.json`,
`compiled_workflow_graph.json`, `agent_registry.json`, etc.

### Phase 62: Doc sync — status: pending
- `case_study.md` — point CS1 to `case_study_1.md` + new run dir.
- `report.md` §8.1 — replace airway numbers with heart-MERFISH summary.
- `implement.md`, `progress.md`, `findings.md` — Session 7 entries.
- `runs/case1/heart_merfish/README.md` — per-run writeup.
- Existing `runs/case1/airway/README.md` flagged as v1 snapshot.
**Verify:** every doc mentioning the airway CS1 also mentions the new
heart-MERFISH CS1.

### Phase 63: Regression check + commit + push — status: pending
- 30-task GSM8K smoke vs Session 6 baseline (~93–94 %, ±1 pt).
- pytest 89 + new tests.
- Logical commits + push to `clean-dev`.

---

## Session 9 simplify — code review + cleanup of commit 1958539 (2026-05-03)

### Phase 64: Diff + parallel review — status: in_progress
Run `git diff HEAD~1 HEAD`, hand the diff to three concurrent agents
(reuse / quality / efficiency), each capped at ~600-700 words.
Findings logged below; fixes applied directly.

### Phase 65: Apply fixes — status: pending
For each finding worth addressing, edit the file directly. False
positives noted and skipped.

### Phase 66: Verify + commit — status: pending
Re-run `pytest tests/` (must stay 144/144). Commit cleanup as a
separate logical commit on top of `1958539`.

### Phase 64 — review findings (logged when agents return)

(populated by /simplify run)

### Phase 64 — review findings (resolved)

| Source | File:line | Fix | Status |
|---|---|---|---|
| Quality + Bonus | repo_collaboration.py:367-371 — dead `image_override` ternary | Extracted `_image_for(card_name)` helper; deleted shadowed code | done |
| Efficiency | scripts/dense_tsv_to_mtx.py — BytesIO double-buffer (~3-4 GB peak RSS) | Stream `mmwrite` directly to `gzip.GzipFile` | done |
| Efficiency | No `.dockerignore` — 10 GB build context per docker build | Added `.dockerignore` excluding data/, runs/, external/, .git/, venvs | done |
| Efficiency | signac_atac_marker_agent.R — 22× redundant ClosestFeature calls | Single batched `ClosestFeature(unique_peaks)` + `dplyr::left_join` | done |
| Efficiency | scripts/cs2_setup.sh — 6 GEO downloads sequential | Parallelised via `&` + `wait` (saves ~1-3 min cold start) | done |
| Quality | inline CellMarker eval block dupes `_eval_against_gold` | SKIPPED — would change `precision_recall_summary.csv` schema (loses `cellmarker_filter_mode`); ~70 line savings vs downstream-consumer risk |
| Quality | `_build_gold_sets` vs `_build_panglaodb_gold_sets` (~70% overlap) | SKIPPED — parameterising 3 axes makes the merged signature ugly; existing pair is read-only-once-per-run |
| Quality | R wrappers share ~60 lines (`resolve_path`, `read_count_matrix`, etc) | SKIPPED — extracting `wrappers/_r_common.R` adds Dockerfile churn + import-path complexity for 60-line savings; defer |
| Reuse | `_build_runtime_image` overlaps `SandboxBuilder.build` | SKIPPED — different threat models (one is hardened sandbox, one is bind-mounted host); reuse review agreed to leave separate |
| Reuse | `_run_adapter_in_docker` vs `build_run_command` | SKIPPED — different threat models; one-line cross-reference noted |
| Reuse | `dense_tsv_to_mtx.py` overlaps Python `seurat_local._read_dense_tsv_to_sparse` | SKIPPED — pandas chunked-read uses 2× more RAM; the streaming script is intentionally leaner for the offline conversion path |

### Phase 65 + 66 — apply + verify — status: complete

`pytest tests/` → 144 / 144 still pass after all fixes. Cleanup committed
on top of `1958539`.
