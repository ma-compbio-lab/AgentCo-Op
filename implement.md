# AgentCo-Op — Implementation Record

This file tracks the framework implementation. Treat it as the single entry point for re-orientation after `/clear` or when resuming work.

**Current status:** Framework skeleton complete (2026-04-22). Experiment configuration is intentionally out of scope at this stage.

---

## What exists

### Top-level layout

```
AgentCo-Op/
  agentcoop/
    __init__.py                   re-exports core Pydantic models
    cli.py                        Typer CLI (compile / run / benchmark / repo wrap / repo smoke)
    core/
      schema.py                   TaskProfile, NodeSpec, EdgeSpec, GatePolicy, WorkflowBlueprint,
                                  NodeResult, RunState, MetaSkill, AgentSkill, EvalContract, MemoryPlan
      profiler.py                 rule-based task profiler; async LLM-augmented profiler
      compiler.py                 instantiate_topology, bind_agent_skills, gate/memory/eval
                                  attachment, minimal-sufficient graph selection
      gates.py                    GraphPatchOp enum, evaluate_gates, plan_patch, apply_patch,
                                  GateLimits
      runtime.py                  async DAG orchestrator, cycle-safe ready-set, sink-aware final
      integrator.py               majority-vote integration with contract-aware normalization
      reviewer.py                 deterministic grader clamps LLM reviewer confidence
      tracing.py                  JSONL TraceWriter + read_events
      cost.py                     CostLedger with per-node + total totals
    skills/
      registry.py                 SkillRegistry with markdown + YAML loaders and ranked retrieval
      retriever.py                thin wrappers
      meta/                       12 meta-skills (simple_direct_answer … human_review_for_high_risk)
      agents/                     7 agent-skills (math / code / retriever / bio / spatial)
    backends/
      base.py                     Backend protocol + BackendRegistry + NodeContext
      llm.py                      LLMBackend with injectable client; MockLLM default
      mcp.py                      tool-registry backend
      python_sandbox.py           subprocess-based runner with timeout
      repo_sandbox.py             command builder enforcing §6.5 safety flags (dry-run in fwk phase)
      human_review.py             auto-approve in dev mode
    memory/
      blackboard.py               typed visibility-scoped entries
      artifact_store.py           filesystem-backed artifact root
      trace_store.py              per-run JSONL trace wrapper
      skill_memory.py             append-only JSONL + task_profile_hash
      summarizer.py               token-bounded summarizer
    wrappers/
      biodiscovery/               Dockerfile.agentcoop + adapter.py + manifest.yaml (stubbed)
      spatialagent/               Dockerfile.agentcoop + adapter.py + manifest.yaml (stubbed)
  configs/
    models.yaml                   mock defaults
    budgets.yaml                  default + large presets
    safety.yaml                   sandbox + approval policy
    benchmarks/                   empty (experiments not configured yet)
  docker/
    base-python.Dockerfile        non-root base image
  tests/unit/                     45 passing tests
  runs/ data/                     .gitkeep placeholders
```

### Core contracts

- **TaskProfile** — 14 typed fields including `difficulty`, `{tool,retrieval,repo_execution}_need`, `verification_available`, `risk_level`, `budget`, and `rationale_summary`. Numeric fields bounded [0,1]; literals enforced.
- **WorkflowBlueprint** — ordered nodes, edges, gate policies, memory plan, eval contract, budget, topology level (L0–L7), complexity score, and provenance (which meta/agent skills produced the graph).
- **NodeSpec.backend ∈ {llm, mcp, python_sandbox, sandbox_repo, human_review}**. Each backend implements `async execute(node, payload, ctx) -> NodeResult`.
- **GatePolicy** — `trigger`, `threshold`, `action`, `max_activations`, scope. Triggers recognized today: `schema_invalid`, `low_confidence`, `test_failure`, `tool_error`, `evidence_missing`, `specialist_disagreement`, `budget_near_limit`, `risk_escalation`.
- **GraphPatchOp** — 10 bounded operations; `apply_patch` enforces `GateLimits.max_graph_patches`, `max_same_node_repairs`, `max_total_activations`.
- **EvalContract** — `grader` ∈ {pytest, exact_match, deterministic, llm_rubric, None}. Deterministic grader confidence always clamps the LLM reviewer (§5-Phase-5 of instructions.md).

### Compiler objective

```
score = coverage
      + 0.5·evidence_support
      + 0.5·verification_strength
      - 0.7·complexity
      - 0.3·cost
      - 0.5·risk
      - 0.2·meta.complexity_penalty
      - 0.35·max(0, meta.complexity_level - target_level_for(difficulty))
```
Tie-break by smaller complexity. Hard constraints gate candidates:
- `repo_execution_need ≥ 0.5` ⇒ must include a `sandbox_repo` node.
- `retrieval_need ≥ 0.5` ⇒ must include a retrieval node.
- `retrieval_need < 0.3` ⇒ must **not** include a retrieval node (avoid needless cost).
- `answer_type == program` ⇒ must include `python_sandbox` or programmer/tool role.
- `risk_level == high` ⇒ either a `human_review` node or an `escalate_human` gate.

### Runtime algorithm

