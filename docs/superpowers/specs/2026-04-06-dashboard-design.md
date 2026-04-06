# AgentCo-Op Dashboard — Design Spec

## Overview

A web dashboard for the AgentCo-Op framework that visualizes multi-agent workflow topology, execution logs, benchmark metrics, model configuration, and provides a chat interface for interactive workflow execution.

## Constraints

- React SPA + FastAPI backend
- React + Tailwind CSS + shadcn/ui for components
- React Flow for interactive topology graph
- Light theme with purple accent (`#7c3aed` primary)
- Text sidebar navigation (not icon-only)
- Live + post-run monitoring via WebSocket
- No database — reads directly from existing `runs/` directory structure
- Ships as optional `pip install agentcoop[dashboard]` dependency
- CLI: `agentcoop-dashboard` starts the server

---

## Project Structure

```
dashboard/
  backend/
    __init__.py
    server.py              # FastAPI app: static files + REST + WebSocket
    routes/
      __init__.py
      runs.py              # Run listing and detail endpoints
      topology.py          # Blueprint graph + node execution status
      logs.py              # Execution event logs
      config.py            # Model/experiment config read/write
      chat.py              # Interactive workflow execution
      skills.py            # Skill library browsing
    watcher.py             # Filesystem watcher for live run monitoring
  frontend/
    package.json
    vite.config.ts
    tailwind.config.ts
    tsconfig.json
    src/
      main.tsx
      App.tsx
      lib/
        api.ts             # REST client (fetch wrappers)
        ws.ts              # WebSocket client
        types.ts           # TypeScript types matching Python models
      pages/
        Overview.tsx        # Run list + metric cards
        Topology.tsx        # React Flow interactive graph
        Logs.tsx            # Filterable execution trace table
        Chat.tsx            # Workflow interaction interface
        Config.tsx          # Model/experiment settings editor
      components/
        Layout.tsx          # Sidebar + content shell
        Sidebar.tsx         # Navigation + active run indicator
        MetricCard.tsx      # Stat card (value + label + color)
        RunSelector.tsx     # Dropdown to switch between runs
        NodeDetail.tsx      # Slide-out panel for node inspection
        EventRow.tsx        # Expandable log row
        ChatMessage.tsx     # Chat bubble component
```

---

## Visual Design

### Theme

- **Background**: `#fafafa` (main), `#ffffff` (cards/sidebar)
- **Borders**: `#e5e7eb`
- **Text**: `#111827` (primary), `#9ca3af` (secondary), `#6b7280` (muted)
- **Primary accent**: `#7c3aed` (purple-600)
- **Success**: `#16a34a` (green-600)
- **Warning**: `#d97706` (amber-600)
- **Error**: `#dc2626` (red-600)
- **Info**: `#2563eb` (blue-600)

### Layout

Text sidebar (200px) with named navigation items + active run status footer. Main content area with top header bar showing current view title and run selector.

---

## Five Views

### 1. Overview (`/`)

- **Run selector** dropdown at top (lists all runs from `runs/` directory by timestamp)
- **4 metric cards** in a row:
  - Solve Rate / Pass@1 (green)
  - Total Cost in USD (blue)
  - Avg Latency in seconds (amber)
  - Tasks Completed (purple)
- **Recent runs table** below: columns = name, date, benchmark, score, cost, status badge
- **Click a run row** → navigates to `/topology/:runId`

### 2. Topology (`/topology/:runId`)

- **React Flow canvas** rendering the compiled workflow blueprint as a DAG
- **Node appearance**: rounded rectangle with role name, kind icon (agent/evaluator/tool/router), status color fill:
  - Green = success
  - Red = failed
  - Amber/pulsing = running (live mode)
  - Gray = pending
- **Node click** → slide-out `NodeDetail` panel showing:
  - Inputs JSON (collapsible)
  - Outputs JSON (collapsible)
  - Confidence score + bar
  - Cost breakdown (USD, tokens, wall time)
  - Error message if failed
  - Trace metadata
- **Edges** show data flow between nodes. Labels show mapping keys if present.
- **Subgraphs** rendered as dashed-border groups. Gate-activated subgraphs highlighted.
- **Gate annotations** on edges or as badge overlays

### 3. Logs (`/logs/:runId`)

- **Filterable table** of execution events from trace files
- **Columns**: timestamp, event type, node ID, status, confidence, cost
- **Filters**: node dropdown, status dropdown (success/failed/skipped), event type dropdown
- **Expandable rows**: click to show full trace JSON in a code block
- **Color coding**: green rows for success, red for failure, yellow for warnings
- **Search bar** for text search across event data

