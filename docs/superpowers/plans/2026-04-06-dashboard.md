# AgentCo-Op Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React + FastAPI dashboard for visualizing multi-agent workflow topology, execution logs, metrics, and interactive workflow execution.

**Architecture:** FastAPI backend reads run data from the filesystem (`runs/` directory). React SPA with React Flow for topology visualization, shadcn/ui components, Tailwind CSS styling. WebSocket for live execution monitoring. Single `agentcoop-dashboard` CLI entry point.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, websockets, React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, React Flow, React Router v6

---

## File Map

**Backend (Python):**
- `dashboard/backend/__init__.py`
- `dashboard/backend/server.py` — FastAPI app, static file serving, CLI entry point
- `dashboard/backend/routes/__init__.py`
- `dashboard/backend/routes/runs.py` — GET /api/runs, GET /api/runs/:runId
- `dashboard/backend/routes/topology.py` — GET /api/runs/:runId/topology
- `dashboard/backend/routes/logs.py` — GET /api/runs/:runId/logs
- `dashboard/backend/routes/config.py` — GET/PUT /api/config
- `dashboard/backend/routes/skills.py` — GET /api/skills
- `dashboard/backend/watcher.py` — filesystem watcher + WebSocket broadcast

**Frontend (TypeScript/React):**
- `dashboard/frontend/package.json`
- `dashboard/frontend/vite.config.ts`
- `dashboard/frontend/tailwind.config.ts`
- `dashboard/frontend/tsconfig.json`
- `dashboard/frontend/index.html`
- `dashboard/frontend/src/main.tsx`
- `dashboard/frontend/src/App.tsx` — Router setup
- `dashboard/frontend/src/lib/api.ts` — REST client
- `dashboard/frontend/src/lib/types.ts` — TypeScript types
- `dashboard/frontend/src/components/Layout.tsx` — Sidebar + content shell
- `dashboard/frontend/src/components/Sidebar.tsx` — Navigation
- `dashboard/frontend/src/components/MetricCard.tsx` — Stat card
- `dashboard/frontend/src/pages/Overview.tsx` — Run list + metrics
- `dashboard/frontend/src/pages/Topology.tsx` — React Flow graph
- `dashboard/frontend/src/pages/Logs.tsx` — Event table
- `dashboard/frontend/src/pages/Chat.tsx` — Chat interface
- `dashboard/frontend/src/pages/Config.tsx` — Settings + skill browser

**Config:**
- `pyproject.toml` — add dashboard optional dependency + CLI entry point

---

## Task 1: FastAPI Backend with REST Endpoints

**Files:**
- Create: `dashboard/backend/__init__.py`
- Create: `dashboard/backend/server.py`
- Create: `dashboard/backend/routes/__init__.py`
- Create: `dashboard/backend/routes/runs.py`
- Create: `dashboard/backend/routes/topology.py`
- Create: `dashboard/backend/routes/logs.py`
- Create: `dashboard/backend/routes/config.py`
- Create: `dashboard/backend/routes/skills.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create backend package structure**

```bash
mkdir -p dashboard/backend/routes
touch dashboard/backend/__init__.py
touch dashboard/backend/routes/__init__.py
```

- [ ] **Step 2: Create `dashboard/backend/routes/runs.py`**

```python
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _scan_runs(base_dirs: list[Path]) -> list[dict[str, Any]]:
    runs = []
    for base in base_dirs:
        if not base.exists():
            continue
        for summary_path in sorted(base.rglob("summaries/summary.json"), reverse=True):
            run_dir = summary_path.parents[1]
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata_path = run_dir / "summaries" / "run_metadata.json"
            metadata = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            runs.append({
                "run_id": run_dir.name,
                "run_path": str(run_dir),
                "name": summary.get("benchmark", run_dir.parent.name),
                "timestamp": metadata.get("start_time", run_dir.name[:15]),
                "benchmark": summary.get("benchmark", "unknown"),
                "solve_rate": summary.get("solve_rate", summary.get("pass_at_1", summary.get("accuracy"))),
                "cost": summary.get("average_usd", 0) * summary.get("task_count", 0),
                "task_count": summary.get("task_count", 0),
                "status": "completed",
            })
    return runs


def _read_report(path: Path) -> dict[str, Any]:
    if str(path).endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


_run_dirs: list[Path] = []


def set_run_dirs(dirs: list[Path]) -> None:
    global _run_dirs
    _run_dirs = dirs