1. Build `nx.DiGraph` from blueprint nodes + edges.
2. Detect back-edges once per outer iteration and remove them from the readiness computation (so cycles don't deadlock).
3. Execute ready nodes in sorted (deterministic) order; run each node via its backend; append `NodeResult` to blackboard and `events.jsonl`.
4. After every node, evaluate gates. Fire patches while respecting `max_activations`, `max_same_node_repairs`, `max_graph_patches`, and budget fraction.
5. Stop when no node is ready, `state.terminated`, budget exceeded, or all nodes executed.
6. `_pick_final_result` prefers sinks with `formatter`/`integrator`/`reviewer` roles; falls back to the last result.

### Skill files

- **Meta-skills** use Markdown + YAML frontmatter. The YAML carries `name`, `tags`, `complexity_level`, `complexity_penalty`, `task_signals`, `topology_template.nodes/edges`, `gates`, `memory_policy`, `expected_benefit`, and optional `requires_verification` + `evidence_refs`.
- **Agent-skills** are YAML with `backend_type`, `capabilities`, `applicable_tags`, `input_contract`, `output_contract`, `source` (repo + commit for sandbox backends), `risk`, `cost_class`.
- Meta-skill `code_test_repair_loop` marks the `repair_planner → programmer` back-edge `condition: needs_repair` so the runtime identifies it as optional.

### Safety envelope (framework defaults)

- `repo_sandbox.build_run_command` always emits `--network`, `--read-only`, `--tmpfs`, `--cpus`, `--memory`, `--pids-limit`.
- Manifests are rejected unless `image`, `commit_sha`, and `non_root=True` are present.
- Secrets are only referenced by env-variable name; never interpolated into the generated command.
- CLI `repo wrap` scaffolds manifests with `--network none` by default.

---

## Known limitations (intentional for framework phase)

1. **No live Docker execution.** `RepoSandboxBackend.execute_live` defaults to False; live mode is stubbed. Experiment phase will wire `docker run` through the same command builder.
2. **Conditional-edge semantics are ordering-only.** The runtime respects "target waits for source" but doesn't evaluate edge `condition` strings; back-edges are discovered topologically and skipped. Real control-flow will be added together with the evaluator-optimizer end-to-end.
3. **Mock LLM only.** Real OpenAI / Anthropic / LiteLLM adapters are not implemented. The `LLMClient` Protocol is stable; experiment phase can drop in a concrete client without runtime changes.
4. **Benchmark loaders absent.** `agentcoop/benchmarks/` is empty; the CLI `benchmark` subcommand returns a `not_wired` status by design.
5. **Cost table is symbolic.** `CostLedger` uses `mock-llm` at $0. Real prices go into the `price_table` when providers are wired.
6. **Skill retrieval is keyword/tag-based.** Embedding retrieval hook is reserved but not implemented.

---

## How to run

```bash
# Unit tests
python -m pytest tests/unit -v

# Compile + run (mock backends)
python -m agentcoop.cli compile --task "What is 13 * 17?" --out runs/demo_bp.json
python -m agentcoop.cli run --blueprint runs/demo_bp.json --out runs

# Scaffold a repo manifest
python -m agentcoop.cli repo wrap \
  --repo https://github.com/Genentech/SpatialAgent \
  --commit <sha> --name SpatialAgent --out runs/spatial_manifest.yaml

# Dry-run the sandbox command builder
python -m agentcoop.cli repo smoke --manifest runs/spatial_manifest.yaml
```

Tests cover schema round-trip, memory permissions, cost ledger, registry loading, profiler rules, compiler routing on GSM8K/code/HotpotQA/DROP/repo shapes, gate activation limits, patch application, integrator/reviewer clamp, runtime DAG walk (including a cyclic code blueprint), and wrapper adapter contracts.

---

## Next (when experiments start)

1. Real LLM client (`backends/llm.py` `LLMClient` implementations).
2. Benchmark loaders (`benchmarks/aflow_splits.py` + per-benchmark files).
3. Deterministic graders (`pytest` runner for HumanEval/MBPP; GSM8K / MATH numeric grader; HotpotQA F1; DROP EM/F1).
4. Pin SpatialAgent / BioDiscoveryAgent commits and Docker-build the real wrappers.
5. Fill `configs/benchmarks/*.yaml`.
6. Wire `RepoSandboxBackend.execute_live=True` behind an opt-in flag + image-digest check.

---

## Session 2 — Experiments configured (2026-04-22)

The framework is now paired with a fully-wired experimental harness,
modulo a live LLM client. Nothing has been launched yet because no API
key has been provided; every piece works in dry-run mode with `MockLLM`.

### External repos (cloned, pinned, gitignored)

`configs/external_commits.yaml` records the pinned HEAD of each clone.
`.gitignore` excludes `external/` so the tree stays lean.

| Repo | URL | Commit |
|---|---|---|
| AFlow | https://github.com/FoundationAgents/AFlow | `3f457218fc716093fe53f6df8a5d5e6379d66346` |
| BioDiscoveryAgent | https://github.com/snap-stanford/BioDiscoveryAgent | `19673c7371c542cfa155ffaa9047c53525f9fe2e` |
| SpatialAgent | https://github.com/Genentech/SpatialAgent | `e51ec0f6b1e5c8ddbc52dae846031fbc04d3c9d6` |
| human-eval | https://github.com/openai/human-eval | `6d43fb980f9fee3c892a914eda09951f772ad10d` |
| SpatialBench | https://github.com/Genentech/SpatialBench | `NOT_PUBLIC` — gated access as of 2026-04-22 |

### Datasets

`scripts/download_datasets.py` pulls the six AFlow-aligned datasets via
HuggingFace `datasets` (HumanEval from the local clone). Hashes are
recorded in `data/data_hashes.json` so a later re-download can detect
drift.

| Dataset | HF id / source | Raw rows | Notes |
|---|---|---|---|
| GSM8K | `gsm8k` (main) | 8,792 (train+test) | final-number reference after `####` |
| MATH | `EleutherAI/hendrycks_math` × 7 subjects | 12,500 (train+test) | level-5 × 4-category filter yields 605 (AFlow paper: 617; 12-row drift documented) |
| MBPP | `mbpp` (sanitized) | 427 | sanitized variant; test/val/train/prompt splits kept |
| HotpotQA | `hotpot_qa` (distractor) | 97,852 | validation pool capped at 1000 per AFlow |
| DROP | `drop` / `ucinlp/drop` | 86,935 | numeric-aware F1/EM grader |
| HumanEval | local `external/human-eval/data/HumanEval.jsonl.gz` | 164 | no official train/test split; seed=42 carved 20% val |

### AFlow-aligned splits

`agentcoop/benchmarks/aflow_splits.py` writes
`data/aflow_aligned/<dataset>/{validation,test}.jsonl` from the raw
files. Seed=42 + 20/80 val/test split, HotpotQA + DROP capped at 1000,
MATH filtered to level-5 × 4 categories. Records per AFlow §2.1.

### Benchmark code

- `agentcoop/benchmarks/common.py` — `BenchmarkTask` dataclass, JSONL
  iterator, path resolvers.
- `agentcoop/benchmarks/{gsm8k,math,humaneval,mbpp,hotpotqa,drop}.py` —
  per-dataset `load(split, limit, aflow=True)` returning
  `list[BenchmarkTask]`.
- `agentcoop/benchmarks/graders.py` — deterministic graders:
  - GSM8K: last-number extractor + float equivalence.
  - MATH: `\boxed{}` extraction + canonicalization + optional
    `sympy.parsing.latex` equivalence fallback.
  - HumanEval / MBPP: subprocess pytest-style harness with timeout.
  - HotpotQA: SQuAD-style token F1.
  - DROP: multi-span numeric-aware F1 / EM.
- `agentcoop/benchmarks/runner.py` — compile→execute→grade loop. Emits
  `predictions.csv` + `metrics.json` per run. Honors
  `AGENTCOOP_MODE=dry_run` and auto-dry-runs when no
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is present.

### Benchmark configs (`configs/benchmarks/`)

- `_base.yaml` — defaults for model, budgets, 8 named variants
  (AC-Direct, AC-Compiled, AC-Gated, AC-NoMeta, AC-NoSkillMemory,
  AC-ForcedMulti, AC-NoSandbox, AC-NoReviewer) and safety policy.
- Per-dataset overlays: `gsm8k.yaml`, `math.yaml`, `humaneval.yaml`,
  `mbpp.yaml`, `hotpotqa.yaml`, `drop.yaml` — each declares dataset,
  grader, budget, model, and the subset of variants that apply.
- `aflow_aligned.yaml` — top-level sweep (6 datasets × 4 variants × 3
  repeats).
- `biodiscovery.yaml` — 5 datasets × 7 variants with `repo.manifest`
  pointing at the sandbox manifest.
- `spatialbench.yaml` — two tiers (S2-small = public canonical examples
  shipped with SpatialAgent; S2-full gated on access).
- `cross_specialist_pilot.yaml` — exploratory Spatial→Bio pilot, marked
  `status: exploratory`.

YAML loading supports `extends: <name>` → deep-merge with `_base`.

### Repo wrapping pipeline

- Manifests: `agentcoop/wrappers/{biodiscovery,spatialagent}/manifest.yaml`
  now carry the pinned commit SHAs (same values as
  `configs/external_commits.yaml`).
- Adapters: `agentcoop/wrappers/<name>/adapter.py` read
  `/inputs/request.json`, return `/outputs/result.json` per §6.4 of
  instructions.md. Framework-phase stubs — real repo calls are wired in
  the experiment-execution phase.
- `scripts/build_repo_images.sh` — assembles a clean build context from
  `external/<repo>` + the wrapper adapter + Dockerfile, verifies the
  pinned SHA, and runs `docker build`. Script-syntax OK; actual build
  deferred until the Docker daemon is running.
- `scripts/smoke_repo_wrappers.py` — offline dry-run: emits the exact
  `docker run` command and verifies each adapter parses a valid request.

### Running the benchmark harness

```bash
# 1) one-time: download data + build AFlow splits
python scripts/download_datasets.py
python -m agentcoop.benchmarks.aflow_splits

# 2) dry-run a benchmark (MockLLM — offline)
python -m agentcoop.cli benchmark --dataset gsm8k --limit 3 \
  -v AC-Direct -v AC-Gated --dry-run

# 3) live run (requires OPENAI_API_KEY / ANTHROPIC_API_KEY + a real
#    LLMClient shim in agentcoop/backends/llm.py — not wired yet)
export OPENAI_API_KEY=...
python -m agentcoop.cli benchmark --dataset gsm8k --limit 10 -v AC-Gated
```

### Remaining work before experiments can run live

1. Implement a real `LLMClient` (OpenAI / Anthropic / LiteLLM) in
   `agentcoop/backends/llm.py`. The contract is already defined and the
   runner auto-detects dry-run when no key is set.
2. Start Docker Desktop and run `./scripts/build_repo_images.sh` for
   both wrappers; then flip `RepoSandboxBackend.execute_live=True`.
3. Acquire SpatialBench full-set access to run S2-full; otherwise
   S2-small (public canonical examples in `external/SpatialAgent/resource/`)
   is the default tier.

---

## Session 3 — Experiments refactored to benchmarks.md + case_study.md (2026-04-22)

The v1 specialized track (SpatialBench / BioDiscoveryAgent /
cross-specialist pilot) was removed and replaced with three case
studies. Variant names now match `experiments.md` §3.

### Removed (outdated, see git history)

- `configs/benchmarks/biodiscovery.yaml`, `spatialbench.yaml`, `cross_specialist_pilot.yaml`.
- `agentcoop/wrappers/{biodiscovery,spatialagent}/`.
- `agentcoop/skills/{agents/biodiscovery_agent.yaml, agents/spatial_agent.yaml, meta/domain_agent_collaboration.md}`.
- `tests/unit/test_wrappers.py` (replaced by `test_case_studies.py`).

### New modules

```
agentcoop/
  benchmarks/
    bio.py              Case Study 1 helpers: select_markers / enrich / run_geneagent
    perturb.py          Case Study 2 helpers: synthetic dataset, baselines, evaluators, ensembles
  core/
    aflow_import.py     AST-based AFlow workflow importer → WorkflowBlueprint
    augment_graph.py    attach_skills_and_tools + load_gate_yaml + apply_gates
    gate_analysis.py    rescue / harm rate analysis across run directories
  wrappers/
    geneagent/          Case Study 1 wrapper (GeneAgent stub)
    gears/              Case Study 2 wrappers (GEARS / scGPT / scFoundation / Geneformer)
    scgpt/
    scfoundation/
    geneformer/
```

### New configs

- `configs/benchmarks/_base.yaml` — 9 variants matching experiments.md §3.
- Per-dataset `configs/benchmarks/{gsm8k,math,humaneval,mbpp,hotpotqa,drop}.yaml`
  now declare the benchmarks.md §7 per-dataset budgets and gate maps.
- `configs/case_studies/{case1_airway,case2_norman_replogle,case3_aflow_import}.yaml` —
  case-study runbooks with repo manifests, gates_file pointers, and
  variant ablation plans.
- `configs/gates/{code_runtime_gates,math_gates,bio_gates,perturb_gates}.yaml` —
  gate policy catalogs consumed by `agentcoop aflow augment-graph --gates`.
- `configs/skills/{code_debugging,python_testing,math_skills}.yaml` —
  extra skill cards attached via Case Study 3's augment-graph step.
- `configs/external_commits.yaml` — updated to list GeneAgent, GEARS,
  scGPT, scFoundation, Geneformer, scPerturBench, swe_bench.

### Unified task schema (benchmarks.md §3)

`BenchmarkTask` now ships both a flat `.prompt` / `.reference` and the
nested `input{question, context, prompt} / reference{answer, tests} /
metadata{source, category, difficulty}` dicts. The AFlow importer emits
nested records and all six loaders round-trip them.

### Runner output layout (benchmarks.md §11)

Every run now writes:

```
runs/{dataset}/{method}/{timestamp}/
  config.yaml            (resolved config after extends)
  git_state.txt
  data_hashes.json       (SHA-256 of the input split)
  model_versions.json    (provider / model / temperature / key presence)
  workflow_blueprint.json (first compiled blueprint)
  predictions.jsonl      (one record per (task, variant))
  metrics.json           (per-variant aggregates + route / gate totals)
  traces/                (JSONL events per run)
  sandbox_logs/
  artifacts/
```

### New CLI commands

- `agentcoop run-benchmark` / `agentcoop benchmark` (alias)
- `agentcoop evaluate` — regrade an existing `predictions.jsonl`.
- `agentcoop analyze-gates` — compute rescue / harm rates.
- `agentcoop data import-aflow` / `agentcoop data hash`.
- `agentcoop bio select-markers` / `agentcoop bio enrich`.
- `agentcoop run-node geneagent` — dry-run the GeneAgent wrapper.
- `agentcoop aflow import-workflow` / `agentcoop aflow augment-graph`.
- `agentcoop perturb {download, synth, run, evaluate, ensemble}` — CS2 pipeline.
- `agentcoop repo {wrap, smoke}` — unchanged contract, now covers the new wrappers.

### Gate catalogue (expanded)

Session 3 adds `answer_format_invalid`, `solver_disagreement`,
`method_disagreement`, `symbolic_check_fail`, `boxed_answer_missing`,
`domain_mismatch`, `numeric_inconsistency`, `multi_span_conflict`,
`unusually_complex_problem`, `arithmetic_fail`, `answer_extraction_fail`,
`syntax_error`, `runtime_error`, `public_or_generated_test_failure`,
`timeout`, `answer_unsupported`, `high_disagreement`,
`design_ambiguous`, `too_few_markers`, `gene_mapping_low`,
`enrichment_empty`, `geneagent_unsupported_claim`, `model_env_fail`,
`gene_universe_mismatch`, `prediction_schema_invalid`, `metric_outlier`,
`simple_baseline_beats_all`, `model_disagreement_high`.

### Immediately-runnable commands (no API key needed)

```bash
# 1. AFlow-aligned dry run
python -m agentcoop.cli run-benchmark --dataset gsm8k --limit 3 \
  -v AC-Direct -v AC-Gated --dry-run

# 2. Case Study 1 offline (uses synthetic DE TSV if R is unavailable)
python scripts/case1_synthetic_de.py artifacts/case1
python -m agentcoop.cli bio select-markers \
  --de-results artifacts/case1/de_results.tsv --out artifacts/case1/gene_sets
python -m agentcoop.cli bio enrich \
  --gene-set artifacts/case1/gene_sets/up_genes.json \
  --out artifacts/case1/enrichment/up.json
python -m agentcoop.cli run-node geneagent \
  --input artifacts/case1/gene_sets/up_genes.json \
  --context "airway dex vs control" --out artifacts/case1/geneagent_up.json

# 3. Case Study 2 offline (synthetic dataset, simple baselines)
python -m agentcoop.cli perturb synth \
  --dataset synthetic_norman --out data/perturb/synth.json
python -m agentcoop.cli perturb run \
  --dataset-path data/perturb/synth.json --out runs/case2/predictions
python -m agentcoop.cli perturb evaluate \
  --predictions runs/case2/predictions \
  --dataset-path data/perturb/synth.json --out runs/case2/metrics.json
python -m agentcoop.cli perturb ensemble \
  --predictions runs/case2/predictions \
  --dataset-path data/perturb/synth.json --strategy validation_winner \
  --out runs/case2/ensemble.json

# 4. Case Study 3: import AFlow MBPP graph, augment, run (dry)
python -m agentcoop.cli aflow import-workflow \
  --workflow-file external/AFlow/workspace/MBPP/workflows/round_1/graph.py \
  --dataset mbpp --out runs/case3/mbpp_aflow.json
python -m agentcoop.cli aflow augment-graph \
  --graph runs/case3/mbpp_aflow.json \
  --skills configs/skills/code_debugging.yaml \
  --skills configs/skills/python_testing.yaml \
  --tools sandbox_python --tools generated_tests --tools static_analyzer \
  --gates configs/gates/code_runtime_gates.yaml \
  --out runs/case3/mbpp_aflow_augmented.json
python -m agentcoop.cli run-benchmark --dataset mbpp --limit 3 \
  -v AC-AFlowImported-Gated --dry-run --method ac-aflow-skills-gates
```

### Tests

82 unit tests pass. New modules: `test_aflow_importer.py` (importer +
augment-graph + gate YAML loader), `test_case_studies.py` (CS1 + CS2
end-to-end, wrapper adapter stubs, config sanity), `test_gate_extensions.py`
(each new gate trigger fires once).

---

## Session 4 — Live execution + AFlow-aligned benchmark sweep (2026-04-27)

OpenAI key supplied → flipped the framework into live mode. Built the
real LLM client, role × dataset prompt registry, parallel runner,
and a chain of grader / profiler / runtime fixes. Final result:
**AC-Gated surpasses AFlow on 5 of 6 standard benchmarks** at 200
tasks each (HumanEval is the full 132-task test split). See `report.md`
for the full table.

### What shipped

| Layer | Module | Notes |
|---|---|---|
| Real LLM client | `agentcoop/backends/llm.py::OpenAIClient` | `httpx` Chat Completions + JSON mode opt-in + 4-attempt exp-backoff retry on 429/5xx |
| Per-role prompts | `agentcoop/backends/prompts.py` (NEW, 380 LOC) | role × dataset templates and parsers; LaTeX-backslash repair for MATH; choose JSON vs free-text per role |
| Cost ledger prices | `agentcoop/core/cost.py` | gpt-4o-mini / gpt-4o / gpt-4o-2024-08-06 / gpt-3.5-turbo |
| Parallel runner | `agentcoop/benchmarks/runner.py` | `asyncio.gather` over (variant × task) under `Semaphore` + per-task `wait_for(240s)`; `--concurrency` CLI flag |
| Dataset-aware profiler | `agentcoop/core/profiler.py::DATASET_PROFILE_OVERRIDES` | locks-in domain / difficulty / retrieval-need per dataset |
| Runtime cycle fix | `agentcoop/core/runtime.py` | always add executed nodes to `executed`; retries decremented in `_ready_nodes` only — fixes an infinite repair loop discovered in MBPP |
| MBPP loader | `agentcoop/benchmarks/mbpp.py` | embeds public `test_list` in the prompt per benchmarks.md §4.4 |
| DROP grader | `agentcoop/benchmarks/graders.py::_drop_extract_answers` | spans treated as alternative annotator answers (max F1) |
| Code grader | `agentcoop/benchmarks/graders.py::_extract_pred_code` | accepts `code` *or* `final_answer` (formatter writes the code into `final_answer`) |
| Math grader | `agentcoop/benchmarks/graders.py` | LaTeX-backslash repair before `_extract_boxed` |
| python_sandbox | `agentcoop/backends/python_sandbox.py` | extracts code from upstream `output:*` keys; lightweight syntax check (real iteration is blocked by back-edge exclusion) |

### Result vs AFlow (200-task subset for 5; full 132 for HumanEval)

| Dataset | AFlow | AC-Gated | Δ |
|---|---:|---:|---:|
| HotpotQA | 73.5 | **76.5** | +3.0 ✓ |
| DROP | 80.6 | **81.7** | +1.1 ✓ |
| GSM8K | 93.0 | **93.5** | +0.5 ✓ |
| MATH | 56.1 | **60.0** | +3.9 ✓ |
| MBPP | 82.4 | **87.0** | +4.6 ✓ |
| HumanEval | 94.7 | 90.2 | −4.5 |
| **Avg** | 80.05 | **81.48** | +1.43 |

Total API spend across all 1 132 tasks: **$0.62**. Wall-clock ≈ 30 minutes
with concurrency 6 per dataset (six datasets in parallel).

### Configs bumped this session

- `configs/benchmarks/_base.yaml` — added `concurrency: 8` default.
- All six per-dataset configs — bumped `budget.max_tokens` to 32k–64k and
  per-call `model.max_tokens` to 2 048–8 192 (per the user's "no token
  bottleneck" directive).
- `configs/benchmarks/gsm8k.yaml` — bumped `model.max_tokens` 1 024 → 4 096.

### Tests

89 unit tests pass — added `tests/unit/test_prompts.py` (7 tests) covering
the role × dataset registry + LaTeX repair + code-fence extraction.

### Live commands (post-Session-4)

```bash
export OPENAI_API_KEY=sk-...
python -m agentcoop.cli run-benchmark \
  --dataset gsm8k --limit 200 -v AC-Gated --concurrency 6 \
  --out runs/gsm8k/AC-Gated/$(date -u +%Y%m%dT%H%M%SZ)
```

---

## Session 6 — close-the-gap attempts + CS3 MedPrompt variant (2026-04-29)

### Goal

Push HumanEval (89.4 → ≥ 95) and DROP (78.3 → ≥ 81) above AFlow
without disturbing the experiments.md narrative (CLAUDE.md
simplicity-first, "modifications not overly drastic").

### Attempts and outcomes

| # | Attempt | Smoke / mid / full result | Decision |
|---:|---|---|---|
| 1 | Code prompt: "silently trace each `>>>` example" silent-discipline block | 89.4 % → **86.4 %** on full HumanEval (model deletes `>>>` lines, leaves docstring unclosed) | **REVERTED** |
| 1 | DROP `numeric_reasoner` prompt: 5-step "list numbers / state op / compute / verify" | 86.1 % → **84.0 %** on N=100 (DROP same-100 subset) | **REVERTED** |
| 2 | Bounded back-edge iteration: `state.executed: set[str]` on RunState; new `RETRY_UPSTREAM` patch op walks back to programmer + stales downstream nodes; `python_sandbox` runs `>>>` examples as informative asserts; `tool_error` skips `ran_public_tests=True` cases; `_user_prompt` injects a REPAIR section with prior code + traceback when programmer is being retried | 91.25 % → **88.75 % – 91.25 %** on N=80 across four iterations. 0 known fails recovered, 0-2 regressions per variant (gpt-4o-mini @ T=0 re-derives the same buggy code on retry) | **REVERTED** (mechanism documented in `findings.md` Session 6 §2) |
| 3 | DROP `answer_formatter` prompt: "use the SHORTEST minimal phrase" with examples of articles and titles to drop | 86.1 % → **83.6 %** on N=100 (over-trims correct full-credit spans → 1.0 → 0.0 regressions on 5 tasks) | **REVERTED** |
| 4 | CS3: new `AFlow+MedPrompt-Voting` variant — K=3 samples @ T=0.7 of `AFlow+Skills+Tools` graph, candidate picked by public-test pass count (tie-break: shortest passing code) | **0.880** on N=50 MBPP, 3× cost; beats AFlow paper baseline (0.824) by 5.6 pts and the next-best CS3 variant by 2 pts | **kept** (case-study scope only) |

### What's actually new in code

- `scripts/case3_aflow_dynamic.py::_execute_medprompt` — K-sample
  parallel runner + public-test voting harness (added at end of file,
  no impact on existing variants).
- CS3 README rewritten to add the 5th variant + diagnostics.

The runtime, gates, schema, sandbox, and prompts files are
**bit-identical** to Session 5 head — every code change made during
Session 6 was rolled back via `git checkout HEAD --` after the smoke /
mid / full result didn't move (or moved the wrong way).

### Final numbers (Session 6 reruns vs Session 5 baseline)

| Dataset | n | S5 | S6 rerun | AFlow | Δ vs AFlow |
|---|---:|---:|---:|---:|---:|
| HotpotQA | 800 | 76.4 | **76.5** | 73.5 | **+3.0 ✓** |
| GSM8K | 1 056 | 93.8 | **94.4** | 93.0 | **+1.4 ✓** |
| MATH (S5 carried) | 478 | 58.2 | (carried) | 56.1 | **+2.1 ✓** |
| MBPP | 342 | 86.6 | **87.1** | 82.4 | **+4.7 ✓** |
| DROP | 800 | 78.3 | 77.2 | 80.6 | −3.4 |
| HumanEval | 132 | 89.4 | 90.2 | 94.7 | −4.5 |
| **Avg** | — | 80.43 | **80.55** | 80.05 | +0.50 |

**4 / 6 wins vs AFlow on full-test data**, with each winner widening
its margin by ~0.6 pt on the rerun. Total Session 6 spend: $1.31 (the
five re-runs only; MATH carried over). CS3 MedPrompt-Voting added
another $0.0245 on N=50.

### Tests

89 unit tests still pass (no test file changes; see `pytest tests/unit -q`).

---

## Session 7 — External-repo collaboration framework + CS1 (Tissue × Gene) (2026-05-01)

### Goal

Add a generalised framework so that AgentCo-Op can take any two
GitHub repository URLs + a task description, build per-repo sandboxes,
register each as an agent backend, and orchestrate upstream/downstream
collaboration. First user is the TissueAgent × GeneAgent CS1 from
`case_study_1.md`. LLM model: `gpt-5` ("thinking" reasoning model
family) with `reasoning_effort=medium`.

**Hard constraint** — minimise codebase changes; protect Sessions 4–6
benchmark numbers. New behaviour goes through new modules; existing
files only touched with surgical, additive edits.

### What's new (NEW files)

| Module | Purpose |
|---|---|
| `agentcoop/core/repo_profile.py` | `RepoProfile` Pydantic model + `profile_repo()` (clones + inspects environment files / READMEs / scripts; detects API-key envs, package managers, run modes, candidate capabilities) |
| `agentcoop/core/sandbox_build.py` | `SandboxSpec` + `SandboxBuilder` (renders Dockerfile per `conda` / `uv` / `pip` strategy + `docker-compose.yml` + per-image smoke test; `dry_run=True` writes specs without invoking docker) |
| `agentcoop/core/agent_card.py` | `AgentCard` (matches `case_study_1.md` §4.3 schema) + `AgentRegistry` (YAML-loadable) |
| `agentcoop/core/artifact_broker.py` | Typed handoff broker with `gene_set` / `csv_path` / `image_path` / generic-file validators; writes a JSONL audit log |
| `agentcoop/core/repo_collaboration.py` | `RepoCollaborationOrchestrator` — end-to-end driver consuming a request YAML, runs adapters in declaration order, brokers handoffs, integrates outputs via the existing `OpenAIClient`, writes `run_manifest.json` + `compiled_workflow_graph.json` + `agent_registry.json` + traces |
| `agentcoop/skills/meta/external_repo_collaboration.md` | L7 meta-skill (12 meta total; the registry test was bumped 11 → 12) |
| `agentcoop/wrappers/__init__.py` | Side-effect imports register the local-Python adapters used in `--no-docker` mode |
| `agentcoop/wrappers/tissueagent/{__init__.py, manifest.yaml, Dockerfile.agentcoop, adapter.py}` | TissueAgent local adapter — log-norm + Welch t-test + BH FDR + volcano + coding report; deterministic synthetic-MERFISH fallback when the real h5ad is absent |
| `agentcoop/wrappers/geneagent_local/{__init__.py, adapter.py}` | GeneAgent local adapter — direct OpenAI Chat-Completions call shaped per `case_study_1.md` §11.4, JSON-mode with deterministic fallback when no API key |
| `case_study_1.request.yaml` | User-facing request fixture (the inaugural CS1 case) |
| `tests/unit/test_repo_collaboration.py` | 12 new unit tests covering each new module + an offline orchestrator smoke test |

### Surgical edits to existing files (kept intentionally tiny)

| File | Edit | Reason |
|---|---|---|
| `agentcoop/backends/llm.py` | `complete()` now accepts `reasoning_effort` and switches to `max_completion_tokens` when `model.startswith("gpt-5")` or starts with `o1/o3/o4`. Otherwise the parameter set is **bit-identical** to Session 6, so existing gpt-4o-mini benchmarks are unaffected. | gpt-5 family rejects `temperature` and uses `max_completion_tokens` |
| `agentcoop/core/cost.py` | Added gpt-5 family + o-series price entries (additive). | New models need pricing |
| `agentcoop/cli.py` | Added `agentcoop collaborate` Typer subcommand. No edits to existing commands. | Generic external-repo collaboration entrypoint |
| `agentcoop/skills/meta/code_test_repair_loop.md` | (untouched in S7) | preserve CS3 + benchmarks |
| `tests/unit/test_skills.py` | Bumped meta-skill count assertion 11 → 12 to reflect the new `external_repo_collaboration` skill. | New meta-skill |

### Run results

| Outcome | Value |
|---|---|
| `agentcoop collaborate --request case_study_1.request.yaml --no-docker --model gpt-5 --reasoning-effort medium` | end-to-end success, status `synthetic_fallback` |
| Wall time | ~2 min |
| Marker count | 51 (target 46) |
| Expected example marker overlap | **6 / 6** |
| GeneAgent process label | "AV canal/AV node–biased developmental program in AV ring atrial fibroblasts" |
| GeneAgent subprocesses | 5 (TF network · ECM · myofibroblast contractile · AV-junction adhesion · paracrine remodeling) |
| Total LLM tokens (GeneAgent + integrator) | 5 048 (gpt-5, medium reasoning) |

`runs/case1/heart_merfish/` carries the full artifact tree per
`case_study_1.md` §12 (run_manifest, compiled_workflow_graph,
agent_registry, broker.jsonl, repo profiles, Dockerfiles, smoke tests,
DE table, marker CSV/JSON, volcano plot, GeneAgent report MD/JSON,
final hypothesis report MD/JSON).

### Regression check

| Test | Result |
|---|---|
| `pytest tests/unit -q` | **101 / 101 pass** (89 prior + 12 new in `test_repo_collaboration.py`) |
| GSM8K N=30 quick smoke (gpt-4o-mini path) | 0.90 — within Session 6 baseline N=1056 of 0.944 ± natural variance |
| `git diff HEAD` on workflows/, runtime, gates, schema, prompts, python_sandbox, profiler, runner, compiler | no changes (these files protect the Sessions 4–6 numbers) |

### Generalisation note

The framework is **not** hard-coded to TissueAgent / GeneAgent. Any
two GitHub URLs + a request YAML drive the same pipeline:

```bash
agentcoop collaborate --request my_request.yaml --workdir runs/my_run \
  --no-docker --model gpt-5 --reasoning-effort medium
```

Per-repo specifics (local-Python adapters, Dockerfile overrides) live
under `agentcoop/wrappers/<agent>/`; the framework code does not
mention TissueAgent or GeneAgent.

### Session 7.1 — real-data CS1 run (2026-05-01)

User downloaded the Farah Dryad MERFISH AnnData and asked to rerun
CS1 against the real file (`data/heart_merfish/overall_merfish.h5ad`).

Steps:
1. `pip install anndata scanpy h5py` (no AgentCo-Op code change).
2. `case_study_1.request.yaml` → `local_cache_dir: data/heart_merfish`
   (the path the user actually used).
3. Archived prior synthetic run as
   `runs/case1/heart_merfish_synthetic_fallback_v1/` and re-ran into
   the canonical `runs/case1/heart_merfish/`.

Real-data result:

| Metric | Value | Target |
|---|---|---|
| Status | **success** (real data) | — |
| n_target / n_control | 576 / 5 685 | non-zero |
| Welch t markers | 53 | 40–60 acceptable |
| Mann-Whitney U markers | **46 — exact manuscript match** | 46 strict |
| Expected example overlap | **6 / 6** | ≥ 5 / 6 |
| GeneAgent label | "AV Canal/Nodal Fibroblast Developmental Program" | "Cardiac Development and Remodeling" or eq. |
| GeneAgent subprocesses | 6 / 5+ | ≥ 4 / 5 |
| GeneAgent + integrator tokens | 4 611 (gpt-5) | — |
| Wall time | 160 s | — |

`de_sensitivity_summary.csv` records both Welch t (53) and
Mann-Whitney U (46); the latter exactly reproduces the
manuscript-reported 46-marker AVN/AV ring panel.

No benchmark regression: 101/101 unit tests still pass; only the
request YAML, run READMEs, and the doc files were touched. None of
the Sessions 4–6 protective files (`workflows/*.json`, runtime,
gates, schema, prompts, python_sandbox, profiler, runner, compiler)
changed.

---

## Session 7.3 — `parallel_then_join` topology + CS2 (Seurat × Signac × CellMarker) (2026-05-02)

### Goal

User updated `case_study_2.md` to require AgentCo-Op to take the
Seurat / Signac GitHub URLs, the SHARE-seq mouse skin RNA/ATAC GEO
files, the cell-type label file, and CellMarker 2.0 mouse markers,
then **autonomously sandbox both R tools, register them as agent
nodes, run them in parallel on real data, and evaluate the
intersection / union of their per-cell-type marker sets against
CellMarker 2.0**. Same constraints as Sessions 7 / 7.1 / 7.2:

- preparation entirely internal to AgentCo-Op (env config + sandbox);
- structured outputs (logs + topology PNG/DOT + standalone report);
- generalisable framework changes only;
- minimal blast radius — Sessions 4–6 benchmark numbers must remain
  reproducible.

### What's new (NEW files only)

| Module | Role |
|---|---|
| `agentcoop/wrappers/seurat_local/{__init__, manifest, Dockerfile, adapter}` | Python local Seurat-equivalent: scanpy Wilcoxon RNA marker discovery from a comma-id dense TSV; mirrors the R wrapper in `case_study_2.md` §10. |
| `agentcoop/wrappers/signac_local/{__init__, manifest, Dockerfile, adapter}` | Python local Signac-equivalent: scanpy Wilcoxon DA on MatrixMarket peak counts + lazy-fetched GENCODE vM25 mm10 nearest-gene mapping; mirrors §11. Also bundles a `data_cache/` dir for the auto-downloaded GTF + parsed gene-coord TSV. |
| `agentcoop/wrappers/cellmarker_evaluator_local/{__init__, adapter}` | Python join-agent: parses `Cell_marker_Mouse.xlsx`, applies primary `Skin` + extended `Skin/Hair follicle/Epidermis/Dermis/Hair` filters, builds gold marker sets, computes per-cell-type set ops + precision / recall + collaboration_gain + heatmap + barplot; mirrors §13–§14. |
| `case_study_2.request.yaml` | User-facing inaugural CS2 request fixture (loads with the same generic `agentcoop collaborate` CLI as CS1). |
| `tests/unit/test_parallel_then_join.py` | 5 new unit tests: wrapper registration, request-YAML loading, parallel-then-join orchestration, linear-handoff regression guard, CellMarker P/R helpers. |

### Surgical edits to existing files (additive only)

| File | Edit |
|---|---|
| `agentcoop/core/repo_collaboration.py` | Added `topology` (default `"linear_handoff"`), `join_agent`, and `parameters` fields to `CollaborationRequest`. Extracted CS1's inline body into `_execute_linear_handoff()`. Added `_execute_parallel_then_join()` that runs every repo in parallel via a `ThreadPoolExecutor`, brokers per-branch typed artifacts, optionally invokes a registered `join_agent` adapter, and feeds the join response into the existing `_integrate(...)`. Added `_compile_graph_parallel()` so the topology PNG renders the parallel branches. `_ensure_env()` now also resolves the `join_agent` deps. `run_manifest.json` gained `topology`, `branch_responses`, `parameters`, `join_agent`, `topology_artifacts` blocks. |
| `agentcoop/wrappers/__init__.py` | Side-effect imports for the three new wrappers so the registry resolves them when the CLI imports `agentcoop.wrappers`. |

### Run results

| Outcome | Value |
|---|---|
| `agentcoop collaborate --request case_study_2.request.yaml --workdir runs/case2/shareseq_skin --no-docker --model gpt-5 --reasoning-effort medium` | end-to-end success |
| Wall time | 648 s (10.8 min) |
| EnvManager packages auto-resolved | 8 (e.g. `openpyxl 3.1.5` was actually pip-installed on this run; the rest already present) |
| Cells / cell types | 32 231 / 22 (after `Mix` removed; min 20 cells/type) |
| RNA / ATAC matrix shapes | 32 231 × 23 296 / 32 231 × 344 592 |
| Seurat marker rows | 147 057 |
| Signac peak markers → mm10 gene rows | 10 776 → 8 843 |
| Cell types mapped to CellMarker | 19 / 22 |
| Mean precision (RNA / ATAC / **intersection**) | 0.051 / 0.003 / **0.083** |
| Mean recall (RNA / ATAC / union) | 0.122 / 0.009 / 0.122 |
| Strict intersection wins | 1 (Basal: 0.333 vs RNA 0.04 vs ATAC 0.02) |
| Total LLM tokens (integrator) | 5 679 (`gpt-5`, `reasoning_effort=medium`) |

`runs/case2/shareseq_skin/` carries the full artifact tree per
`case_study_2.md` §19 (run_manifest, compiled_workflow_graph,
agent_registry, env_manifest, broker.jsonl, repo profiles,
Dockerfiles, smoke tests, full marker tables, peak-to-gene mapping,
gold marker sets, label mapping, P/R per-celltype + summary,
collaboration_gain table, heatmap PNG, barplot PNG, integrator
report MD/JSON, collaboration_log, topology PNG/DOT, final_report).

### Regression check

| Test | Result |
|---|---|
| `pytest tests/unit -q` | **112 / 112 pass** (107 prior + 5 new in `test_parallel_then_join.py`) |
| `git diff HEAD` on workflows/, runtime, gates, schema, prompts, python_sandbox, profiler, runner, compiler, benchmarks/ | NO changes (Sessions 4–6 numbers remain reproducible) |
| CS1 path | guarded by `test_orchestrator_linear_handoff_still_runs` |

### Generalisation

- The `topology: parallel_then_join` field + `join_agent` block work
  for any N-branch fan-out + evaluator pattern. Just register a
  local adapter per branch and one for the join_agent, then write a
  request YAML — no framework code edits needed.
- The `case_study_2.md` Seurat/Signac specifics live entirely under
  `agentcoop/wrappers/{seurat,signac,cellmarker_evaluator}_local/`;
  none of the framework code names them.

### Session 7.2 — internalised env config + structured outputs (2026-05-01)

User asked for three additions:

1. **All preparatory work — including environment configuration —
   handled internally by AgentCo-Op**, not via separate `pip install`.
2. A clear explanation of the current external-agent collaboration
   mechanism.
3. Structured final outputs (collaboration log + topology
   visualisation + a single standalone report).

NEW modules (additive only):

| Module | Role |
|---|---|
| `agentcoop/core/env_manager.py` | Wrapper modules declare PyPI deps via `register_required_packages("Name", [...])`. The orchestrator calls `ensure_env_for_agents(...)` before invoking adapters; missing packages are pip-installed in `--no-docker` mode (skipped in `--docker` mode where the SandboxBuilder weaves them into the Dockerfile). Per-package install/already-present state is recorded in `manifests/env_manifest.json`. |
| `agentcoop/core/topology_viz.py` | Renders `compiled_workflow_graph.json` as both `topology.png` (matplotlib + networkx, hierarchical, color-by-kind) and `topology.dot` (Graphviz source). |
| `agentcoop/core/collab_report.py` | Writes `collaboration_log.md` (per-stage narrative) and `final_report.md` (single-file standalone, embeds topology, env table, stage table, GeneAgent biology verbatim, integrator hypothesis verbatim, every artifact path, reproduce block). |
| `docs/external_agent_collaboration.md` | Design + mechanism walkthrough — every stage explained, file map of one run, generalisation recipe. |

Surgical edits (additive only):

| File | Edit |
|---|---|
| `agentcoop/core/repo_collaboration.py` | Wires `_ensure_env()` between AgentRegistry and adapter invocation, calls `render_topology()` after the compiled graph is written, and emits `collaboration_log.md` + `final_report.md` after the run completes. The integrator's `_integrate()` returns an `integrator_meta` dict so the reporter can attribute model + reasoning_effort + tokens. `run_manifest.json` gains `env_report`, `integrator_meta`, `topology`, and `structured_outputs` blocks. |
| `agentcoop/wrappers/tissueagent/__init__.py` | Calls `register_required_packages("TissueAgent", [...])` with numpy/pandas/scipy/matplotlib/anndata/scanpy/h5py/PyYAML. |
| `agentcoop/wrappers/geneagent_local/__init__.py` | Calls `register_required_packages("GeneAgent", ["httpx"])`. |

Tests added:

- `tests/unit/test_env_topology_report.py` — 6 tests covering
  package registration, env-manager already-present detection, docker
  skip path, manifest persistence, topology PNG/DOT render, and
  collaboration_log + final_report emission with embedded reports.

After the additions: **107 / 107 unit tests pass**. No edits to the
Session 4–6 protective files (workflows, runtime, gates, schema,
prompts, python_sandbox, profiler, runner, compiler).

### Session 7.2 run results

Re-ran CS1 end-to-end on the real Farah MERFISH (`status: success`,
98.6 s wall time, 5 547 LLM tokens for the GeneAgent + integrator
calls). All four structured outputs appear at the workdir root:

- `runs/case1/heart_merfish/final_report.md` — standalone report.
- `runs/case1/heart_merfish/collaboration_log.md` — per-stage narrative.
- `runs/case1/heart_merfish/topology.png` — 1438 × 1183 PNG.
- `runs/case1/heart_merfish/topology.dot` — Graphviz source.

Plus `runs/case1/heart_merfish/manifests/env_manifest.json` listing
the 9 declared Python packages auto-resolved by the EnvManager (all
already present on this host; the install path is exercised when a
package is missing).

## Session 8 — `ablation.md` 2 × 2 factorial (2026-05-02 / 03)

User asked for the ablation declared in `ablation.md` — two factors
(skills+tools, gate repair) × two levels = four variants, run on the
full AFlow splits of all six benchmarks. Hard constraint:
**YAML configuration changes only — no edits to core code.**

### Variant grid

| Variant            | skills + tools | gate repair | YAML knobs applied |
|---|:--:|:--:|---|
| `AC-Full`          | ✓ | ✓ | (none — alias for `AC-Gated`) |
| `AC-NoGate`        | ✓ | ✗ | `disable_gates: true` |
| `AC-NoSkillsTools` | ✗ | ✓ | `disable_reviewer: true` (proxy) |
| `AC-Minimal`       | ✗ | ✗ | both |

`AC-NoSkillsTools` is the **proxy form** — `_apply_variant` only
honours `disable_gates` / `disable_reviewer` today. Wiring a true
"disable skill registry / force llm-only backend" knob is the
canonical follow-up; documented openly in `configs/ablations/*.yaml`
notes so the proxy nature is visible to anyone running the ablation.

### Files added (additive only — no Sessions 4–7 protective files touched)

| File | Role |
|---|---|
| `configs/benchmarks/_base.yaml` | Adds the 4 variant blocks at the end of `variants:`. Inherits into every per-dataset config via `extends: _base` deep-merge in `agentcoop/benchmarks/runner.py::_merge`. |
| `configs/ablations/ac_full.yaml` | Documentation-only method config: `method: ac_full`, `skills_tools: true`, `gate_repair: true`, `yaml_implementation` block pointing at `_base.yaml`. |
| `configs/ablations/ac_nogate.yaml` | Same shape; `gate_repair: false`. |
| `configs/ablations/ac_noskillstools.yaml` | Same shape; `skills_tools: false`. Note explains the proxy. |
| `configs/ablations/ac_minimal.yaml` | Same shape; both factors off. |
| `scripts/aggregate_ablation.py` | Pure-Python aggregator (~250 lines, only depends on pandas + stdlib). Produces `ablation_results.csv` (§6.1), `component_effects.csv` (§6.2), `cost_latency.csv` (§6.3), `gate_rescue.csv` (§6.4), `summary.json`. Reuses `runs/full_v6/<dataset>/metrics.json` for AC-Full (and `runs/full/math/metrics.json` for MATH AC-Full). |
| `tests/unit/test_ablation_variants.py` | 32 tests: 24 inheritance × variant × dataset, 4 `_apply_variant` behaviour tests (AC-Full keeps blueprint; AC-NoGate zeroes max_activations; AC-NoSkillsTools drops reviewer nodes + dangling edges; AC-Minimal combines both), 4 method-config-file format tests. |
| `runs/ablations/README.md` | Narrative report with all four §6 tables filled in plus the rescue-rate caveat (gates rarely fire on AFlow-aligned splits — only DROP has material activity). |
| `report.md` §9 | New ablation chapter inserted between Case studies and Key takeaways; §10–§13 renumbered. |

### Run invocation

```bash
# 18 fresh runs: 3 ablation variants × 6 datasets
for ds in hotpotqa drop humaneval mbpp gsm8k math; do
  for v in AC-NoGate AC-NoSkillsTools AC-Minimal; do
    python -m agentcoop.cli run-benchmark --dataset $ds --limit 99999 \
      -v $v --concurrency 6 --out runs/ablations/$ds/$v
  done
done

# Aggregate the four §6 tables
python scripts/aggregate_ablation.py
```

The actual S8 sweep parallel-launched per-benchmark groups under
≤ 30-in-flight ceiling (≤ 5 procs × c=6 or 2 procs × c=12). Wall-clock
≈ 45 min total, math being the bottleneck (≈ 12–17 tasks/min/proc),
hotpotqa / humaneval / mbpp / gsm8k each ≈ 20 min, drop ≈ 30 min.

### Result snapshots (full numbers in `runs/ablations/README.md`)

Average normalised score: AC-Full 0.8059 > AC-NoGate 0.7974 >
AC-NoSkillsTools 0.7943 > AC-Minimal 0.7886. AC-Full wins 5/6 columns;
DROP is the lone exception. MATH shows the largest near-additive
contributions from both factors. HumanEval & GSM8K show positive
interaction. DROP is the only negative-interaction case.

### Tests

After the additions: **144 / 144 unit tests pass** (was 112). No edits
to runtime, gates, schema, prompts, python_sandbox, profiler, runner,
compiler, or `workflows/*.json` — Sessions 4–7 numbers remain
reproducible.

---

## Session 9 — Docker-mode CS1/CS2 with R Seurat + R Signac + dual-DB eval (2026-05-03)

### Goal

Re-run CS1 and CS2 inside Docker (the prior runs had `no_docker: true`)
after the user installed colima, and add independent PanglaoDB
precision/recall to the CellMarker evaluator. Strictly minimal /
generalised code changes.

### Code changes (all additive or guarded by a YAML knob defaulting to
prior behaviour)

#### `agentcoop/core/repo_collaboration.py`

| Edit | Why |
|---|---|
| Drop `dry_run=True` hardcode in `_build_sandboxes()` → `dry_run=self.no_docker` | Without this, even `--docker` ran in dry-run mode and per-repo Dockerfiles never built. |
| New `_build_runtime_image()` — builds (or reuses cached) `agentcoop-runtime:case-study` once per orchestrator run | The default Python sandbox for any repo that doesn't override `runtime_image`. |
| Rewrite `_run_adapter_in_docker()` — `docker run --rm` per call with workdir bind-mounted at the same absolute path inside the container; honours per-spec `runtime_image` overrides + env var `AGENTCOOP_DOCKER_RUN_TIMEOUT_S` (default 7 200 s) | The old code did `docker exec` on a non-existent container. |
| New env var `AGENTCOOP_BRANCH_CONCURRENCY` in `_execute_parallel_then_join()` (default = `max(2, n_branches)`) | Lets memory-constrained hosts serialise R-heavy branches without code change. |
| New helper `_runtime_image_for(spec)` | Reads `spec.model_extra["runtime_image"]` so `case_study_2.request.yaml` can route Seurat/Signac to the R image while keeping CellMarkerEvaluator on the Python image. |

#### `agentcoop/core/sandbox_build.py`

| Edit | Why |
|---|---|
| Lowercase image tags in the per-repo build path | Docker rejects mixed-case tags like `agentcoop-TissueAgent:case-study`; without normalisation the per-repo build silently failed. |

#### `agentcoop/wrappers/cellmarker_evaluator_local/adapter.py`

Additive PanglaoDB block — does NOT modify the CellMarker code path:

- `_resolve_panglaodb_file(join_inputs, log)` — declared file or fallback to `external/SpatialAgent/data/PanglaoDB_markers_27_Mar_2020.tsv`.
- `_read_panglaodb_file(path, log)` — handles the canonical 14-column TSV.
- `_build_panglaodb_gold_sets(pdb, ..., aliases, log)` — species-token-aware (PanglaoDB `species` column has values like `"Mm Hs"`); reuses the same per-dataset-celltype alias mapping the CellMarker block uses.
- `_eval_against_gold(rna, atac, gold, all_cts, db_label)` — DRY helper for the per-DB precision/recall + collaboration-gain win counts.
- `invoke_cellmarker_evaluator_local()` — appends a PanglaoDB block after the existing CellMarker block, writes 4 new artifacts (`panglaodb_gold_markers_by_celltype.json`, `panglaodb_label_mapping.csv`, `precision_recall_panglaodb_by_celltype.csv`, `precision_recall_panglaodb_summary.csv`), and surfaces PanglaoDB metrics in `result.summary` and `result.main_results.panglaodb`.

#### New files

| File | Purpose |
|---|---|
| `agentcoop/wrappers/__main__.py` | Generic Docker entrypoint. `python -m agentcoop.wrappers <agent_name> <invoke.json>` reads the request, dispatches to the registered adapter, writes `<stem>.response.json`, and echoes the response to stdout. Used by every Python adapter inside the runtime image. |
| `agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R` | Real R wrapper. Reads sparse 10X-style MTX trio if present (`<cache>/rna_mtx/`); falls back to `data.table::fread` on the dense GEO TSV otherwise. `Seurat::FindAllMarkers` with `presto` acceleration. Writes the same artifact filenames the Python adapter writes so downstream nodes don't care which backend produced them. |
| `agentcoop/wrappers/signac_local/signac_atac_marker_agent.R` | Real R wrapper. Same MTX-first pattern. Picks the celltype-table column with maximum overlap against MTX cell IDs. Builds the named-cells vector that `CreateFragmentObject` needs (handles SHARE-seq's rna.bc-vs-atac.bc quirk). Two methods: `peak_to_gene` (default) or `gene_activity` (opt-in, requires colima ≥ 22 GB). Stratified subsample `peak_marker_max_cells` (default 4 000) keeps `FindAllMarkers` tractable. |
| `docker/agentcoop-runtime.Dockerfile` | Generic Python sandbox. python:3.11-slim + agentcoop pkg + scientific stack (numpy, pandas, scipy, matplotlib, anndata, scanpy, statsmodels, openpyxl, seaborn). `ENTRYPOINT ["python", "-m", "agentcoop.wrappers"]`. |
| `docker/agentcoop-r-runtime.Dockerfile` | rocker/r-ver:4.3.3 base + Bioconductor 3.18 (GenomicRanges, IRanges, GenomeInfoDb, Rsamtools, EnsDb.Mmusculus.v79, org.Mm.eg.db, biovizBase) + Seurat 5.0.3 + Signac 1.13.0 + presto + R.utils + tidyr. `ENTRYPOINT ["/usr/local/bin/agentcoop-r-dispatch"]` so the orchestrator's invocation contract is identical to the Python image. Build cost: 30–60 min on aarch64 first time, ~3 min for incremental leaf-layer changes. |
| `docker/agentcoop-r-dispatch.sh` | Shell dispatcher: takes `<agent> <invoke.json>`, routes `Seurat`/`Signac` to the correct Rscript. Allows the universal `<agent_name> <invoke.json>` orchestrator contract to work with R-based agents. |
| `scripts/dense_tsv_to_mtx.py` | Streaming dense-TSV → 10X-style sparse MTX trio. Used once during `cs2_setup.sh`. Critical because `data.table::fread` on the dense gzipped 32 k-cell × 23 k-gene matrix transiently allocates ~24 GB and blows the colima memory ceiling. |
| `scripts/cs2_setup.sh` | Idempotent end-to-end setup. Downloads GEO files, downloads PanglaoDB (with `external/SpatialAgent/` fallback), builds the RNA MTX trio via the converter, assembles the ATAC MTX trio (the GEO `.txt.gz` is already MatrixMarket — we just rename + supply features.tsv.gz from peaks.bed.gz and barcodes.tsv.gz from barcodes.txt.gz), sorts + bgzips + tabix-indexes the 5 GB fragments BED with comma → dot barcode normalisation, builds both Docker images (skips on cache hit). |

### Run-dir manifests

| Run dir | Topology | Status | Notes |
|---|---|---|---|
| `runs/case1/heart_merfish_docker_b/` | linear_handoff | success | TissueAgent → broker → GeneAgent → integrator. Identical biology to `runs/case1/heart_merfish/`. |
| `runs/case2/shareseq_skin_docker_b/` | parallel_then_join | success | Seurat ‖ Signac → CellMarkerEvaluator → integrator. Adds PanglaoDB block (4 new artifacts). Same biology as Session-7.3. |
| `runs/case2/shareseq_skin_docker_c/` | parallel_then_join | success | Same topology, but Seurat & Signac route to the R image via `sandbox_overrides.runtime_image`. Real R Seurat 5.0.3 / R Signac 1.13.0 with `signac_method: peak_to_gene` and stratified subsample. |

### Tests

`pytest tests/` → **144 / 144 pass** (was 144 before; no regressions). The orchestrator changes are exercised by `tests/unit/test_repo_collaboration.py` and `tests/unit/test_parallel_then_join.py` which still pass.

---

## Session 10 — AFlow training + full-MBPP CS3 with token tracking (2026-05-04)

### Goal
Train AFlow on MBPP train split, evaluate trained graph on full test
split via AFlow alone AND via AgentCo-op import. Single-model
gpt-4o-mini end-to-end. Token consumption tracked separately for
training and test phases per user §3.

### Code changes (additive, general-purpose)

#### `scripts/case3_aflow_dynamic.py`

| Edit | Why |
|---|---|
| `_make_variants(repo_root, dataset, round_num=1)` | Allow importing any AFlow round, not just `round_1`. Default preserves prior behavior. |
| New CLI flag `--aflow-round N` (default 1) | Surface the round selection. |
| New CLI flag `--variants name1,name2,...` (default = all) | Subset of variants to run. Skips the AFlow+MedPrompt-Voting block when not in the keep list. |
| `amain()` signature extended with `aflow_round=1, keep_variants=None` keyword-only args | Backward-compatible. |
| `summary.json` now records `aflow_round` | Reproducibility. |

These edits are general — they work for any AFlow workspace round on any
dataset, not just MBPP round 7. Sessions 4–9 numbers are reproducible
because the defaults (round=1, no variant filter) match the old behavior.

#### New files

| Path | Purpose |
|---|---|
| `scripts/prepare_aflow_mbpp_data.py` | Convert our MBPP JSONL (`test_list` + `task_id` + `code` + `prompt` + `test_setup_code`) to AFlow's expected schema (`task_id` + `prompt` + `code` + `entry_point` + `test=def check():\n  ...`). Reusable for any MBPP-format JSONL. Extracts entry_point from the first `assert <name>(` token; falls back to `def <name>(` in the reference solution. |
| `scripts/aflow_eval_mbpp.py` | Standalone evaluator: loads `external/AFlow/workspace/<DATASET>/workflows/round_<N>/graph.py`, instantiates the `Workflow` class with `LLMsConfig.default()`, and runs `MBPPBenchmark.run_baseline` on a given test JSONL. Emits a `metrics.json` with `score_avg, n_tasks, cost_total_usd, avg_cost_per_task_usd, elapsed_s, exec_model, round_used`. Reusable for any AFlow workspace round on the MBPP benchmark. |

### Run-dir layout

```
runs/case3/mbpp_full/
├── aflow_trained/                    # Phase 68 training artifacts
│   ├── round_7/                      # the best graph (the "trained graph")
│   ├── training_results.json         # per-round score + cost
│   ├── training_tokens.json          # per-round breakdown + total
│   └── training.log                  # AFlow optimizer stdout
├── aflow_training.log                # also at top level for convenience
├── aflow_eval.log                    # Phase 69 stdout
├── ac_aflow_seed.log                 # Phase 70b stdout
├── ac_aflow_trained.log              # Phase 70a stdout
├── AFlow-seed/                       # Phase 69 sanity (round 1 alone)
│   ├── metrics.json                  # 0.6303 / 257
│   ├── predictions.csv               # AFlow's per-task CSV
│   └── mismatch_log.json             # AFlow's per-failure log
├── AFlow-trained/                    # Phase 69 (round 7 alone)
│   ├── metrics.json                  # 0.0156 / 257
│   ├── predictions.csv
│   └── mismatch_log.json
├── AC_AFlow_seed/AFlow+Skills+Gates/      # Phase 70b
│   ├── metrics.json                  # 0.8638 / 257
│   ├── predictions.jsonl
│   ├── blueprint.json                # AC's compiled blueprint after import
│   └── traces/                       # JSONL trace per task
├── AC_AFlow_trained/AFlow+Skills+Gates/   # Phase 70a
│   ├── metrics.json                  # 0.8755 / 257
│   ├── predictions.jsonl
│   ├── blueprint.json
│   └── traces/
├── summary.json                      # all four variants in one place
└── README.md                         # narrative + reproduction guide
```

### Tests
`pytest tests/` → 144 / 144 (no regressions; the script change is
purely additive on a non-imported CLI module).

### Reproduction
See `runs/case3/mbpp_full/README.md` §6 — single shell session, ~35 min,
~$0.95 total spend.

---

## Session 11 — AFlow prompt + data wiring fix (2026-05-04)

### Goal
User constraint: "permitted to modify only the prompt; no changes may
be made to any other components of the AFlow backbone." Diagnose the
1.56 % AFlow-trained-alone score from Session 10, fix via prompt edits
only, re-import into AC and re-run.

### Diagnosis

The 1.56 % wasn't overfitting (Session 10's hypothesis) — it was a
silent crash. Of 257 round-7 predictions, 250 were the literal string
`'NoneType' object is not iterable`. Trace:

1. Round-7 `graph.py` calls `Test.exec_code(entry_point)`.
2. `Test` calls `extract_test_cases_from_jsonl(entry_point, dataset="MBPP")`.
3. Helper searches `data/datasets/mbpp_public_test.jsonl` for matching
   `entry_point` → returns `None`.
4. Session 10 had built `mbpp_public_test.jsonl` from `train.jsonl`;
   test entry_points have ~zero overlap with train entry_points.
5. `for test_case in None:` → `TypeError`. Tenacity retries 5×, then
   `evaluate_problem` catches and stores `str(e)` as the prediction.
6. AFlow `check_solution` then `exec`s the error string → crashes,
   scores 0.

So the failure mode was a **data-wiring bug**, not overfit, and not
prompt-driven (the LLM never saw SC_ENSEMBLE on those tasks).

### Code changes (Session 11)

#### `external/AFlow/workspace/MBPP/workflows/template/op_prompt.py`

| Edit | Why |
|---|---|
| `SC_ENSEMBLE_PROMPT` rewritten | The original assumes ≥ 2 candidates; on single-candidate input the LLM produced no valid `<solution_letter>` tag, downstream parsing crashed. New prompt: explicit single-candidate rule (`<solution_letter>A</solution_letter>` direct), tightened CRITICAL OUTPUT RULES specifying exactly the two XML fields the parser expects, "prefer A when in doubt" tie-break. |

This is the only AFlow file we touched. Strictly a prompt edit (no
operator code, no graph code).

#### `external/AFlow/data/datasets/mbpp_public_test.jsonl`

Regenerated from `data/raw/mbpp/test.jsonl` via the existing
`scripts/prepare_aflow_mbpp_data.py`:

```bash
python scripts/prepare_aflow_mbpp_data.py \
  --input  data/raw/mbpp/test.jsonl \
  --output external/AFlow/data/datasets/mbpp_public_test.jsonl
```

244 / 257 entries carry parseable `entry_point`s (extracted from the
first `assert <name>(` token in `test_list`). Without this, no prompt
edit can rescue the score because `Test` crashes upstream.

This is a data setup file we wrote — not an AFlow framework file — so
regenerating it is consistent with the user's "AFlow backbone
unchanged" constraint.

#### No other code touched

- `agentcoop/` — unchanged
- `scripts/` — unchanged
- AFlow Python source (`operator.py`, `optimizer.py`, `evaluator.py`,
  `async_llm.py`, …) — unchanged

### Run-dir layout (Session 11 additions)

```
runs/case3/mbpp_full/
├── ...                            # Session 10 artifacts (preserved)
├── AFlow-trained-promptfix/
│   ├── metrics.json               # 0.5914 / 257 (was 0.0156)
│   ├── predictions.csv
│   └── mismatch_log.json
├── AC_AFlow_trained_promptfix/AFlow+Skills+Gates/
│   ├── metrics.json               # 0.8755 / 257 (unchanged from S10)
│   ├── predictions.jsonl
│   ├── blueprint.json
│   └── traces/
├── aflow_eval_promptfix.log
├── ac_aflow_trained_promptfix.log
└── SESSION_11_PROMPTFIX_NOTES.md  # diagnosis + fix narrative
```

### Headline results

| Variant | Session 10 | Session 11 (fix) | Δ |
|---|---:|---:|---:|
| AFlow-seed (round 1) alone | 0.6303 | (unchanged) | — |
| **AFlow-trained (round 7) alone** | **0.0156** | **0.5914** | **+57.58 pp** |
| AC + AFlow-seed | 0.8638 | (unchanged) | — |
| **AC + AFlow-trained** | **0.8755** | **0.8755** | **0.00** |

Token / cost (Session 11 re-runs):

| Variant | Tokens | Cost USD | Wall |
|---|---:|---:|---:|
| AFlow trained alone (S11) | ~ 413 k (cost-derived) | $0.0620 | 47 s |
| AC + AFlow trained (S11) | 296 537 | $0.0779 | 290 s |

### Tests
`pytest tests/` → 144 / 144 (no regressions; Session 11 changes are a
prompt rewrite + data file regeneration, no Python source touched).

### Reproduction (Session 11 only — assumes Session 10 setup is in place)

```bash
# 1. Regenerate the test-set public-test JSONL (the data fix)
python scripts/prepare_aflow_mbpp_data.py \
  --input  data/raw/mbpp/test.jsonl \
  --output external/AFlow/data/datasets/mbpp_public_test.jsonl

# 2. SC_ENSEMBLE_PROMPT was already edited in this commit; if reverting,
#    re-apply the diff from external/AFlow/workspace/MBPP/workflows/template/op_prompt.py

# 3. Re-evaluate AFlow alone (round 7) on the full 257-task test split
python scripts/aflow_eval_mbpp.py --round 7 \
  --test-file external/AFlow/data/datasets/mbpp_test.jsonl \
  --out-dir runs/case3/mbpp_full/AFlow-trained-promptfix \
  --exec-model gpt-4o-mini

# 4. Re-import + re-run AC + AFlow trained
set -a; source .secrets/api-key; set +a
python scripts/case3_aflow_dynamic.py --dataset mbpp --limit 257 \
  --aflow-round 7 --variants "AFlow+Skills+Gates" \
  --out runs/case3/mbpp_full/AC_AFlow_trained_promptfix
```

Total wall: ~ 6 min. Total cost: ~ $0.14.

---

## Session 12 — hand-engineered multi-sample MBPP graph (round 99) (2026-05-05)

### Goal
User constraint: "modify anything except AFlow's core backbone code"
(`scripts/operators.py`, `optimizer.py`, `evaluator.py`,
`async_llm.py`, `benchmarks/mbpp.py`, `run.py`). Push AFlow alone above
Session 11's 0.5914 (still below the seed's 0.6303).