### 4. Chat (`/chat`)

- **Split layout**: chat panel (left 60%) + mini topology view (right 40%)
- **Chat input**: user types a task description → sends to backend
- **Backend flow**: compile blueprint from task → execute → stream node completions via WebSocket
- **Chat messages**: user messages (right-aligned), system messages showing node completions (left-aligned), final answer highlighted
- **Mini topology**: simplified React Flow view that updates in real-time as nodes complete

### 5. Config (`/config`)

- **Model section**: provider, name, temperature, max_output_tokens (editable form)
- **Experiment section**: current experiment config name, key settings (review_threshold, benchmark_workers)
- **Skill library browser**: two tabs (Meta-Skills / Agent-Skills), each showing name, description, domain_tags, capability_tags. Click to expand and see full SKILL.md content.
- **Save button**: writes config override to `~/.agentcoop/dashboard_config.yaml`

---

## REST API

| Endpoint | Method | Description | Response |
|---|---|---|---|
| `/api/runs` | GET | List all runs | `[{run_id, name, timestamp, benchmark, solve_rate, cost, task_count}]` |
| `/api/runs/:runId` | GET | Run detail | `{summary, metadata, task_results_count}` |
| `/api/runs/:runId/topology` | GET | Blueprint + execution status | `{nodes: [{id, role, kind, status, confidence, cost}], edges: [{id, src, dst, mapping}], subgraphs, gates}` |
| `/api/runs/:runId/logs` | GET | Execution events | `[{timestamp, type, node_id, status, confidence, cost, trace}]` |
| `/api/runs/:runId/tasks` | GET | Per-task results | `[{task_id, correct, prediction, gold, cost, failure_type}]` |
| `/api/config` | GET | Current config | `{model, experiment, executor}` |
| `/api/config` | PUT | Update config | `{saved: true}` |
| `/api/skills` | GET | Skill library | `{meta_skills: [...], agent_skills: [...]}` |
| `/api/chat` | POST | Execute task | `{run_id}` (execution starts, results streamed via WS) |

## WebSocket API

| Endpoint | Direction | Events |
|---|---|---|
| `/ws/run/:runId` | Server → Client | `{type: "node_completed", node_id, status, confidence, cost}`, `{type: "gate_activated", gate_id}`, `{type: "run_complete", summary}` |
| `/ws/chat` | Bidirectional | Client sends `{task}`, server streams `{type: "node_completed", ...}` and `{type: "answer", content}` |

---

## Backend Implementation

### Data Source

The backend reads directly from the `runs/` directory structure written by `RunRecorder`:

```
runs/{name}/{timestamp}_{id}/
  summaries/summary.json       → /api/runs/:id (metrics)
  summaries/run_metadata.json  → /api/runs/:id (metadata)
  eval/task_results.json       → /api/runs/:id/tasks
  traces/*.report.json.gz      → /api/runs/:id/topology, /api/runs/:id/logs
  config/resolved_config.yaml  → /api/config context
```

No database. The filesystem is the data store.

### Live Monitoring

For active runs, `watcher.py` polls the `traces/` directory every 1 second using `pathlib.Path.glob`. When a new `.report.json.gz` file appears, the watcher:
1. Reads and parses the report
2. Extracts node completion events
3. Pushes events to all connected WebSocket clients for that run

### Dependencies

Added to `pyproject.toml` as optional:
```toml
[project.optional-dependencies]
dashboard = [
  "fastapi>=0.110,<1",
  "uvicorn>=0.29,<1",
  "websockets>=12,<14",
]
```

### CLI Entry Point

```toml
[project.scripts]
agentcoop-dashboard = "agentcoop.dashboard.backend.server:main"
```

Starts uvicorn on `localhost:3000`, serves the built React app as static files at `/`, API at `/api/*`.

---

## Commit Organization

| Commit | Scope |
|---|---|
| `[feat] add FastAPI backend with run/topology/logs/config REST endpoints` | Backend API |
| `[feat] add WebSocket live monitoring for active runs` | Backend WS |
| `[feat] scaffold React frontend with Vite + Tailwind + shadcn/ui` | Frontend setup |
| `[feat] add Overview page with run list and metric cards` | Overview view |
| `[feat] add Topology page with React Flow graph and NodeDetail panel` | Topology view |
| `[feat] add Logs page with filterable event table` | Logs view |
| `[feat] add Chat page with split layout and live topology` | Chat view |
| `[feat] add Config page with settings editor and skill browser` | Config view |
| `[feat] add agentcoop-dashboard CLI entry point` | Integration |
| `[docs] update README and CLAUDE.md with dashboard docs` | Documentation |
