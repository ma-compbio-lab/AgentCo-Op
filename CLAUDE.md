# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Research artifact for the AgentCo-Op paper: a **task-conditioned workflow compiler** for multi-agent
systems. Given a task, it profiles it, retrieves skills, compiles the minimum sufficient workflow
graph, executes it, and repairs only the implicated nodes when execution evidence indicates failure.
Everything is designed to run offline/deterministically by default, with live LLM calls as an opt-in.

## Commands

```bash
pip install -e .[dev]                 # core + pytest
pip install -e .[dev,bench,repo]      # + datasets/pandas + docker/gitpython

pytest                                # full suite (~150 tests, ~5s, fully offline)
pytest tests/unit/test_gates.py -v    # one file
pytest tests/unit/test_gates.py::test_name -v
pytest -k "gate and not extension"    # by expression
```

`asyncio_mode = "auto"` is set in [pyproject.toml](pyproject.toml) — async tests need no marker.
There is no linter/formatter configured; match surrounding style.

```bash
# Compile + run a blueprint (mock backends, no API key)
agentcoop compile --task "What is 13 * 17?" --out runs/demo/blueprint.json
agentcoop run --blueprint runs/demo/blueprint.json --out runs/demo

# Benchmarks: compile → execute → grade → write a reproducibility dir
agentcoop run-benchmark --dataset mbpp --limit 10 -v AC-Gated
agentcoop run-benchmark --dataset gsm8k --limit 5 --dry-run     # force offline
agentcoop evaluate --predictions <run>/predictions.jsonl --dataset mbpp   # re-grade only
agentcoop analyze-gates --runs runs/mbpp                        # gate rescue/harm rates

# External-repo collaboration (the case studies)
agentcoop collaborate --request docs/agents/case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish --no-docker

# Data (both dirs are gitignored; regenerate from scratch)
python scripts/download_datasets.py
python -m agentcoop.benchmarks.aflow_splits
```

Benchmark runs land in `runs/{dataset}/{method}/{timestamp}/` with `config.yaml`, `git_state.txt`,
`data_hashes.json`, `model_versions.json`, `workflow_blueprint.json`, `predictions.jsonl`,
`metrics.json`, `traces/`, `sandbox_logs/`, `artifacts/`.

## Architecture

There are **two independent execution paths**. They share the typed IR and the skill library but
nothing else — [repo_collaboration.py](agentcoop/core/repo_collaboration.py) deliberately does not
import the compiler or runtime, so changes to one path cannot break the other's results.

### Path A — compile & run (benchmarks, `compile`/`run`/`run-benchmark`)

```
raw task ─▶ profile_task ─▶ compile_workflow ─▶ run_blueprint ─▶ grade
           (profiler.py)     (compiler.py)       (runtime.py)     (graders.py)
                                  ▲                    │
                            SkillRegistry         evaluate_gates
                          (skills/registry.py)    → plan_patch → apply_patch
                                                     (gates.py)
```

- [core/schema.py](agentcoop/core/schema.py) is the single source of truth for the IR:
  `TaskProfile`, `NodeSpec`/`EdgeSpec`, `GatePolicy`, `WorkflowBlueprint`, `NodeResult`, `RunState`.
  These use `extra="forbid"` — adding a field to a serialized blueprint without updating the model
  will fail validation.
- [core/profiler.py](agentcoop/core/profiler.py) is regex-rule-based (deterministic), with an
  optional LLM layer. It never persists chain-of-thought — only a one-paragraph `rationale_summary`.
- [core/compiler.py](agentcoop/core/compiler.py) **does not search**. It instantiates each candidate
  meta-skill's `topology_template`, binds agent-skills, filters on hard constraints
  (`_satisfies_hard_constraints`) and capability coverage, then maximizes
  `coverage + evidence + verification − complexity − cost − risk` with an explicit simplicity bias
  toward the L0–L7 topology ladder (`_DIFFICULTY_TARGET_LEVEL`). Falls back to a two-node
  solver→formatter graph when no candidate qualifies.
- [core/runtime.py](agentcoop/core/runtime.py) walks the DAG with networkx, rebuilding it each outer
  loop because patches mutate the blueprint in place. Cycles are handled by detecting back-edges and
  treating them as optional. Retries are consumed via `state.node_retries`, never by removing a node
  from the `executed` set. **Ready nodes run sequentially**, even for parallel topologies.
- [core/gates.py](agentcoop/core/gates.py) — `_match_trigger` is one long dispatch over ~35 trigger
  names (schema_invalid, test_failure, low_confidence, budget_near_limit, plus per-case-study
  triggers like `gene_mapping_low`, `gene_universe_mismatch`). Each fired gate maps to one of the
  bounded `GraphPatchOp`s. Repair is always *local* and capped by `GateLimits`
  (`max_total_activations`, `max_same_node_repairs`, `max_graph_patches`).
- [backends/](agentcoop/backends/) — every backend implements the same
  `async execute(node, payload, ctx) -> NodeResult`. `default_registry()` wires llm / mcp /
  python_sandbox / sandbox_repo / human_review. The runtime never sees a concrete SDK.
  [backends/prompts.py](agentcoop/backends/prompts.py) holds all role × dataset prompt construction
  and output parsing (boxed answers, code fences, JSON); dataset-specific answer extraction belongs
  there, not in the backends.

