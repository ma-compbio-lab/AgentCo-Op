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
- Spec Enrichment: fills missing constraints/success criteria via spec agents before planning.
- Tool Intelligence: optional tool/repo discovery + plan synthesis before execution.
- Chat I/O layer: multi-turn chat wrapper for terminal and Streamlit UI.
- Cross-cutting services:
  - Registry: agent capability catalog used for routing.
  - BudgetRouter: model routing by role and budget.
  - Cache: in-memory TTL cache for tool/agent reuse.
  - Hooks: pre/post plan/step/judge/output intervention points.
  - Observability: JSONL trace logging.
- Memory/Safety: minimal stubs, ready for extension.
- Memory: Memori-backed long-term memory with per-agent and global scopes (optional).

## Repo Layout
- `run.py`: CLI entrypoint.
- `config.py`: structured configs for Hydra/OmegaConf.
- `models.py`: model routing and API key configuration.
- `prompts.py`: prompt construction helpers.
- `utils.py`: JSONL logging and time helpers.
- `core/`: contracts, registry, context, runtime, hooks, judge, safety, budget, cache, memory.
- `core/chat_store.py`: SQLite-backed chat history store (user/assistant only).
- `core/event_bus.py`: simple event stream for logs and token deltas.
- `core/observability_hooks.py`: RunHooks -> EventBus bridge.
- `core/docker_runtime.py`: Docker build/run sandbox for tool execution.
- `core/tool_intelligence.py`: tool discovery + plan synthesis orchestration.
- `core/repo2run_runtime.py`: Repo2Run integration and Dockerfile generation.
- `engine/`: protocols and executor.
- `engine/tool_runtime.py`: tool execution with allowlist and hooks.
- `app_agents/`: planner/worker/judge/io agent builders and factory (named to avoid SDK import collision).
- `app_agents/tool_scout.py`: tool/repo discovery agent (web search).
- `app_agents/tool_evaluator.py`: tool selection agent.
- `app_agents/tool_doc_synth.py`: tool plan synthesis agent.
- `app_agents/docker_repair.py`: Dockerfile repair agent.
- `chat/service.py`: chat session wrapper over the single-turn workflow.
- `cli/chat.py`: terminal multi-turn REPL.
- `ui/app.py`: Streamlit chat UI with live logs + streaming.
- `methods/`: baseline, sequential, orchestrated method implementations.
- `eval/`: harness and metrics for batch evaluation.
- `conf/`: Hydra YAML configs for single runs and eval.

## Data Contracts (core/contracts.py)
- TaskSpec: user goal, constraints, criteria, budgets, modalities, and optional chat_context.
- TaskSpec includes spec metadata: `spec_source/spec_confidence/spec_notes/spec_evidence`.
- AgentSpec: capabilities and IO metadata.
- Message: standardized inter-agent envelope.
- ExecutionPlan/SubTask: protocol + task DAG + constraints + model_hint/meta (edges as Edge objects).
- EvidenceRequest/EvidencePack: web search inputs and structured evidence summaries + citations.
- ToolCandidate/ToolPlan/ToolExecutionResult: tool discovery candidates, runnable plans, and execution results.
- Memory items: stored via Memori in agent/global scopes (see core/memory.py).
- JudgeReport: pass/fail, score, issues, optional patch.
- TraceEvent: event stream for observability.

## Execution Flow
1. CLI builds TaskSpec and runtime services (context, cache, registry, budget).
2. Spec enrichment fills missing constraints/success criteria (may use web search) and updates TaskSpec.
3. Tool Intelligence (optional) discovers tools and synthesizes a ToolPlan (cached).
4. Orchestrator injects global memory hints into planning (when enabled) and produces an ExecutionPlan.
5. Execution Engine runs evidence steps (if requested), executes ToolPlan in Docker (if enabled),
   injects per-agent memory, and executes protocol steps.
6. Judge runs SDK judge and aggregator agents, returning final output.
7. If the judge fails, a repair loop runs (worker fixes issues) and re-judges up to `repair.max_rounds`.
8. Judge writes success/failure summaries into global memory when enabled.
9. Chat wrapper calls the single-turn workflow repeatedly, injecting recent chat context.

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
- Memory:
  - `python scripts/memory_init.py --db-path memory/agent_cop.db`
  - `python scripts/memory_clear.py --all`
  - Dependencies: `memori` and `sqlalchemy` are required for SQLite-backed memory.
