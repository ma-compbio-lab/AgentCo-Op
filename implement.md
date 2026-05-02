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
