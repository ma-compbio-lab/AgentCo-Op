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
