# Agent-Cop Implementation Notes

## Purpose
This repo implements a simplified multi-agent workflow with three core modules:
Orchestrator (plan/route/assign), Execution Engine (protocol-driven graph runner),
and Judge (verify/repair/aggregate). It avoids LangGraph and uses a LatentMAS-like
layout with `run.py`, `models.py`, `methods/`, and core services.

## Architecture
- Core modules:
  - Orchestrator: builds an `ExecutionPlan` from `TaskSpec`.
  - Execution Engine: runs a protocol over agents with hooks and tracing.
  - Judge: verifies outputs and aggregates a final response.
- Cross-cutting services:
  - Registry: agent capability catalog used for routing.
  - BudgetRouter: simple model budget decision.
  - Hooks: pre/post plan/step/judge/output intervention points.
  - Observability: JSONL trace logging.
  - Memory/Safety: minimal stubs, ready for extension.

## Repo Layout
- `run.py`: CLI entrypoint.
- `config.py`: structured configs for Hydra/OmegaConf.
- `models.py`: model backends with caching and token estimates.
- `prompts.py`: prompt construction helpers.
- `utils.py`: JSONL logging, token estimation, time helpers.
- `core/`: contracts, registry, orchestrator, hooks, judge, safety, budget, memory.
- `engine/`: protocols and executor.
- `engine/tool_runtime.py`: tool execution with allowlist and hooks.
- `agents/`: base agent class, reasoning agents, IO agent, factory.
- `methods/`: baseline, sequential, orchestrated method implementations.
- `eval/`: harness and metrics for batch evaluation.
- `conf/`: Hydra YAML configs for single runs and eval.

## Data Contracts (core/contracts.py)
- TaskSpec: user goal, constraints, criteria, budgets, modalities.
- AgentSpec: capabilities and IO metadata.
- Message: standardized inter-agent envelope.
- ExecutionPlan/SubTask: protocol + task DAG + constraints + model_hint/meta.
- JudgeReport: pass/fail, score, issues, optional patch.
- TraceEvent: event stream for observability.

## Execution Flow
1. CLI builds TaskSpec and runtime services.
2. Orchestrator selects candidate agents, protocol, and subtasks (planner -> worker when available).
3. Execution Engine runs the protocol with hooks and logs trace events.
4. Judge verifies and aggregates the final answer.

## Protocols
- pipeline: sequential subtask execution.
- roundtable: rotating agents over shared context.
- debate: alternating pro/con stances (requires 2 agents).
- loop: iterative refinement with the same agent.
- hybrid: pipeline phase, then roundtable.

## Hooks
HookManager supports:
- pre_plan, post_plan
- pre_step, post_step
- before_judge, before_output
- on_tool_error

## Usage
- Single task:
  - `python run.py task.goal="Your task here" method=orchestrated`
- Batch eval:
  - `python eval/harness.py tasks=path/to/tasks.jsonl method=orchestrated`

## Model Backends
- mock: default, offline deterministic output.
- openai: requires `openai` package and `model.name=...`.

## Key Points
- `logs/traces.jsonl` stores trace events for each run.
- `mock` backend is the default to keep runs offline and deterministic.
- Planner/worker subtasks force pipeline execution when both agents are available.
- Protocols and hooks are minimal but structured for extension.
- Hydra config files keep experiments reproducible and editable as YAML.
- Hydra entrypoints normalize configs into typed dataclasses for safer access.

## Maintenance Record
- 2025-12-22: initial implementation of core architecture, methods, protocols,
  tool runtime, runtime builder, and documentation.
- 2025-12-22: fixed JSON trace serialization for datetime fields.
- 2025-12-22: replaced argparse with Hydra/OmegaConf configs and added YAML configs.
- 2025-12-22: normalized Hydra configs into typed dataclasses in entrypoints.