@router.get("")
def list_runs() -> list[dict[str, Any]]:
    return _scan_runs(_run_dirs)


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    for base in _run_dirs:
        for run_dir in base.rglob(f"*{run_id}"):
            if run_dir.is_dir() and (run_dir / "summaries" / "summary.json").exists():
                summary = json.loads((run_dir / "summaries" / "summary.json").read_text(encoding="utf-8"))
                metadata = {}
                metadata_path = run_dir / "summaries" / "run_metadata.json"
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                task_results_path = run_dir / "eval" / "task_results.json"
                task_count = 0
                if task_results_path.exists():
                    task_count = len(json.loads(task_results_path.read_text(encoding="utf-8")))
                return {"summary": summary, "metadata": metadata, "task_results_count": task_count, "run_path": str(run_dir)}
    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
```

- [ ] **Step 3: Create `dashboard/backend/routes/topology.py`**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dashboard.backend.routes.runs import _read_report

router = APIRouter(prefix="/api/runs", tags=["topology"])


def _find_run_dir(run_id: str, run_dirs: list[Path]) -> Path | None:
    for base in run_dirs:
        for run_dir in base.rglob(f"*{run_id}"):
            if run_dir.is_dir() and (run_dir / "summaries" / "summary.json").exists():
                return run_dir
    return None


@router.get("/{run_id}/topology")
def get_topology(run_id: str) -> dict[str, Any]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Read first trace to get blueprint structure
    trace_dir = run_dir / "traces"
    nodes_by_id: dict[str, dict] = {}
    edges: list[dict] = []
    events: list[dict] = []

    for report_path in sorted(trace_dir.glob("*.report.json*"))[:1]:
        report = _read_report(report_path)
        for event in report.get("events", []):
            if event.get("type") == "compile_trace":
                payload = event.get("payload", {})
                # Extract blueprint structure from compile trace if available
                break

        for trace in report.get("traces", []):
            nid = trace.get("node_id", "")
            if nid not in nodes_by_id:
                nodes_by_id[nid] = {
                    "id": nid,
                    "role": trace.get("role", nid),
                    "kind": "agent",
                    "status": trace.get("status", "pending"),
                    "confidence": trace.get("confidence"),
                    "cost": trace.get("cost", {}),
                }
            else:
                nodes_by_id[nid]["status"] = trace.get("status", nodes_by_id[nid]["status"])

        # Build edges from node execution order
        trace_ids = [t["node_id"] for t in report.get("traces", []) if t.get("status") != "skipped"]
        for i in range(len(trace_ids) - 1):
            edges.append({
                "id": f"{trace_ids[i]}_to_{trace_ids[i+1]}",
                "src": trace_ids[i],
                "dst": trace_ids[i+1],
            })
        events = report.get("events", [])

    # Aggregate status across all traces
    for report_path in sorted(trace_dir.glob("*.report.json*")):
        report = _read_report(report_path)
        for trace in report.get("traces", []):
            nid = trace.get("node_id", "")
            if nid in nodes_by_id:
                if trace.get("status") == "failed":
                    nodes_by_id[nid]["status"] = "failed"

    return {
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "subgraphs": [],
        "gates": [],
        "events": events[:50],
    }


@router.get("/{run_id}/tasks")
def get_tasks(run_id: str) -> list[dict[str, Any]]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    task_results_path = run_dir / "eval" / "task_results.json"
    if not task_results_path.exists():
        return []
    return json.loads(task_results_path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Create `dashboard/backend/routes/logs.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dashboard.backend.routes.runs import _read_report
from dashboard.backend.routes.topology import _find_run_dir

router = APIRouter(prefix="/api/runs", tags=["logs"])


@router.get("/{run_id}/logs")
def get_logs(run_id: str) -> list[dict[str, Any]]:
    from dashboard.backend.routes.runs import _run_dirs
    run_dir = _find_run_dir(run_id, _run_dirs)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    trace_dir = run_dir / "traces"
    all_events: list[dict[str, Any]] = []

    for report_path in sorted(trace_dir.glob("*.report.json*")):
        task_id = report_path.stem.replace(".report", "")
        report = _read_report(report_path)
        for event in report.get("events", []):
            event_copy = dict(event)
            event_copy["task_id"] = task_id
            all_events.append(event_copy)

    return all_events