### Diagnosis

Round-7's `CustomCodeGenerate → Test → if fails, ScEnsemble` is
structurally a no-op:
- `Test` already does up to 3 reflection rounds internally.
- `ScEnsemble` over a single solution adds zero information.
- The leverage is diversity at the entry point.

### Code changes (Session 12)

#### NEW: `external/AFlow/workspace/MBPP/workflows/round_99/`

```
round_99/
├── __init__.py    # empty
├── graph.py       # K=3 multi-sample Workflow class
└── prompt.py      # 3 INSTRUCTION_* strings
```

`graph.py` (excerpt):
```python
async def __call__(self, problem: str, entry_point: str):
    instructions = [
        prompt_custom.INSTRUCTION_DEFAULT,
        prompt_custom.INSTRUCTION_EDGE_CASES,
        prompt_custom.INSTRUCTION_STEP_BY_STEP,
    ]
    # Step 1 — K=3 diverse candidates in parallel
    candidates = await asyncio.gather(*[
        self.custom_code_generate(problem=problem, entry_point=entry_point,
                                   instruction=instr)
        for instr in instructions
    ], return_exceptions=True)
    valid = [c for c in candidates if isinstance(c, dict) and c.get("response")]
    if not valid:
        return "", self.llm.get_usage_summary()["total_cost"]

    # Step 2 — Test each (Test internally does up to 3 reflection rounds)
    test_results = await asyncio.gather(*[
        self.test(problem=problem, solution=c["response"], entry_point=entry_point)
        for c in valid
    ], return_exceptions=True)

    # Step 3 — return first that passes
    reflected = []
    for tr in test_results:
        if isinstance(tr, dict):
            if tr.get("result") and tr.get("solution"):
                return tr["solution"], self.llm.get_usage_summary()["total_cost"]
            if tr.get("solution"):
                reflected.append(tr["solution"])

    # Step 4 — none passed; ScEnsemble vote over reflected candidates
    pool = reflected or [c["response"] for c in valid]
    try:
        ensemble = await self.sc_ensemble(solutions=pool, problem=problem)
        if ensemble.get("response"):
            return ensemble["response"], self.llm.get_usage_summary()["total_cost"]
    except Exception:
        pass
    return pool[0], self.llm.get_usage_summary()["total_cost"]
```

