# External-agent collaboration in AgentCo-Op

This document explains the **current form and mechanism** of the
external-repo collaboration capability added in Sessions 7 / 7.1 / 7.2.
It is the design + execution reference for the
`agentcoop collaborate --request <yaml>` CLI introduced in
`agentcoop/cli.py` and exercised end-to-end by Case Study 1
(TissueAgent × GeneAgent).

The pattern is **generic** — any two GitHub repositories + a task
description can be composed through the same pipeline. The only
case-study-specific code lives under `agentcoop/wrappers/<agent>/`.

## What "external-agent collaboration" means here

> Given **two GitHub repository URLs** and a **natural-language task**,
> AgentCo-Op autonomously profiles each repo, prepares an isolated
> execution environment per repo, registers each repo as an agent, runs
> them in upstream/downstream order, validates the typed handoff
> between them, integrates their outputs with an LLM-backed integrator,
> and emits a structured, auditable report.

The collaboration is **not** a generic chat-orchestration loop. It is a
**compiled L7 topology** (per `architecture.md` §4.3
`sandbox_repo_execution`) with eight named stages, deterministic
edges, and one optional LLM stage at the end.

## High-level shape

```
              ┌──────────────────────────────┐
              │     CollaborationRequest     │
              │  (case_study_*.request.yaml) │
              └──────────────┬───────────────┘
                             │ load
                             ▼
   ┌────────────────────────────────────────────────────┐
   │ RepoCollaborationOrchestrator (core/repo_collaboration.py) │
   └────────────────────────────────────────────────────┘
       1. RepoProfiler        → manifests/repo_profile_*.json
       2. SandboxBuilder      → docker/Dockerfile.*, docker-compose.yml,
                                manifests/docker_build_report.json
       3. AgentRegistry       → agent_registry.json
       4. EnvManager (auto)   → manifests/env_manifest.json
       5. Topology compiler   → compiled_workflow_graph.json,
                                topology.png, topology.dot
       6. Upstream adapter    → artifacts/<upstream>_run/*
       7. ArtifactBroker      → manifests/broker.jsonl,
                                artifacts/<downstream>_input.json
       8. Downstream adapter  → artifacts/<downstream>_run/*
       9. LLM integrator      → artifacts/integration/*
      10. Reporter            → run_manifest.json,
                                collaboration_log.md, final_report.md,
                                traces/execution_trace.jsonl
```

The pipeline is deterministic except for stages 6/8/9, which delegate
to whatever adapter is registered for each agent. Currently:

- **Local-Python adapter** (default `--no-docker`): the wrapper module
  registers a callable through
  `agentcoop.core.repo_collaboration.register_local_adapter("Name")`.
  The orchestrator invokes it directly in the host process. Useful
  when Docker is unavailable or the wrapper is a thin Python shim.
- **Docker adapter** (`--docker`): the orchestrator runs
  `docker exec <container> python /workspace/wrappers/<name>_worker.py
  --invoke-json '<json>'` and parses the response from stdout.

## Stage-by-stage mechanism

### 1. RepoProfiler (`agentcoop/core/repo_profile.py`)

Clones the repository into `external/<RepoName>/` (shallow,
`--recurse-submodules`), then inspects:

- environment files (`environment.yml`, `pyproject.toml`, `uv.lock`,
  `requirements.txt`, `flake.nix`, `Dockerfile`, `Makefile`);
- README clues for run modes (FastAPI, Streamlit, papermill, headless,
  notebook);
- entry-script names (`main.py`, `run.py`, `worker.py`, etc.);
- API-key envs by regex over Python files (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, etc.);
- candidate capabilities by keyword search over README + env-file text
  (e.g. "differential expression", "gene set", "spatial transcriptomics").

Output: a typed `RepoProfile` Pydantic object plus a JSON dump per repo
under `manifests/repo_profile_<name>.json`.

### 2. SandboxBuilder (`agentcoop/core/sandbox_build.py`)

Picks an installer strategy from the profile (`conda` / `uv` / `pip` /
`nix`), then renders a Dockerfile with the right base image
(`mambaorg/micromamba:1.5.8` for conda, `python:3.11-slim` for uv/pip),
the right install block, and the right CMD. It also writes a per-image
smoke test and a `docker-compose.yml` that mounts `data`, `artifacts`,
`traces`, and `wrappers` directories.