### Path B — external-repo collaboration (`collaborate`)

Driven by a request YAML (`repositories`, `dataset`, `task`, `topology`, `required_outputs`).
[RepoCollaborationOrchestrator.run()](agentcoop/core/repo_collaboration.py) executes fixed stages:

1. `profile_repo` → `manifests/repo_profile_<name>.json`
2. `SandboxBuilder` → Dockerfiles + `manifests/docker_build_report.json`
3. `AgentRegistry` of `AgentCard`s → `agent_registry.json`
4. `EnvManager` auto-installs declared deps → `manifests/env_manifest.json`
5. deterministic topology compile → `compiled_workflow_graph.json` + `topology.png/.dot`
6. adapter execution per repo (Docker, or local Python under `--no-docker`)
7. `ArtifactBroker` validates every typed handoff → `manifests/broker.jsonl`
8. LLM integrator → `final_hypothesis_report.md` + `run_manifest.json`

Two topologies exist: `linear_handoff` (default) and `parallel_then_join` (fan-out branches plus a
`join_agent`). Adding a third means a new `_execute_*` method plus a `_compile_graph_*` counterpart.

Everything repo-specific lives in [agentcoop/wrappers/](agentcoop/wrappers/) — each wrapper ships
`manifest.yaml` (typed agent card), `Dockerfile.agentcoop`, and `adapter.py`. Adapters register
themselves via the `@register_local_adapter("<name>")` decorator, and
[wrappers/__init__.py](agentcoop/wrappers/__init__.py) imports each subpackage for that side effect —
**a new wrapper is invisible until it is added to that import list**.

See [docs/external_agent_collaboration.md](docs/external_agent_collaboration.md) for the full design.

## Skills

Two kinds, loaded by [SkillRegistry.load_dir](agentcoop/skills/registry.py):

- **Meta-skills** — `agentcoop/skills/meta/*.md`: YAML frontmatter (`complexity_level`,
  `task_signals`, `topology_template`, `gates`, `evidence_refs`) plus a body with
  `# Intent` / `# When to use` / `# When not to use` sections that are parsed into the model.
  A meta-skill is a compilation rule: it *is* the topology template the compiler instantiates.
- **Agent-skills** — `agentcoop/skills/agents/*.yaml` and flat `configs/skills/*.yaml`: capability
  contracts bound to nodes by backend match + tag overlap with `profile.domain`. Both single-mapping
  and `{skills: [...]}` bundle layouts are accepted.

To make the compiler produce a new topology, add a meta-skill markdown file — no compiler change is
usually needed.

## Configuration

- `configs/benchmarks/*.yaml` — per-dataset; `extends: _base` is resolved by `load_config` with a
  deep merge. Defines budget, model provider, gates, and the `variants` table.
- `configs/ablations/*.yaml` — documentation-only records of the 2×2 factorial; the real switches
  live in the `variants` block of `_base.yaml`.
- `configs/external_commits.yaml` — pinned SHAs for cloned external repos (several still `TODO_PIN`).

## Gotchas

- **Dry-run is the default whenever no API key is set.** `run_benchmark` then uses `MockLLM` with a
  deliberately empty canned answer, so scores are near zero by design — a low dry-run score is not a
  regression. Set `OPENAI_API_KEY` for real numbers; only the `openai` provider is wired
  (`_build_llm_client` raises `NotImplementedError` otherwise).
- **Most variant knobs in the configs are declarative only.** `_apply_variant` in
  [benchmarks/runner.py](agentcoop/benchmarks/runner.py) implements just `disable_gates` and
  `disable_reviewer`; `force_topology_level` only appends a provenance string, and
  `disable_python_sandbox` / `source_graph` / `augment_skills` / `retrieval_bias` have no effect
  there (Case Study 3 applies its equivalents itself in
  [scripts/case3_aflow_dynamic.py](scripts/case3_aflow_dynamic.py) via `core/augment_graph.py`).
- `collaborate` defaults to `--no-docker`; `--docker` needs a running daemon and built images
  (`scripts/build_repo_images.sh`, `docker/*.Dockerfile`).
- `data/`, `runs/` (except curated case-study READMEs/summaries), `external/`, and `docs/agents/` are
  gitignored. `docs/agents/*.request.yaml` are referenced by the README but not tracked — they exist
  only in a local working copy.
- Env vars: `AGENTCOOP_MODE=dry_run`, `AGENTCOOP_REPO_COLLAB_MODEL`, `AGENTCOOP_REPO_COLLAB_EFFORT`,
  `AGENTCOOP_BRANCH_CONCURRENCY`, `AGENTCOOP_DOCKER_RUN_TIMEOUT_S`, `AGENTCOOP_RUN_ID`.
- Node failures inside the runtime are captured as a failed `NodeResult` rather than raised, unless
  `RuntimeConfig(raise_on_node_error=True)`. Tests that assert on exceptions must set it.
