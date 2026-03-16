# DynaForge

DynaForge is a Python implementation of a dynamic-by-construction workflow compiler/runtime for open-world agentic research automation. It is organized around four core ideas:

- Dynamic topology is a first-class concept through `WorkflowBlueprint = base graph + gated subgraphs`.
- Metric bootstrapping is explicit through hard checks, soft judges, and trace assertions.
- Containerized agent reuse is standardized through `SandboxSpec` and `MCPServerRef`.
- Causal-ish credit assignment is handled through execution traces, contract violations, hard-check classification, and deterministic patch ops.

## What is implemented

- Typed workflow IR with strict Pydantic validation.
- Execution report models, budget accounting, and content-addressable artifact storage.
- Blueprint executor with gating, caching, hard-check execution, soft-judge proxies, and repair-loop support.
- Live default node execution through an OpenAI-compatible LLM router for agent/evaluator/router nodes.
- Reusable agent-skill support for LLM-backed nodes via `SKILL.md` loading and prompt injection.
- Real MCP client calls for tool nodes, with SDK-backed stdio/streamable-http/SSE transports.
- Registry-level tool discovery for planner/router/agent nodes via `ToolRegistry` and `ToolScout`.
- Builtin web-search support exposed as an MCP server for discovery-enabled nodes.
- Docker CLI-backed sandbox materialization with Repo2Run build hooks and containerized hard-check execution.
- Failure classification and blame assignment.
- Deterministic patch policy plus patch application helpers.
- Hydra + YAML-based model/experiment configuration and a runnable CLI entrypoint.
- Benchmark preparation helpers for MedQA, MATH, and HumanEval.
- MCP server wrapper template for containerized tool/agent exposure.
- Prompt templates for LLM patch planning.
- A runnable minimal example and tests.

## Project layout