The default is `dry_run=True` (write the spec without invoking
`docker build`) so the case study runs on hosts without a Docker
daemon. Set up the daemon and pass `dry_run=False` (or use the
`--docker` CLI mode) to actually build and smoke-test the images.

### 3. AgentRegistry (`agentcoop/core/agent_card.py`)

Builds one `AgentCard` per repo by combining the `RepoProfile` with
the `role_hint` field from the request YAML. Each card carries:

- `name`, `repository`, `container`;
- `role`, `capabilities[]`;
- `input_schema`, `output_schema` (default schemas in
  `agent_card._default_input_schema` / `_default_output_schema`; users
  can override per agent in YAML);
- the matching `SandboxSpec`.

The cards are written to `agent_registry.json` and consumed by the
runtime when invoking each agent.

### 4. EnvManager (`agentcoop/core/env_manager.py`) — **auto preparation**

Each wrapper module declares its required PyPI packages at import time
via `register_required_packages("<AgentName>", [...])`. Before any
adapter runs, the orchestrator calls `ensure_env_for_agents(...)`
which:

- introspects the current Python interpreter for each declared package
  (using `importlib.util.find_spec`);
- runs `python -m pip install --quiet <pkg>` for anything missing;
- records every package's state (`already_present` / `installed` /
  `install_failed`) in `manifests/env_manifest.json`.

In `--docker` mode the EnvManager writes a manifest but does not
install — the SandboxBuilder weaves the same package list into each
Dockerfile via `extra_pip_packages`.

This is what the user means by "all preparatory work — including
environment configuration — should be handled entirely internally
within agentco-op": the user does **not** need to `pip install`
anything before `agentcoop collaborate`, and they do not need to
maintain per-agent shell scripts.

### 5. Topology compiler (`agentcoop/core/repo_collaboration.py::_compile_graph`)

Builds a deterministic L7 graph (no LLM call) describing the
upstream → broker → downstream → integrator → reporter flow plus the
internal AgentCo-Op stages (repo_profiler, sandbox_builder,
agent_registry). The result is dumped as
`compiled_workflow_graph.json`.

`agentcoop/core/topology_viz.py` then renders both `topology.png`
(matplotlib + networkx, hierarchical layout, color-by-kind) and
`topology.dot` (Graphviz source). Either artifact is suitable for
embedding in a paper / report.

### 6. Upstream adapter

The orchestrator invokes the upstream agent's adapter (local or
Docker) with a typed request:

```json
{
  "task_id": "<case_id>_<agent_lower>",
  "capability": "run_primary_analysis",
  "input": {
    "task_description": "...",
    "task_meta": {...},
    "dataset": {...},
    "success_targets": {...}
  },
  "output_dir": "<workdir>/artifacts/<agent_lower>_run"
}
```

The adapter writes its files under `output_dir` and returns a JSON
response shaped per the `agent_card`'s `output_schema`. The standard
fields the orchestrator depends on are `status`, `summary`, `artifacts`
(map of name → path or in-memory value), and an optional
`main_results` block.

For CS1 the upstream is `TissueAgent`'s local adapter
(`agentcoop/wrappers/tissueagent/adapter.py`): load the `overall_merfish.h5ad`,
discover labels, build target/control masks for `aFibro` × `AVN/AV
Ring` vs `Left + Right Atria`, run a Welch t-test (and Mann-Whitney U
sensitivity panel) with Benjamini-Hochberg FDR, write a DE table,
marker CSV/JSON, volcano PNG, and a coding report.

### 7. ArtifactBroker (`agentcoop/core/artifact_broker.py`)

Validates the typed handoff. For CS1 the upstream output includes a
flat `gene_set` list; the broker:

- normalises gene symbols (uppercase for human; preserve order; drop
  invalid symbols and duplicates);
- writes a JSON file consumable by the downstream agent;
- additionally validates a CSV handoff (`marker_genes_csv`) when
  present;
- appends a JSONL audit record to `manifests/broker.jsonl`.

The broker prevents the kind of free-text drift that would happen if
the integrator were just shown both agents' Markdown outputs and asked
to reconcile them.

### 8. Downstream adapter

Same invocation contract as stage 6. For CS1 the downstream is
GeneAgent's local adapter (`agentcoop/wrappers/geneagent_local/adapter.py`):
calls the OpenAI Chat Completions endpoint with a JSON-mode prompt
shaped per `case_study_1.md` §11.4 to produce a structured gene-set
interpretation report (process label, subprocesses, supporting genes,
self-verification, caveats, final interpretation).