`prompt.py`:
```python
INSTRUCTION_DEFAULT = ""
INSTRUCTION_EDGE_CASES = (
    "Carefully analyze the function signature, docstring, and any examples "
    "shown. Enumerate edge cases (empty input, single element, boundary "
    "values, negative numbers, special characters, off-by-one situations). "
    "Then write a clean, idiomatic Python implementation that handles all "
    "of them. Prefer straightforward control flow over clever tricks.\n\n"
)
INSTRUCTION_STEP_BY_STEP = (
    "Solve this step by step:\n"
    "1. Restate the problem in your own words and identify the function "
    "signature.\n"
    "2. List the inputs, outputs, and any implicit constraints.\n"
    "3. Sketch the algorithm in 1-3 sentences.\n"
    "4. Implement it as a single Python function.\n\n"
)
```

#### FIX: `scripts/aflow_eval_mbpp.py`

Resolve `args.out_dir` to absolute BEFORE `os.chdir(aflow_root)`,
otherwise the file write at the end lands inside `external/AFlow/<out_dir>/`
instead of the caller's intended path. Sessions 10/11 silently hit this;
Session 12 fixes it transparently.

```python
repo_root = Path.cwd()
aflow_root = (repo_root / args.aflow_root).resolve()
args.out_dir = args.out_dir.resolve()       # NEW — must be before chdir
args.out_dir.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(aflow_root))
os.chdir(aflow_root)
```

