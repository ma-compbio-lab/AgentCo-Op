# Agent-Cop

Agent-Cop is a minimal, reproducible multi-agent workflow runner with three core modules:
Orchestrator (plan/route/assign), Execution Engine (protocol runner), and Judge (verify/repair/aggregate).
It uses the OpenAI Agents SDK (no LangGraph) and keeps a LatentMAS-style layout.

## Features

- Multi-agent orchestration (planner + worker + judge + aggregator).
- Protocols: pipeline, roundtable, debate, loop, hybrid.
- Web search as evidence (researcher agent only) with caching.
- Spec enrichment: auto-generate missing constraints/success criteria.
- Auto repair: judge failure -> repair -> re-judge (multi-round).
- Tool discovery + optional Docker sandbox execution (tool/repo planning).
- Optional local shell execution with workspace-only writes and approval prompts.
- MCP integration for tools/data/prompts with tool filtering and caching (optional).
- Optional Memori-backed long-term memory (per-agent + global).
- Optional planning-with-memory (task plan / findings / progress stored in DB).
- Hydra configs and CLI overrides for experiments.
- Multi-turn chat (terminal REPL + Streamlit UI) with live logs and streaming.
- Colored debug logging with prompt/output previews.

## Requirements

- Python 3.10 or 3.11 recommended.
- OpenAI Agents SDK (`openai-agents`).
- Optional memory: `memori` + `sqlalchemy`.
- Optional tool execution: Docker Engine for sandboxed tool runs.
- Optional Repo2Run: install in a separate env via `requirements-repo2run.txt`.
  - macOS: install Docker Desktop and ensure `docker` is available on PATH.
- Optional MCP servers:
  - Node.js + `npx` (TypeScript MCP servers like filesystem/github/postgres).
  - `uvx` (Python MCP servers like git).
- Optional UI: `streamlit`, `prompt_toolkit`, `rich` (already in `requirements.txt`).

Install:

```bash
pip install -r requirements.txt
```

Set your API key:

```bash
export OPENAI_API_KEY="your-key"
```

## Quick Start

```bash
python run.py \
  method=orchestrated \
  task.goal="Summarize key risks in this plan."
```

## Detailed Usage Guide

### 1) Configure a task (YAML or CLI)

Default config lives in `conf/config.yaml`. Edit it directly, or override via CLI:

```bash
python run.py \
  method=orchestrated \
  task.goal="Implement a Python function levenshtein_distance(a: str, b: str) -> int." \
  'task.constraints=["Use Python 3.10 syntax.","No external libraries."]' \
  'task.success_criteria=["Include 3 tests."]'
```

Tip: in zsh, quote list overrides to avoid globbing errors.

### 2) Choose prompt guidance (task type + verbosity)

```bash
python run.py task.task_type=coding task.prompt_verbosity=verbose
```

### 3) Enable spec enrichment (auto constraints/criteria)

If constraints or success criteria are missing, the spec designer/critic will fill them in:

```bash
python run.py task.constraints=[] task.success_criteria=[]
```

### 4) Evidence-first web search (research tasks)

```bash
python run.py task.allow_web_search_for_spec=auto task.spec_max_search_queries=2
```

### 5) Tool discovery + Docker sandbox

```bash
python run.py tool.enabled=true task.allow_tool_search=on
```

Repo2Run (Dockerfile generation for GitHub repos):

```bash
python run.py tool.enabled=true tool.repo2run_enabled=true
```

### 6) MCP tools/data/prompts

Enable MCP and define servers in `conf/config.yaml`:

```bash
python run.py mcp.enabled=true
```

### 7) Local execution (workspace-only writes)

```bash
python run.py exec.enabled=true exec.workspace_root=. exec.require_approval=true
```

### 8) Multi-turn chat (terminal / UI)

```bash
python cli/chat.py --config conf/config.yaml --session-id demo
streamlit run ui/app.py
```