### 9. LLM integrator (`_integrate`)

Single OpenAI call (default `gpt-5`, `reasoning_effort=medium`) that
synthesises both agent responses into one Markdown hypothesis report
plus a JSON sidecar. The integrator's prompt explicitly asks for an
artifact-listed, evidence-linked, caveat-noted report.

### 10. Reporter (`agentcoop/core/collab_report.py`)

Writes three structured outputs:

- `run_manifest.json` — top-level outcome + every artifact path +
  pointers to the structured outputs;
- `collaboration_log.md` — per-stage narrative with status, key
  artifacts, package install table, broker handoff table, integrator
  meta;
- `final_report.md` — single self-contained Markdown report. Embeds
  the topology PNG (relative path), executive summary, per-stage
  table, GeneAgent's biology report verbatim, the integrator's
  hypothesis report verbatim, every artifact path, and a reproduce
  block.

The reporter is what makes the run **structured and standalone**: a
reviewer can open `final_report.md` and see everything that happened
without crawling JSON files.

## File map of one run

```
<workdir>/
  run_manifest.json                 ← top-level outcome (JSON)
  collaboration_log.md              ← per-stage narrative
  final_report.md                   ← standalone, embeds topology + reports
  topology.png                      ← matplotlib hierarchical layout
  topology.dot                      ← Graphviz source (`dot -Tpng topology.dot ...`)
  compiled_workflow_graph.json      ← deterministic L7 graph
  agent_registry.json               ← AgentCards per repo
  manifests/
    repo_profile_<Repo>.json
    docker_build_report.json
    env_manifest.json               ← auto-resolved Python packages
    broker.jsonl                    ← typed handoff audit log
  docker/
    Dockerfile.<Repo>
    docker-compose.yml
    smoke_<Repo>.py
  artifacts/
    <upstream>_run/
      response.json
      <repo-specific files (DE table, plots, ...)>
    <downstream>_input.json         ← broker payload
    <downstream>_run/
      response.json
      <repo-specific files (GeneAgent reports, ...)>
    integration/
      final_hypothesis_report.md    ← LLM integrator
      final_hypothesis_report.json
  traces/
    execution_trace.jsonl           ← per-stage event log
```

## Generalising to other agent pairs

To collaborate two arbitrary GitHub agents:

1. Write a `<my_case>.request.yaml` matching `case_study_1.md` §3.3 —
   the only required fields are `case_id`, `repositories[]` with
   `{name,url,role_hint}`, and `task.description`. Optional:
   `dataset`, `success_targets`, `required_outputs`,
   `sandbox_overrides` per repo.
2. Add an adapter under `agentcoop/wrappers/<agent>/`:
   - `__init__.py` that calls `register_required_packages` and
     `register()`.
   - `adapter.py` that implements an
     `invoke_<agent>_local(req: dict) -> dict` callable returning the
     `{status, summary, artifacts, main_results}` shape.
   - `manifest.yaml` that mirrors `case_study_1.md` §4.3.
   - `Dockerfile.agentcoop` (when shipping a container image).
3. Run `agentcoop collaborate --request my_case.request.yaml
   --workdir runs/my_case --no-docker --model gpt-5
   --reasoning-effort medium`.

The framework code (`agentcoop/core/repo_profile.py`, `sandbox_build.py`,
`agent_card.py`, `artifact_broker.py`, `repo_collaboration.py`,
`env_manager.py`, `topology_viz.py`, `collab_report.py`,
`agentcoop/skills/meta/external_repo_collaboration.md`,
`agentcoop/cli.py::collaborate`) does not mention TissueAgent or
GeneAgent — those names appear only inside `agentcoop/wrappers/`.

## What's NOT in scope (yet)

- Live image build + smoke test inside the orchestrator runs with
  `--docker`; the building works once the daemon is up but the
  default `--no-docker` mode is what CS1 currently uses.
- Multi-agent topologies beyond two agents (the broker / integrator
  pattern would need extending; the L7 meta-skill template would
  remain the same).
- Automated retries when an adapter fails — adapter exceptions are
  surfaced in the response's `warnings` field but not retried.

These are deliberate scope choices for the inaugural framework; each
is a small addition once needed.
