# Agent-Cop Implementation Notes

## Purpose
Agent-Cop implements a compact multi-agent workflow with three core modules:
Orchestrator (plan/route/assign), Execution Engine (protocol runner),
and Judge (verify/repair/aggregate). It uses the OpenAI Agents SDK and a
LatentMAS-style layout (`run.py`, `models.py`, `methods/`).

## Core Architecture
- Orchestrator: builds an `ExecutionPlan` from the task spec.
- Execution Engine: runs protocol steps and invokes agents/tools.
- Judge: validates outputs, triggers repairs, and aggregates final answers.

## Cross-Cutting Services
- Registry: agent capability catalog.
- BudgetRouter: model selection per role.
- Cache: in-memory caching for plans/evidence/tool results.
- Hooks: pre/post plan/step/judge/output intervention points.
- Observability: JSONL trace logging.
- Memory (optional): Memori-based per-agent + global memory.
- MCP Manager (optional): MCP server lifecycle with tool filtering/caching.
- Local Exec (optional): workspace-only local command execution with approvals.
- Docker Runtime (optional): sandboxed tool execution + Repo2Run integration.

## Execution Flow
1. Build `TaskSpec` (with optional chat context).
2. Spec enrichment fills missing constraints/success criteria if needed.
3. Tool discovery (optional) produces a ToolPlan.
4. Orchestrator builds an ExecutionPlan.
5. Engine executes steps (protocols, tools, MCP, evidence).
6. Judge validates output; repair loop runs if configured.
7. Final answer is returned and traced.

## Key Features
- Protocols: pipeline, roundtable, debate, loop, hybrid.
- Spec enrichment with optional web search evidence.
- Tool discovery + Docker sandbox; Repo2Run for Dockerfile generation.
- MCP integration for tools/data/prompts with tool filtering + caching.
- Local execution with workspace-only writes and approval prompts.
- Multi-turn chat (terminal + Streamlit UI) with live logs/streaming.

## Config Summary (Hydra)
- Task: `task.goal`, `task.constraints`, `task.success_criteria`,
  `task.task_type`, `task.prompt_verbosity`.
- Repair: `repair.enabled`, `repair.max_rounds`, `repair.template_mode`.
- Tool/Docker: `tool.enabled`, `tool.use_docker`, `tool.repo2run_enabled`.
- Local Exec: `exec.enabled`, `exec.workspace_root`, `exec.require_approval`.
- MCP: `mcp.enabled`, `mcp.servers`, `mcp.prompts`.
- Chat: `chat.db_path`, `chat.history_turns`, `chat.stream`.

## Safety Notes
- Local exec writes are blocked outside `exec.workspace_root`.
- Docker runs can disable network access and apply resource limits.
- MCP tools should be filtered per-step and gated for sensitive actions.

## Tests
- `pytest -q` (third_party tests are excluded).
- Added focused unit tests for local execution in `tests/test_local_exec.py`.
- MedQA benchmark runner: `eval/med_qa_eval.py` (requires `datasets`).

## Recent Maintenance (Condensed)
- Added MCP manager + tool filtering.
- Added Repo2Run + Docker repair loop.
- Added chat REPL + Streamlit UI with streaming logs.
- Added local execution tool with workspace-only approvals.
- Fixed evidence prompt verbosity handling to prevent runtime errors.
- Added MedQA evaluation script and dataset dependency.