### 9) Batch eval

```bash
python eval/harness.py tasks=data/tasks.jsonl method=orchestrated max_samples=10
```

### 10) SpatialBench eval

SpatialBench lives in `third_party/spatialbench`. The eval wrapper uses the same
config interface as MedQA (`conf/config_w_api.yaml` by default) and runs your
workflow against SpatialBench eval JSON files.

Requirements:
- Install SpatialBench deps (see `third_party/spatialbench/pyproject.toml`)
- Install `latch` CLI if you want to download `latch://` data nodes
- Set `LATCH_TOKEN` for private datasets

Example (canonical evals):

```bash
python eval/spatialbench_eval.py \
  --config conf/config_w_api.yaml \
  --eval-dir third_party/spatialbench/evals_canonical \
  --max-samples 2 \
  --progress
```

If you want a quick pipeline check without downloading data:

```bash
python eval/spatialbench_eval.py \
  --config conf/config_w_api.yaml \
  --eval-path third_party/spatialbench/evals_canonical/qc/xenium_xenium_qc_filter_min_umi_counts.json \
  --skip-download \
  --progress
```

Tip: for non-interactive batch runs, disable local write approvals:

```bash
python eval/spatialbench_eval.py \
  --config conf/config_w_api.yaml \
  exec.require_approval=false
```

### 10) Logging + debug

```bash
python run.py log.level=debug log.show_prompts=true log.show_outputs=true
```

## Multi-Turn Chat (Terminal)

```bash
python cli/chat.py --config conf/config.yaml --session-id demo
```

Chat history is stored in `chat.db` (configurable via `chat.db_path`).

You can pass Hydra-style overrides after the config:

```bash
python cli/chat.py --config conf/config.yaml model.name=gpt-4o-mini log.level=debug
```

Chat settings:

```bash
chat.db_path=data/chat.db
chat.history_turns=8
chat.stream=true
```

## Streamlit UI

```bash
streamlit run ui/app.py
```

## CLI and Hydra Overrides

Config files:

- `conf/config.yaml`: single-run config
- `conf/eval.yaml`: batch eval config

Override examples:

```bash
python run.py task.goal="Draft a checklist." model.name=gpt-4o-mini
python run.py model.planner=gpt-4o model.worker=gpt-4o-mini model.judge=gpt-4o
python eval/harness.py tasks=data/tasks.jsonl method=sequential max_samples=10
```

### Note on zsh and list syntax

If you want to pass empty lists, quote them to avoid zsh globbing errors:

```bash
python run.py \
  task.goal="Implement a function" \
  'task.constraints=[]' \
  'task.success_criteria=[]'
```

## Methods

- `baseline`: single worker agent
- `sequential`: planner -> worker
- `orchestrated`: orchestrator + engine + judge (recommended)

## Spec Enrichment (Auto Constraints / Success Criteria)

When `constraints` or `success_criteria` are missing, the system runs a spec
enrichment phase before planning:

- `spec_designer` proposes constraints/criteria.
- `spec_critic` refines them for testability and consistency.
- Optional web search for spec design (researcher agent).

Controls:

```bash
task.allow_web_search_for_spec=auto   # auto | on | off
task.spec_max_search_queries=2
task.task_type=auto                  # auto | coding | research | analysis | general
task.prompt_verbosity=normal         # minimal | normal | verbose
```

Example:

```bash
python run.py \
  method=orchestrated \
  task.goal="Implement levenshtein_distance(a: str, b: str) -> int." \
  'task.constraints=[]' \
  'task.success_criteria=[]' \
  task.allow_web_search_for_spec=auto \
  task.spec_max_search_queries=1
```

## Web Search (Evidence-First)

Web search is confined to the `researcher` agent and produces structured
`EvidencePack` summaries + citations. Other agents consume evidence but do not
search the web directly.

Automatic gating uses heuristics (latest/current/2025/etc). You can also set
`needs_web_search` via planning or by task wording.

