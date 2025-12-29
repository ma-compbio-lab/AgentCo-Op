# Agent-Cop Implementation Notes

## Purpose
This repo implements a simplified multi-agent workflow with three core modules:
Orchestrator (plan/route/assign), Execution Engine (protocol-driven graph runner),
and Judge (verify/repair/aggregate). It avoids LangGraph and uses the OpenAI
Agents SDK with a LatentMAS-like layout (`run.py`, `models.py`, `methods/`).

## Architecture
- Core modules:
  - Orchestrator: uses an SDK planner agent to build an `ExecutionPlan`.
  - Execution Engine: runs protocols in code and invokes SDK agents per step.
  - Judge: uses SDK judge + aggregator agents to verify and finalize output.
- Cross-cutting services:
  - Registry: agent capability catalog used for routing.
  - BudgetRouter: model routing by role and budget.
  - Cache: in-memory TTL cache for tool/agent reuse.
  - Hooks: pre/post plan/step/judge/output intervention points.
  - Observability: JSONL trace logging.
  - Memory/Safety: minimal stubs, ready for extension.

## Repo Layout
- `run.py`: CLI entrypoint.
- `config.py`: structured configs for Hydra/OmegaConf.
- `models.py`: model routing and API key configuration.
- `prompts.py`: prompt construction helpers.
- `utils.py`: JSONL logging and time helpers.
- `core/`: contracts, registry, context, runtime, hooks, judge, safety, budget, cache, memory.
- `engine/`: protocols and executor.
- `engine/tool_runtime.py`: tool execution with allowlist and hooks.
- `app_agents/`: planner/worker/judge/io agent builders and factory (named to avoid SDK import collision).
- `methods/`: baseline, sequential, orchestrated method implementations.
- `eval/`: harness and metrics for batch evaluation.
- `conf/`: Hydra YAML configs for single runs and eval.

## Data Contracts (core/contracts.py)
- TaskSpec: user goal, constraints, criteria, budgets, modalities.
- AgentSpec: capabilities and IO metadata.
- Message: standardized inter-agent envelope.
- ExecutionPlan/SubTask: protocol + task DAG + constraints + model_hint/meta (edges as Edge objects).
- JudgeReport: pass/fail, score, issues, optional patch.
- TraceEvent: event stream for observability.

## Execution Flow
1. CLI builds TaskSpec and runtime services (context, cache, registry, budget).
2. Orchestrator calls the SDK planner agent to produce an ExecutionPlan.
3. Execution Engine executes the protocol and runs SDK worker agents per step.
4. Judge runs SDK judge and aggregator agents, returning final output.
5. If the judge fails, a repair loop runs (worker fixes issues) and re-judges up to `repair.max_rounds`.

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
  - `OPENAI_API_KEY=... python run.py task.goal="Your task here" method=orchestrated`
- Batch eval:
  - `OPENAI_API_KEY=... python eval/harness.py tasks=path/to/tasks.jsonl method=orchestrated max_samples=10`
- Tests:
  - `pytest -q`

## Model Backends
- openai: requires `openai-agents` and `OPENAI_API_KEY`, with optional role overrides.

## Key Points
- `logs/traces.jsonl` stores trace events for each run.
- Planner outputs ExecutionPlan via structured outputs.
- Engine runs protocol steps in code and calls SDK agents via Runner.
- Protocols and hooks are minimal but structured for extension.
- Hydra config files keep experiments reproducible and editable as YAML.
- Hydra entrypoints normalize configs into typed dataclasses for safer access.
- Tests use stubbed SDK calls to avoid network access during unit runs.
- Prompts emphasize clear structure, explicit output formats, and minimal assumptions for predictable agent behavior.
- Terminal logging uses colored module/step output with icons; configure via `log.*` in Hydra config.
- Set `log.show_prompts=true` and `log.show_outputs=true` for more intermediate debug output.
- Repair loop is controlled via `repair.enabled` and `repair.max_rounds`; template enforcement uses `repair.template_mode`.
- Pytest imports are anchored via `tests/conftest.py` to ensure repo root is on `sys.path`.

## Maintenance Record
- 2025-12-22: initial implementation of core architecture, methods, protocols,
  tool runtime, runtime builder, and documentation.
- 2025-12-22: fixed JSON trace serialization for datetime fields.
- 2025-12-22: replaced argparse with Hydra/OmegaConf configs and added YAML configs.
- 2025-12-22: normalized Hydra configs into typed dataclasses in entrypoints.
- 2025-12-22: added OpenAI API key wiring and Hydra eval config.
- 2025-12-22: refactored to OpenAI Agents SDK, added context/cache/runtime services, and renamed local agents to app_agents/.
- 2025-12-22: fixed mutable defaults in core contracts (lists/dicts via Field default_factory).
- 2025-12-22: added unit tests for core modules and execution flow.
- 2025-12-22: updated agent prompts per GPT-5 and prompt-engineering guidelines.
- 2025-12-22: added colored per-module/step terminal logging for debugging.
- 2025-12-22: disabled strict JSON schema for SDK structured outputs to allow dict fields.
- 2025-12-22: replaced ExecutionPlan.edges tuple list with Edge objects for valid JSON schema.
- 2025-12-29: added repair loop with judge-issue injection and optional output template enforcement.
- 2025-12-29: added pytest `tests/conftest.py` to fix module import paths during test collection.
- 2025-12-29: switched datetime defaults to timezone-aware UTC to silence deprecation warnings.