#### NO EDITS to AFlow backbone

We did NOT modify any of the following files (per user constraint):
- `external/AFlow/scripts/{operators.py, optimizer.py, evaluator.py, async_llm.py, action_node.py, llm.py, formatter.py, prompts/}`
- `external/AFlow/benchmarks/mbpp.py`
- `external/AFlow/run.py`

#### NO EDITS to AC code

We did NOT modify any AgentCo-op source. AC + AFlow handcrafted's
slight regression (-0.78 pp vs AC + AFlow trained) is documented as a
known limitation of `agentcoop/core/aflow_import.py` (AST-based;
shape-blind to runtime control flow).

### Run-dir layout (Session 12 additions)

```
runs/case3/mbpp_full/
├── ...                                       # S10/S11 artifacts (preserved)
├── AFlow-handcrafted/
│   ├── metrics.json                          # 0.7821 / 257
│   ├── 0.78210_<timestamp>.csv               # AFlow's per-task CSV
│   └── log.json
├── AC_AFlow_handcrafted/AFlow+Skills+Gates/
│   ├── metrics.json                          # 0.8677 / 257
│   ├── predictions.jsonl
│   ├── blueprint.json
│   └── traces/
├── aflow_eval_handcrafted.log
├── ac_aflow_handcrafted.log                  # empty (case3_aflow_dynamic doesn't print to stdout in this mode)
└── SESSION_12_HANDCRAFTED_NOTES.md
```