## Tool Discovery + Docker Sandbox

The orchestrated method can discover external tools or repos and (optionally)
execute them inside a Docker sandbox. Tool discovery uses dedicated agents:
`tool_scout` -> `tool_evaluator` -> `tool_doc_synth`.
Repo execution can optionally use Repo2Run to generate Dockerfiles.

Controls:

```bash
tool.enabled=true
task.allow_tool_search=auto   # auto | on | off
task.tool_max_candidates=5
task.tool_max_search_queries=3
```

Example:

```bash
python run.py \
  method=orchestrated \
  tool.enabled=true \
  task.allow_tool_search=on \
  task.goal="Find an existing Python package to extract tables from PDFs and outline a runnable plan."
```

Repo example (Dockerfile via Repo2Run):

```bash
python run.py \
  method=orchestrated \
  tool.enabled=true \
  tool.repo2run_enabled=true \
  task.allow_tool_search=on \
  task.goal="Use https://github.com/psf/requests and describe how to run its tests."
```

Docker defaults:

```bash
tool.use_docker=true
tool.base_image=python:3.10-slim
tool.run_allow_net=false
tool.default_timeout_s=300
tool.docker_repair_max_rounds=2
```

## Local Execution (Workspace-Only Writes)

Enable the local execution tool to run shell commands safely inside the workspace. Reads are
allowed anywhere; writes require approval and are blocked outside the workspace.

```bash
python run.py exec.enabled=true exec.workspace_root=. exec.require_approval=true
```

When a write is requested, the terminal prompts with: allow once, allow always for the path, or deny.

Sample tasks are listed in `conf/exec_examples.yaml`.

## Prompt Verbosity + Task Type

You can tune prompt verbosity and task-type guidance via config:

```bash
task.task_type=coding
task.prompt_verbosity=verbose
```

Task types:
- `coding`: focuses on correctness, edge cases, tests.
- `research`: prioritizes evidence and citations.
- `analysis`: emphasizes assumptions, metrics, and reproducible reasoning.
- `general`: concise, goal-focused delivery.

Verbosity levels:
- `minimal`: shortest guidance.
- `normal`: balanced guidance (default).
- `verbose`: step-by-step instructions + examples.

## MCP (Model Context Protocol)

MCP lets the workflow use external tools, resources, and prompts without custom adapters.
Enable MCP in config and define servers:

```yaml
mcp:
  enabled: true
  approval_mode: auto   # auto | required | off
  cache_tools_list: true
  enforce_tool_filter: false
  servers:
    filesystem:
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/abs/path/to/workspace"]
      default_tools: ["read_file", "list_files"]
    git:
      transport: stdio
      command: uvx
      args: ["mcp-server-git", "--repository", "/abs/path/to/repo"]
```

Optional prompt overrides via MCP:

```yaml
mcp:
  prompts:
    planner:
      server: filesystem
      prompt: planner_template
```

Notes:
- The planner sees available MCP servers/tools when enabled.
- Subtasks can declare `required_mcp_servers` and `allowed_tools` in the plan.
- Judge/Aggregator can use MCP tools if `plan.meta` includes `judge_mcp_servers` or `aggregator_mcp_servers`.
- `approval_mode=required` blocks MCP tool usage unless you add an approval flow.
- `cache_tools_list=true` avoids repeated `list_tools()` calls for faster planning.

Repo2Run (optional):

```bash
tool.repo2run_enabled=true
tool.repo2run_path=third_party/Repo2Run
tool.repo2run_llm=gpt-4o-mini
```

Setup:

```bash
git clone https://github.com/bytedance/Repo2Run third_party/Repo2Run
pip install -r third_party/Repo2Run/requirements.txt
```