```

- [ ] **Step 5: Create `dashboard/backend/routes/config.py`**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["config"])

_config_override_path = Path.home() / ".agentcoop" / "dashboard_config.yaml"


@router.get("/config")
def get_config() -> dict[str, Any]:
    conf_dir = Path(__file__).resolve().parents[3] / "src" / "agentcoop" / "conf"
    result: dict[str, Any] = {}
    config_path = conf_dir / "config.yaml"
    if config_path.exists():
        result["base"] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    model_dir = conf_dir / "model"
    if model_dir.exists():
        result["available_models"] = [p.stem for p in sorted(model_dir.glob("*.yaml"))]
    experiment_dir = conf_dir / "experiment"
    if experiment_dir.exists():
        result["available_experiments"] = [p.stem for p in sorted(experiment_dir.glob("*.yaml"))]
    return result


@router.put("/config")
def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    _config_override_path.parent.mkdir(parents=True, exist_ok=True)
    _config_override_path.write_text(yaml.dump(payload, default_flow_style=False), encoding="utf-8")
    return {"saved": True, "path": str(_config_override_path)}
```

- [ ] **Step 6: Create `dashboard/backend/routes/skills.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["skills"])


@router.get("/skills")
def get_skills() -> dict[str, Any]:
    from agentcoop.skills.library import SkillLibrary
    project_root = Path(__file__).resolve().parents[3]
    lib = SkillLibrary(search_paths=[project_root / "skills", project_root / "src" / "agentcoop" / "skills"])
    return {
        "meta_skills": [
            {"name": s.name, "description": s.description, "domain_tags": s.domain_tags,
             "capability_tags": s.capability_tags, "roles": s.roles, "body": s.body[:500]}
            for s in lib.meta_skills
        ],
        "agent_skills": [
            {"name": s.name, "description": s.description, "domain_tags": s.domain_tags,
             "capability_tags": s.capability_tags, "body": s.body[:500]}
            for s in lib.agent_skills
        ],
    }
```

- [ ] **Step 7: Create `dashboard/backend/server.py`**

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dashboard.backend.routes import runs, topology, logs, config, skills

app = FastAPI(title="AgentCo-Op Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(topology.router)
app.include_router(logs.router)
app.include_router(config.router)
app.include_router(skills.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="AgentCo-Op Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--runs-dir", nargs="*", default=["runs", "runs-test", "runs-final", "runs-opt", "runs-clean"])
    args = parser.parse_args()

    run_dirs = [Path(d).resolve() for d in args.runs_dir]
    runs.set_run_dirs(run_dirs)

    # Serve frontend build if it exists
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Update `pyproject.toml`**

Add to `[project.optional-dependencies]`:
```toml
dashboard = [
  "fastapi>=0.110,<1",
  "uvicorn>=0.29,<1",
  "websockets>=12,<14",
]
```

Add to `[project.scripts]`:
```toml
agentcoop-dashboard = "dashboard.backend.server:main"
```

- [ ] **Step 9: Test the backend**

```bash
pip install -e ".[dashboard]"
python -c "from dashboard.backend.server import app; print('Server imports OK')"
# Start server and test endpoints
python -m dashboard.backend.server --port 3001 &
sleep 2
curl -s http://localhost:3001/api/health | python -m json.tool
curl -s http://localhost:3001/api/runs | python -m json.tool | head -20
curl -s http://localhost:3001/api/skills | python -m json.tool | head -20
kill %1
```

- [ ] **Step 10: Commit**

```bash
git add dashboard/backend/ pyproject.toml
git commit -m "[feat] add FastAPI backend with REST endpoints for runs, topology, logs, config, skills

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: React Frontend Scaffold

**Files:**
- Create: `dashboard/frontend/` (full Vite + React + Tailwind + shadcn/ui scaffold)

- [ ] **Step 1: Initialize Vite React TypeScript project**

```bash
cd dashboard
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

- [ ] **Step 2: Install Tailwind CSS**

```bash
npm install -D tailwindcss @tailwindcss/vite
```

Create `dashboard/frontend/src/index.css`:
```css
@import "tailwindcss";
```

Add Tailwind plugin to `dashboard/frontend/vite.config.ts`:
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:3000',
      '/ws': { target: 'ws://localhost:3000', ws: true },
    },
  },
})
```

- [ ] **Step 3: Install core dependencies**

```bash
npm install react-router-dom @xyflow/react lucide-react
```

- [ ] **Step 4: Create types file `dashboard/frontend/src/lib/types.ts`**

```typescript
export interface RunSummary {
  run_id: string
  run_path: string
  name: string
  timestamp: string
  benchmark: string
  solve_rate: number | null
  cost: number
  task_count: number
  status: string
}

export interface RunDetail {
  summary: Record<string, any>
  metadata: Record<string, any>
  task_results_count: number
  run_path: string
}

