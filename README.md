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
- Real MCP client calls for tool nodes, with SDK-backed stdio/streamable-http/SSE transports.
- Docker CLI-backed sandbox materialization with Repo2Run build hooks and containerized hard-check execution.
- Failure classification and blame assignment.
- Deterministic patch policy plus patch application helpers.
- Hydra + YAML-based model/experiment configuration and a runnable CLI entrypoint.
- Benchmark preparation helpers for MedQA and SpatialBench.
- MCP server wrapper template for containerized tool/agent exposure.
- Prompt templates for LLM patch planning.
- A runnable minimal example and tests.

## Project layout

- `src/dynaforge/ir/schema.py`: Typed IR and patch-plan schema.
- `src/dynaforge/runtime/executor.py`: Workflow executor and repair loop.
- `src/dynaforge/runtime/llm.py`: Live LLM router and OpenAI-compatible client.
- `src/dynaforge/runtime/validation.py`: JSON Schema validation helpers.
- `src/dynaforge/runtime/expression.py`: Constrained expression evaluator for invariants and trace checks.
- `src/dynaforge/runtime/blame.py`: Failure classification and blame assignment.
- `src/dynaforge/runtime/patching.py`: Deterministic patch policy and blueprint patch application.
- `src/dynaforge/integrations/mcp_client.py`: Real MCP client manager.
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
- `scripts/setup_spatialbench.py`: Clone/install/validate SpatialBench in a dedicated environment.

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

## Benchmark setup

The repository now includes reproducible helpers for the two requested benchmarks.

MedQA:

```bash
pip install -e ".[benchmarks]"
.venv/bin/python scripts/prepare_medqa.py
.venv/bin/python -m dynaforge.cli experiment=medqa
```

- Uses `bigbio/med_qa`
- Requires `datasets<4`
- Uses `trust_remote_code=True`
- Writes local artifacts under `benchmarks/medqa/`

SpatialBench:

```bash
.venv/bin/python scripts/setup_spatialbench.py
.venv/bin/python -m dynaforge.cli experiment=spatialbench
```

- Clones `https://github.com/latchbio/spatialbench` into `external/spatialbench`
- Creates a dedicated environment at `external/spatialbench/.venv`
- Installs `spatialbench` editable and runs the canonical `validate` smoke test
- Writes local validation output under `benchmarks/spatialbench/`

## Current scope and next refinements

This codebase now has real integration seams rather than only simulation. The main production refinements to add next are:

1. Add richer model-provider adapters beyond the current OpenAI-compatible chat client.
2. Add lifecycle policies for sandbox cleanup/reuse across long-running evaluation batches.
3. Upgrade soft judges and patch planning to use live LLM scoring instead of proxy scoring.
4. Add richer MCP session pooling and retry logic for production-scale workloads.