Notes:
- Docker must be installed and available on PATH.
- If `tool.enabled=false`, tool discovery is skipped.
- Tool execution results are injected into the engine state as a message.
- The default Dockerfile only installs PyPI/CLI tools; repo execution needs a ToolPlan with a Dockerfile or setup commands.
- Repo2Run requires cloning the repo to `tool.repo2run_path` and installing its dependencies.
- Docker build failures trigger a bounded repair loop (`tool.docker_repair_max_rounds`).

## Memory (Memori)

Memori-backed long-term memory is optional and off by default.

Initialize storage:

```bash
python scripts/memory_init.py --db-path memory/agent_cop.db
```

Enable memory:

```bash
python run.py \
  memory.enabled=true \
  memory.db_path=memory/agent_cop.db \
  memory.entity_id=default \
  memory.top_k=3
```

Clear memory:

```bash
python scripts/memory_clear.py --all
```

Note: Memori requires `huggingface_hub<1.0`. If memory import fails, run:

```bash
pip install "huggingface_hub>=0.34,<1.0"
```

## Auto Repair (Judge -> Repair -> Re-judge)

Enable multi-round repair when judge fails:

```bash
python run.py \
  repair.enabled=true \
  repair.max_rounds=3 \
  repair.template_mode=auto
```

`repair.template_mode`:

- `off`: no enforced output sections
- `auto`: enforce template for code-like tasks
- `force`: always enforce template

## Logging and Debug

```bash
python run.py log.level=debug log.use_color=true log.use_icons=true
```

Prompt and output previews:

```bash
python run.py log.level=debug log.show_prompts=true log.show_outputs=true log.preview_chars=600
```

## Tests

```bash
pytest -q
```

## Benchmark: MedQA (bigbio/med_qa)

Install dependencies:

```bash
pip install datasets
```

Run evaluation (uses `conf/config_w_api.yaml` by default):

```bash
python eval/med_qa_eval.py --config conf/config_w_api.yaml --split test --max-samples 50
```

Offline/local dataset (after `datasets.Dataset.save_to_disk`):

```bash
python eval/med_qa_eval.py --config conf/config_w_api.yaml --data-dir /path/to/med_qa_saved --split test
```

Local MedQA JSONL (downloaded from dataset repo):

```bash
python eval/med_qa_eval.py --config conf/config_w_api.yaml --jsonl-dir data/med_qa_repo/data_clean/questions/US --split test
```

Outputs:
- `logs/med_qa_results.jsonl`: per-sample predictions.
- `logs/med_qa_summary.json`: accuracy summary.
Summary metrics include accuracy, top-3/5 accuracy, invalid-output rate, average tokens,
and a per-`meta_info` breakdown.

## Project Layout

```
run.py              # CLI entrypoint
config.py           # Hydra dataclasses
models.py           # model routing + API key
prompts.py          # prompt builders
utils.py            # logging helpers + JSONL
core/               # contracts, memory, orchestrator, judge, hooks, etc.
engine/             # protocols + executor
app_agents/         # agent builders (planner/worker/judge/researcher/spec)
methods/            # baseline/sequential/orchestrated
scripts/            # memory init/clear
conf/               # Hydra configs
cli/                # terminal chat entrypoint
chat/               # chat service wrapper
ui/                 # Streamlit UI
```

## Troubleshooting

- If you see `no matches found: task.constraints=[]` in zsh, quote the override.
- If memory init fails, ensure `memori` and `sqlalchemy` are installed.
- If web search is not available, confirm your OpenAI account has tool access.
- If tool execution fails, verify Docker is installed and `tool.enabled=true`.
### 7.5) Planning with memory (task plan + findings + progress in DB)

```bash
python run.py planning.enabled=true
```

This stores task plan/findings/progress in `memory/planning.db` (configurable) and
injects a compact plan summary into prompts to avoid context explosion.

Optional separate DBs:

```yaml
planning:
  db_path: memory/planning.db
  findings_db_path: memory/planning_findings.db
  progress_db_path: memory/planning_progress.db
```