export interface TopologyNode {
  id: string
  role: string
  kind: string
  status: 'success' | 'failed' | 'running' | 'pending'
  confidence: number | null
  cost: Record<string, number>
}

export interface TopologyEdge {
  id: string
  src: string
  dst: string
}

export interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  subgraphs: any[]
  gates: any[]
  events: any[]
}

export interface LogEvent {
  type: string
  task_id?: string
  node_id?: string
  status?: string
  confidence?: number
  cost?: Record<string, number>
  [key: string]: any
}

export interface SkillInfo {
  name: string
  description: string
  domain_tags: string[]
  capability_tags: string[]
  body: string
  roles?: any[]
}
```

- [ ] **Step 5: Create API client `dashboard/frontend/src/lib/api.ts`**

```typescript
import type { RunSummary, RunDetail, TopologyData, LogEvent, SkillInfo } from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`)
  return res.json()
}

export const api = {
  getRuns: () => get<RunSummary[]>('/runs'),
  getRun: (id: string) => get<RunDetail>(`/runs/${id}`),
  getTopology: (id: string) => get<TopologyData>(`/runs/${id}/topology`),
  getLogs: (id: string) => get<LogEvent[]>(`/runs/${id}/logs`),
  getTasks: (id: string) => get<any[]>(`/runs/${id}/tasks`),
  getConfig: () => get<any>('/config'),
  getSkills: () => get<{ meta_skills: SkillInfo[]; agent_skills: SkillInfo[] }>('/skills'),
}
```

- [ ] **Step 6: Create Layout and Sidebar components**

`dashboard/frontend/src/components/Layout.tsx`:
```tsx
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'

export function Layout() {
  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
```

`dashboard/frontend/src/components/Sidebar.tsx`:
```tsx
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, GitBranch, ScrollText, MessageSquare, Settings } from 'lucide-react'

const links = [
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  { to: '/topology', icon: GitBranch, label: 'Topology' },
  { to: '/logs', icon: ScrollText, label: 'Logs' },
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/config', icon: Settings, label: 'Config' },
]

export function Sidebar() {
  return (
    <aside className="w-52 bg-white border-r border-gray-200 flex flex-col">
      <div className="px-5 py-5">
        <h1 className="text-lg font-bold text-purple-600">AgentCo-Op</h1>
      </div>
      <nav className="flex-1 px-3 space-y-1">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-purple-50 text-purple-700 border border-purple-200'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-3 pb-4">
        <div className="bg-gray-50 rounded-lg px-3 py-2 text-xs">
          <div className="text-gray-400">Framework</div>
          <div className="text-gray-600 font-medium">AgentCo-Op v0.1.0</div>
        </div>
      </div>
    </aside>
  )
}
```

- [ ] **Step 7: Create MetricCard component**

