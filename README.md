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
- Failure classification and blame assignment.
- Deterministic patch policy plus patch application helpers.
- MCP server wrapper template for containerized tool/agent exposure.
- Prompt templates for LLM patch planning.
- A runnable minimal example and tests.

## Project layout

- `src/dynaforge/ir/schema.py`: Typed IR and patch-plan schema.
- `src/dynaforge/runtime/executor.py`: Workflow executor and repair loop.
- `src/dynaforge/runtime/blame.py`: Failure classification and blame assignment.
- `src/dynaforge/runtime/patching.py`: Deterministic patch policy and blueprint patch application.
- `src/dynaforge/integrations/mcp_wrapper.py`: FastMCP wrapper template.
- `templates/mcp/server.py`: Drop-in server template for containerized agents.
- `examples/minimal_blueprint.py`: Minimal end-to-end example.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python examples/minimal_blueprint.py
```

## Current scope and next refinements

This codebase intentionally implements a robust MVP with deterministic local execution primitives and clear extension seams. The main production refinements to add next are:

1. A live MCP client transport layer instead of deterministic placeholder tool invocation.
2. A real Repo2Run/Docker runner that materializes `SandboxSpec` into containers.
3. Live LLM-backed evaluator and patch-planner nodes instead of proxy scoring.
4. Richer schema validation and trace-assertion DSL execution.