- `src/dynaforge/ir/schema.py`: Typed IR and patch-plan schema.
- `src/dynaforge/runtime/executor.py`: Workflow executor and repair loop.
- `src/dynaforge/runtime/llm.py`: Live LLM router and OpenAI-compatible client.
- `src/dynaforge/runtime/skills.py`: `SKILL.md` registry, parsing, and prompt bundle construction.
- `src/dynaforge/runtime/tool_scout.py`: Deterministic candidate-tool ranking for discovery-enabled nodes.
- `src/dynaforge/runtime/validation.py`: JSON Schema validation helpers.
- `src/dynaforge/runtime/expression.py`: Constrained expression evaluator for invariants and trace checks.
- `src/dynaforge/runtime/blame.py`: Failure classification and blame assignment.
- `src/dynaforge/runtime/patching.py`: Deterministic patch policy and blueprint patch application.
- `src/dynaforge/integrations/mcp_client.py`: Real MCP client manager.
- `src/dynaforge/integrations/tool_registry.py`: Registry-wide MCP tool enumeration across blueprint servers.
- `src/dynaforge/integrations/web_search.py`: Builtin web-search adapter and MCP server helper.
- `src/dynaforge/integrations/web_search_server.py`: Builtin FastMCP web-search server.
- `src/dynaforge/integrations/sandbox.py`: Docker/Repo2Run-backed sandbox runner.
- `src/dynaforge/config.py`: Hydra config loading and experiment execution helpers.
- `src/dynaforge/benchmarks.py`: Benchmark-specific preparation and validation helpers.
- `src/dynaforge/cli.py`: CLI entrypoint for composed Hydra runs.
- `src/dynaforge/conf/`: Default YAML config groups for models and experiments.
- `src/dynaforge/integrations/mcp_wrapper.py`: FastMCP wrapper template.
- `templates/mcp/server.py`: Drop-in server template for containerized agents.
- `examples/minimal_blueprint.py`: Minimal end-to-end example.
- `examples/hydra_experiment.py`: Hydra-configured example run.
- `scripts/prepare_medqa.py`: Download a local MedQA preview and metadata cache.
- `scripts/prepare_math.py`: Download and normalize the AFlow-aligned MATH benchmark slice.
- `scripts/prepare_humaneval.py`: Download and normalize the AFlow-aligned HumanEval benchmark split.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python examples/minimal_blueprint.py
python -m dynaforge.cli
```

## Hydra configuration

The repository now supports Hydra-composed YAML configuration for both model selection and experiment setup.

- Default configs live under `src/dynaforge/conf/`.
- Model groups live under `src/dynaforge/conf/model/`.
- Experiment groups live under `src/dynaforge/conf/experiment/`.
- You can run with overrides through the CLI, for example:

```bash
python -m dynaforge.cli model=openai_gpt41
python -m dynaforge.cli experiment=minimal_repair executor.repair_enabled=true
dynaforge-run model=local_openai_compat
```

The composed config is converted into a `WorkflowBlueprint` via `build_blueprint_from_config()` and executed via `run_configured_experiment()`.

Tool discovery is node-level and opt-in. A node can keep explicit `tools`, or it can enable `tool_discovery` to search the blueprint MCP registry and optionally include the builtin web-search server. A minimal discovery-oriented config is available via `experiment=minimal_tool_discovery`. The builtin web-search MCP server requires the `dynaforge[mcp]` extra at runtime.

Agent skills are also node-level and opt-in. LLM-backed nodes can declare `skills` that point to named or explicit-path `SKILL.md` files. The runtime resolves those skills from, in order:

- `executor.skill_search_paths` or `DYNAFORGE_SKILL_PATHS`
- `<project-root>/skills`
- packaged `src/dynaforge/skills`
- `~/.codex/skills`
- `~/.agents/skills`

For exact control, prefer an explicit `path`. A minimal packaged example is available via `experiment=minimal_skills`.

If a resolved skill declares `allowed-tools` metadata, that metadata is now enforced as a runtime allowlist over the node's candidate tools. The enforcement is applied before tool selection, shows up in the node trace under `tool_discovery.skill_tool_policy`, and does not affect nodes that do not declare constrained skills.

## Benchmark setup

The repository now includes reproducible helpers for the active benchmark tracks.

MedQA:

```bash
pip install -e ".[benchmarks]"
.venv/bin/python scripts/prepare_medqa.py
.venv/bin/python -m dynaforge.experiment_cli medqa --model openai_gpt4o_mini
```

- Uses `bigbio/med_qa`
- Requires `datasets<4`
- Uses `trust_remote_code=True`
- Uses `gpt-4o-mini` as the benchmark-aligned base execution model
- Writes local artifacts under `benchmarks/medqa/`
- Restores both:
  - canonical benchmark eval cache under `benchmarks/medqa/processed_test/`
  - full split assets under `benchmarks/medqa/all_splits/`
- Exports a `train_sample_records.jsonl` sample of `1000` train examples

MATH:

```bash
.venv/bin/python scripts/prepare_math.py
.venv/bin/python -m dynaforge.experiment_cli math --subset validation --model openai_gpt4o_mini
```

- Uses `EleutherAI/hendrycks_math`
- Defaults to the official AFlow packaged public split
- Keeps the AFlow public MATH setting:
  four subject areas, level-5 filter, `119 validation / 486 test`
- Writes:
  - canonical AFlow-aligned eval assets under `benchmarks/math/processed/`
  - upstream selected-config full splits under `benchmarks/math/upstream_full/`
- Exports a `train_sample_records.jsonl` sample of `1000` train examples from the AFlow-aligned subject families

HumanEval:

```bash
.venv/bin/python scripts/prepare_humaneval.py
.venv/bin/python -m dynaforge.experiment_cli humaneval --subset validation --model openai_gpt4o_mini
```

- Uses `openai/openai_humaneval`
- Defaults to the official AFlow packaged public split
- Keeps the AFlow public HumanEval setting:
  full public set, `33 validation / 131 test`
- Writes:
  - canonical AFlow-aligned eval assets under `benchmarks/humaneval/processed/`
  - upstream official full splits under `benchmarks/humaneval/upstream_full/`
- The current upstream official dataset exposes only `test`, so the train-sample artifact is empty by design

## Current scope and next refinements

This codebase now has real integration seams rather than only simulation. The main production refinements to add next are:

1. Add richer model-provider adapters beyond the current OpenAI-compatible chat client.
2. Add lifecycle policies for sandbox cleanup/reuse across long-running evaluation batches.
3. Upgrade soft judges and patch planning to use live LLM scoring instead of proxy scoring.
4. Add richer MCP session pooling and retry logic for production-scale workloads.