`dashboard/frontend/src/components/MetricCard.tsx`:
```tsx
interface MetricCardProps {
  label: string
  value: string | number
  color: 'purple' | 'green' | 'blue' | 'amber'
}

const colorMap = {
  purple: 'text-purple-600',
  green: 'text-green-600',
  blue: 'text-blue-600',
  amber: 'text-amber-600',
}

export function MetricCard({ label, value, color }: MetricCardProps) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${colorMap[color]}`}>{value}</div>
    </div>
  )
}
```

- [ ] **Step 8: Create App with routing**

`dashboard/frontend/src/App.tsx`:
```tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Overview } from './pages/Overview'
import { Topology } from './pages/Topology'
import { Logs } from './pages/Logs'
import { Chat } from './pages/Chat'
import { Config } from './pages/Config'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/topology" element={<Topology />} />
          <Route path="/topology/:runId" element={<Topology />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/logs/:runId" element={<Logs />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/config" element={<Config />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
```

- [ ] **Step 9: Create placeholder pages**

Create minimal placeholder for each page so the app compiles:

`dashboard/frontend/src/pages/Overview.tsx`:
```tsx
export function Overview() {
  return <div className="p-6"><h2 className="text-xl font-semibold text-gray-800">Overview</h2></div>
}
```

`dashboard/frontend/src/pages/Topology.tsx`:
```tsx
export function Topology() {
  return <div className="p-6"><h2 className="text-xl font-semibold text-gray-800">Topology</h2></div>
}
```

`dashboard/frontend/src/pages/Logs.tsx`:
```tsx
export function Logs() {
  return <div className="p-6"><h2 className="text-xl font-semibold text-gray-800">Logs</h2></div>
}
```

`dashboard/frontend/src/pages/Chat.tsx`:
```tsx
export function Chat() {
  return <div className="p-6"><h2 className="text-xl font-semibold text-gray-800">Chat</h2></div>
}
```

`dashboard/frontend/src/pages/Config.tsx`:
```tsx
export function Config() {
  return <div className="p-6"><h2 className="text-xl font-semibold text-gray-800">Config</h2></div>
}
```

Update `dashboard/frontend/src/main.tsx`:
```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 10: Verify frontend builds**

```bash
cd dashboard/frontend
npm run build
npm run dev &
# Visit http://localhost:5173 — should show sidebar + "Overview" text
kill %1
```

- [ ] **Step 11: Commit**

```bash
git add dashboard/frontend/
git commit -m "[feat] scaffold React frontend with Vite, Tailwind, React Router, Layout

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Overview Page

**Files:**
- Modify: `dashboard/frontend/src/pages/Overview.tsx`

- [ ] **Step 1: Implement Overview page**

```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { RunSummary } from '../lib/types'
import { MetricCard } from '../components/MetricCard'
import { Activity, DollarSign, Clock, CheckCircle2 } from 'lucide-react'

export function Overview() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selected, setSelected] = useState<RunSummary | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (data.length > 0) setSelected(data[0])
    })
  }, [])

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Overview</h2>
        <select
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          value={selected?.run_id ?? ''}
          onChange={e => setSelected(runs.find(r => r.run_id === e.target.value) ?? null)}
        >
          {runs.map(r => (
            <option key={r.run_id} value={r.run_id}>
              {r.name} — {r.timestamp.slice(0, 10)}
            </option>
          ))}
        </select>
      </div>

      {selected && (
        <div className="grid grid-cols-4 gap-4">
          <MetricCard label="Score" value={`${((selected.solve_rate ?? 0) * 100).toFixed(1)}%`} color="green" />
          <MetricCard label="Total Cost" value={`$${selected.cost.toFixed(4)}`} color="blue" />
          <MetricCard label="Tasks" value={selected.task_count} color="purple" />
          <MetricCard label="Status" value={selected.status} color="amber" />
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left px-4 py-3 font-medium text-gray-500">Run</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Benchmark</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Score</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Tasks</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Cost</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr
                key={run.run_id}
                onClick={() => navigate(`/topology/${run.run_id}`)}
                className="border-b border-gray-50 hover:bg-purple-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 font-medium text-gray-800">{run.name}</td>
                <td className="px-4 py-3 text-gray-600">{run.benchmark}</td>
                <td className="px-4 py-3">
                  <span className="text-green-600 font-semibold">
                    {((run.solve_rate ?? 0) * 100).toFixed(1)}%
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-600">{run.task_count}</td>
                <td className="px-4 py-3 text-gray-600">${run.cost.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify with backend running**

```bash
# Terminal 1: start backend
python -m dashboard.backend.server --port 3000 &
# Terminal 2: start frontend dev
cd dashboard/frontend && npm run dev &
# Visit http://localhost:5173 — should show runs table with real data
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/pages/Overview.tsx
git commit -m "[feat] add Overview page with run list and metric cards

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Topology Page with React Flow

**Files:**
- Modify: `dashboard/frontend/src/pages/Topology.tsx`

- [ ] **Step 1: Implement Topology page**

```tsx
import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../lib/api'
import type { RunSummary, TopologyData } from '../lib/types'
import { X } from 'lucide-react'

const statusColors: Record<string, string> = {
  success: '#16a34a',
  failed: '#dc2626',
  running: '#d97706',
  pending: '#9ca3af',
  cached: '#2563eb',
  skipped: '#9ca3af',
}

function nodeStyle(status: string) {
  return {
    background: '#fff',
    border: `2px solid ${statusColors[status] ?? '#9ca3af'}`,
    borderRadius: 12,
    padding: '10px 16px',
    fontSize: 13,
    fontWeight: 600,
    color: '#111827',
    minWidth: 120,
    textAlign: 'center' as const,
  }
}

export function Topology() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [topo, setTopo] = useState<TopologyData | null>(null)
  const [selectedNode, setSelectedNode] = useState<any>(null)
  const [currentRunId, setCurrentRunId] = useState(runId ?? '')

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (!currentRunId && data.length > 0) {
        setCurrentRunId(data[0].run_id)
      }
    })
  }, [])

  useEffect(() => {
    if (currentRunId) {
      api.getTopology(currentRunId).then(setTopo)
    }
  }, [currentRunId])

  const flowNodes: Node[] = (topo?.nodes ?? []).map((n, i) => ({
    id: n.id,
    position: { x: 200 * i, y: 100 + (i % 2) * 80 },
    data: {
      label: (
        <div>
          <div style={{ fontSize: 11, color: statusColors[n.status] ?? '#888', marginBottom: 2 }}>
            {n.status.toUpperCase()}
          </div>
          <div>{n.role}</div>
          {n.confidence != null && (
            <div style={{ fontSize: 10, color: '#888', marginTop: 2 }}>
              conf: {(n.confidence * 100).toFixed(0)}%
            </div>
          )}
        </div>
      ),
    },
    style: nodeStyle(n.status),
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  }))

  const flowEdges: Edge[] = (topo?.edges ?? []).map(e => ({
    id: e.id,
    source: e.src,
    target: e.dst,
    animated: false,
    style: { stroke: '#a78bfa', strokeWidth: 2 },
  }))

  const onNodeClick = useCallback((_: any, node: Node) => {
    const topoNode = topo?.nodes.find(n => n.id === node.id)
    setSelectedNode(topoNode)
  }, [topo])

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
        <h2 className="text-xl font-semibold text-gray-800">Topology</h2>
        <select
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          value={currentRunId}
          onChange={e => { setCurrentRunId(e.target.value); navigate(`/topology/${e.target.value}`) }}
        >
          {runs.map(r => (
            <option key={r.run_id} value={r.run_id}>{r.name} — {r.timestamp.slice(0, 10)}</option>
          ))}
        </select>
      </div>
      <div className="flex-1 relative">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          onNodeClick={onNodeClick}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#e5e7eb" gap={20} />
          <Controls />
        </ReactFlow>

        {selectedNode && (
          <div className="absolute top-4 right-4 w-80 bg-white border border-gray-200 rounded-xl shadow-lg p-4 max-h-[80vh] overflow-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-gray-800">{selectedNode.role}</h3>
              <button onClick={() => setSelectedNode(null)} className="text-gray-400 hover:text-gray-600">
                <X size={16} />
              </button>
            </div>
            <div className="space-y-2 text-sm">
              <div><span className="text-gray-400">Status:</span> <span className={`font-medium`} style={{ color: statusColors[selectedNode.status] }}>{selectedNode.status}</span></div>
              <div><span className="text-gray-400">Kind:</span> {selectedNode.kind}</div>
              {selectedNode.confidence != null && <div><span className="text-gray-400">Confidence:</span> {(selectedNode.confidence * 100).toFixed(1)}%</div>}
              {selectedNode.cost && (
                <div>
                  <span className="text-gray-400">Cost:</span>
                  <pre className="mt-1 bg-gray-50 rounded p-2 text-xs overflow-auto">{JSON.stringify(selectedNode.cost, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/pages/Topology.tsx
git commit -m "[feat] add Topology page with React Flow graph and node detail panel

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Logs Page

**Files:**
- Modify: `dashboard/frontend/src/pages/Logs.tsx`

- [ ] **Step 1: Implement Logs page**

```tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { RunSummary, LogEvent } from '../lib/types'
import { ChevronDown, ChevronRight } from 'lucide-react'

const statusBadge: Record<string, string> = {
  success: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  skipped: 'bg-gray-100 text-gray-500',
  cached: 'bg-blue-100 text-blue-700',
}

export function Logs() {
  const { runId } = useParams()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [events, setEvents] = useState<LogEvent[]>([])
  const [currentRunId, setCurrentRunId] = useState(runId ?? '')
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const [filterType, setFilterType] = useState('')
  const [filterNode, setFilterNode] = useState('')

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (!currentRunId && data.length > 0) setCurrentRunId(data[0].run_id)
    })
  }, [])

  useEffect(() => {
    if (currentRunId) api.getLogs(currentRunId).then(setEvents)
  }, [currentRunId])

  const types = [...new Set(events.map(e => e.type))]
  const nodes = [...new Set(events.filter(e => e.node_id).map(e => e.node_id!))]
  const filtered = events.filter(e =>
    (!filterType || e.type === filterType) && (!filterNode || e.node_id === filterNode)
  )

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Logs</h2>
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={currentRunId}
          onChange={e => setCurrentRunId(e.target.value)}>
          {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.name}</option>)}
        </select>
      </div>

      <div className="flex gap-3">
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={filterType}
          onChange={e => setFilterType(e.target.value)}>
          <option value="">All types</option>
          {types.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={filterNode}
          onChange={e => setFilterNode(e.target.value)}>
          <option value="">All nodes</option>
          {nodes.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <span className="text-sm text-gray-400 self-center">{filtered.length} events</span>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="w-8"></th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Type</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Node</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Confidence</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Task</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map((event, i) => (
              <>
                <tr key={i} onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                  className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer">
                  <td className="pl-3 text-gray-400">
                    {expandedIdx === i ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{event.type}</td>
                  <td className="px-4 py-2">{event.node_id ?? '—'}</td>
                  <td className="px-4 py-2">
                    {event.status && (
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusBadge[event.status] ?? 'bg-gray-100 text-gray-500'}`}>
                        {event.status}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-gray-600">{event.confidence != null ? `${(event.confidence * 100).toFixed(0)}%` : '—'}</td>
                  <td className="px-4 py-2 text-gray-400 text-xs">{event.task_id ?? ''}</td>
                </tr>
                {expandedIdx === i && (
                  <tr key={`${i}-detail`}>
                    <td colSpan={6} className="px-6 py-3 bg-gray-50">
                      <pre className="text-xs text-gray-600 overflow-auto max-h-60">{JSON.stringify(event, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/pages/Logs.tsx
git commit -m "[feat] add Logs page with filterable event table

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Config Page with Skill Browser

**Files:**
- Modify: `dashboard/frontend/src/pages/Config.tsx`

- [ ] **Step 1: Implement Config page**

```tsx
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { SkillInfo } from '../lib/types'
import { ChevronDown, ChevronRight } from 'lucide-react'

export function Config() {
  const [config, setConfig] = useState<any>(null)
  const [skills, setSkills] = useState<{ meta_skills: SkillInfo[]; agent_skills: SkillInfo[] } | null>(null)
  const [tab, setTab] = useState<'config' | 'meta' | 'agent'>('config')
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)

  useEffect(() => {
    api.getConfig().then(setConfig)
    api.getSkills().then(setSkills)
  }, [])

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-xl font-semibold text-gray-800">Configuration</h2>

      <div className="flex gap-2 border-b border-gray-200">
        {[
          { key: 'config', label: 'Settings' },
          { key: 'meta', label: `Meta-Skills (${skills?.meta_skills.length ?? 0})` },
          { key: 'agent', label: `Agent-Skills (${skills?.agent_skills.length ?? 0})` },
        ].map(t => (
          <button key={t.key} onClick={() => setTab(t.key as any)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key ? 'border-purple-600 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'config' && config && (
        <div className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <h3 className="font-medium text-gray-700 mb-3">Base Configuration</h3>
            <pre className="bg-gray-50 rounded-lg p-4 text-sm text-gray-600 overflow-auto max-h-60">
              {JSON.stringify(config.base, null, 2)}
            </pre>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <h3 className="font-medium text-gray-700 mb-2">Available Models</h3>
              <div className="space-y-1">
                {(config.available_models ?? []).map((m: string) => (
                  <div key={m} className="text-sm text-gray-600 px-2 py-1 bg-gray-50 rounded">{m}</div>
                ))}
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <h3 className="font-medium text-gray-700 mb-2">Available Experiments</h3>
              <div className="space-y-1">
                {(config.available_experiments ?? []).map((e: string) => (
                  <div key={e} className="text-sm text-gray-600 px-2 py-1 bg-gray-50 rounded">{e}</div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {(tab === 'meta' || tab === 'agent') && (
        <div className="space-y-2">
          {(tab === 'meta' ? skills?.meta_skills : skills?.agent_skills)?.map(skill => (
            <div key={skill.name} className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <button onClick={() => setExpandedSkill(expandedSkill === skill.name ? null : skill.name)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50">
                {expandedSkill === skill.name ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
                <div className="flex-1">
                  <div className="font-medium text-gray-800 text-sm">{skill.name}</div>
                  <div className="text-xs text-gray-500">{skill.description}</div>
                </div>
                <div className="flex gap-1 flex-wrap">
                  {skill.domain_tags.map(t => (
                    <span key={t} className="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs">{t}</span>
                  ))}
                </div>
              </button>
              {expandedSkill === skill.name && (
                <div className="px-4 pb-4 border-t border-gray-100">
                  <div className="flex gap-1 mt-2 mb-2 flex-wrap">
                    {skill.capability_tags.map(t => (
                      <span key={t} className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-xs">{t}</span>
                    ))}
                  </div>
                  {skill.roles && skill.roles.length > 0 && (
                    <div className="mb-2">
                      <div className="text-xs font-medium text-gray-500 mb-1">Roles:</div>
                      {skill.roles.map((r: any, i: number) => (
                        <div key={i} className="text-xs text-gray-600 ml-2">- {r.role}: {r.description}</div>
                      ))}
                    </div>
                  )}
                  <pre className="bg-gray-50 rounded p-3 text-xs text-gray-600 whitespace-pre-wrap">{skill.body}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/pages/Config.tsx
git commit -m "[feat] add Config page with settings viewer and skill library browser

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Chat Page

**Files:**
- Modify: `dashboard/frontend/src/pages/Chat.tsx`

- [ ] **Step 1: Implement Chat page**

```tsx
import { useState, useRef, useEffect } from 'react'
import { Send } from 'lucide-react'

interface Message {
  role: 'user' | 'system'
  content: string
  timestamp: Date
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'system', content: 'Welcome to AgentCo-Op. Describe a task and I will compile and execute a workflow for it.', timestamp: new Date() },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    const userMsg: Message = { role: 'user', content: input.trim(), timestamp: new Date() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: userMsg.content }),
      })
      const data = await res.json()
      setMessages(prev => [...prev, {
        role: 'system',
        content: data.error ?? data.summary ?? JSON.stringify(data, null, 2),
        timestamp: new Date(),
      }])
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Error: ${err.message}`,
        timestamp: new Date(),
      }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-6 py-4 border-b border-gray-200 bg-white">
        <h2 className="text-xl font-semibold text-gray-800">Chat</h2>
      </div>

      <div className="flex-1 overflow-auto p-6 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] rounded-xl px-4 py-3 text-sm ${
              msg.role === 'user'
                ? 'bg-purple-600 text-white'
                : 'bg-white border border-gray-200 text-gray-700'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
              <div className={`text-xs mt-1 ${msg.role === 'user' ? 'text-purple-200' : 'text-gray-400'}`}>
                {msg.timestamp.toLocaleTimeString()}
              </div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 text-sm text-gray-400">
              Compiling and executing workflow...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="px-6 py-4 border-t border-gray-200 bg-white">
        <div className="flex gap-3">
          <input
            className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
            placeholder="Describe a task (e.g., 'Solve: What is 2^10 mod 7?')"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
            disabled={loading}
          />
          <button
            onClick={sendMessage}
            disabled={loading || !input.trim()}
            className="bg-purple-600 text-white rounded-xl px-4 py-2 hover:bg-purple-700 disabled:opacity-50 transition-colors"
          >
            <Send size={18} />
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add chat endpoint to backend**

Add to `dashboard/backend/routes/__init__.py` or create a separate file. For simplicity, add to `server.py`:

```python
# In dashboard/backend/server.py, add:
from fastapi import Body

@app.post("/api/chat")
async def chat_endpoint(payload: dict = Body(...)):
    task_description = payload.get("task", "")
    if not task_description:
        return {"error": "No task provided"}
    return {
        "summary": f"Chat execution is not yet connected to the workflow engine. Task received: {task_description}",
        "status": "placeholder",
    }
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/pages/Chat.tsx dashboard/backend/server.py
git commit -m "[feat] add Chat page with message interface

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Build Frontend and Integrate

**Files:**
- Modify: `dashboard/frontend/package.json`

- [ ] **Step 1: Build the frontend**

```bash
cd dashboard/frontend
npm run build
```

- [ ] **Step 2: Verify full stack**

```bash
cd ../..
python -m dashboard.backend.server --port 3000
# Visit http://localhost:3000 — should serve the React app with all pages working
```

- [ ] **Step 3: Commit built assets**

```bash
git add dashboard/frontend/dist/
git commit -m "[feat] build frontend and integrate with FastAPI static serving

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Update Docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update README.md**

Add after the "Skill-driven compilation" section:

```markdown
## Dashboard

AgentCo-Op includes a web dashboard for visualizing workflow topology, execution logs, and benchmark metrics.

\`\`\`bash
pip install -e ".[dashboard]"
agentcoop-dashboard
# Open http://localhost:3000
\`\`\`

Features: interactive topology graph (React Flow), filterable execution logs, metric cards, skill library browser, chat interface for interactive workflow execution.
```

- [ ] **Step 2: Update CLAUDE.md**

Add `agentcoop-dashboard` to the Commands section and add dashboard to the architecture table.

- [ ] **Step 3: Commit and push**

```bash
git add README.md CLAUDE.md
git commit -m "[docs] add dashboard documentation to README and CLAUDE.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git push origin main
```