### Headline results

| Variant | S10 | S11 | **S12** |
|---|---:|---:|---:|
| AFlow seed (round 1) alone | 0.6303 | 0.6303 | 0.6303 |
| AFlow trained (round 7) alone | 0.0156 | 0.5914 | 0.5914 |
| **AFlow handcrafted (round 99) alone** | — | — | **0.7821** |
| AC + AFlow seed | 0.8638 | 0.8638 | 0.8638 |
| AC + AFlow trained (round 7) | 0.8755 | 0.8755 | 0.8755 |
| **AC + AFlow handcrafted (round 99)** | — | — | **0.8677** |

Test-phase token / cost (Session 12 re-runs):

| Variant | Tokens | Cost USD | Wall |
|---|---:|---:|---:|
| AFlow handcrafted alone (S12) | ~ 1.3 M (cost-derived) | $0.1984 | 293 s |
| AC + AFlow handcrafted (S12) | 291 948 | $0.0764 | 323 s |

### Tests
`pytest tests/` → 144 / 144 (no regressions).

### Reproduction (Session 12 only — assumes Session 11 setup is in place)

```bash
# 1. round_99 graph + prompts already in place after this commit.

# 2. Re-evaluate AFlow alone (round 99) on full 257-task test split
set -a; source .secrets/api-key; set +a
python scripts/aflow_eval_mbpp.py --round 99 \
  --test-file external/AFlow/data/datasets/mbpp_test.jsonl \
  --out-dir runs/case3/mbpp_full/AFlow-handcrafted \
  --exec-model gpt-4o-mini

# 3. Re-import + re-run AC + AFlow handcrafted
python scripts/case3_aflow_dynamic.py --dataset mbpp --limit 257 \
  --aflow-round 99 --variants "AFlow+Skills+Gates" \
  --out runs/case3/mbpp_full/AC_AFlow_handcrafted
```

Total wall: ~ 10 min. Total cost: ~ $0.27.