- Spec enrichment:
  - `task.allow_web_search_for_spec=auto` (auto|on|off)
  - `task.spec_max_search_queries=2`
- Tool discovery:
  - `tool.enabled=true`
  - `task.allow_tool_search=auto` (auto|on|off)
  - `task.tool_max_candidates=5`
  - `task.tool_max_search_queries=3`
  - `tool.repo2run_enabled=true` to generate Dockerfiles for GitHub repos.
  - `tool.docker_repair_max_rounds=2` to retry Dockerfile fixes on build failures.
  - install Repo2Run in a separate env via `requirements-repo2run.txt`
  - set `tool.repo2run_python` to the Repo2Run venv Python
- Chat:
  - `python cli/chat.py --config conf/config.yaml --session-id demo`
  - `streamlit run ui/app.py`

## Model Backends
- openai: requires `openai-agents` and `OPENAI_API_KEY`, with optional role overrides.

## Key Points
- `logs/traces.jsonl` stores trace events for each run.
- Planner outputs ExecutionPlan via structured outputs.
- Engine runs protocol steps in code and calls SDK agents via Runner.
- Web search is handled by a dedicated Researcher agent that returns EvidencePack summaries and citations.
- Tool discovery uses `tool_scout`/`tool_evaluator`/`tool_doc_synth` agents and can execute plans in Docker.
- Docker runtime auto-generates a minimal Dockerfile for pip installs; repo execution uses Repo2Run when enabled.
- Docker build failures trigger a bounded LLM repair loop before falling back to manual execution.
- Chat history is stored separately from agent traces and injected as `chat_context` for multi-turn continuity.
- Protocols and hooks are minimal but structured for extension.
- Hydra config files keep experiments reproducible and editable as YAML.
- Hydra entrypoints normalize configs into typed dataclasses for safer access.
- Tests use stubbed SDK calls to avoid network access during unit runs.
- Prompts emphasize clear structure, explicit output formats, and minimal assumptions for predictable agent behavior.
- Terminal logging uses colored module/step output with icons; configure via `log.*` in Hydra config.
- Set `log.show_prompts=true` and `log.show_outputs=true` for more intermediate debug output.
- Repair loop is controlled via `repair.enabled` and `repair.max_rounds`; template enforcement uses `repair.template_mode`.
- Pytest imports are anchored via `tests/conftest.py` to ensure repo root is on `sys.path`.
- Memory is optional; enable via `memory.enabled=true` and initialize storage with `scripts/memory_init.py`.
- Spec enrichment uses `spec_designer` and `spec_critic` agents when constraints/criteria are missing.

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
- 2025-12-29: added EvidencePack web search flow with researcher agent, caching, and evidence injection.
- 2025-12-29: added Memori-backed memory service with per-agent/global recall and judge write-back.
- 2025-12-29: added memory scripts (init/clear) and memory configuration in Hydra.
- 2025-12-29: added repo-root path injection in memory scripts to fix direct execution imports.
- 2025-12-29: added sqlalchemy dependency note and guard for Memori initialization.
- 2025-12-29: memory init uses a SQLite DBAPI connection factory for Memori v3 compatibility.
- 2025-12-29: added spec enrichment with optional web search when constraints/criteria are missing.
- 2025-12-29: expanded README with full feature usage and added inline code comments for clarity.
- 2025-12-29: hardened executor agent fallback, spec evidence cache key, and memory init logging; deduped requirements.
- 2025-12-30: added tool discovery agents, tool plan orchestration, and Docker sandbox execution support.
- 2025-12-30: added Repo2Run integration, Dockerfile repair retries, and Docker workdir support.
- 2025-12-30: moved Repo2Run dependencies to a separate requirements file; documented separate env setup.
- 2025-12-30: removed Repo2Run from main requirements and added `requirements-repo2run.txt`.
- 2025-12-30: added chat service, terminal REPL, Streamlit UI, and event bus for streaming logs/output.
- 2025-12-30: fixed CLI/UI entrypoints by injecting repo root into `sys.path` for direct script runs.
- 2025-12-30: refreshed Streamlit UI with a dark theme, new typography, and improved layout styling.
- 2025-12-30: updated ChatStore to handle async SQLiteSession APIs and removed clear_session coroutine warnings.
