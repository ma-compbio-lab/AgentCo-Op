from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PlanningConfig
from core.contracts import TaskSpec
from utils import ensure_dir


_DEFAULT_PHASES = [
    {
        "title": "Phase 1: Requirements & Discovery",
        "checklist": [
            "Understand user intent",
            "Identify constraints and requirements",
            "Document findings in findings.md",
        ],
        "status": "in_progress",
    },
    {
        "title": "Phase 2: Planning & Structure",
        "checklist": [
            "Define technical approach",
            "Create project structure if needed",
            "Document decisions with rationale",
        ],
        "status": "pending",
    },
    {
        "title": "Phase 3: Implementation",
        "checklist": [
            "Execute the plan step by step",
            "Write code to files before executing",
            "Test incrementally",
        ],
        "status": "pending",
    },
    {
        "title": "Phase 4: Testing & Verification",
        "checklist": [
            "Verify all requirements met",
            "Document test results",
            "Fix any issues found",
        ],
        "status": "pending",
    },
    {
        "title": "Phase 5: Delivery",
        "checklist": [
            "Review all output files",
            "Ensure deliverables are complete",
            "Deliver to user",
        ],
        "status": "pending",
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class _SQLiteDocStore:
    def __init__(self, db_path: str, table: str) -> None:
        ensure_dir(str(Path(db_path).parent))
        self._conn = sqlite3.connect(db_path)
        self._table = table
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                task_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def load(self, task_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            f"SELECT data FROM {self._table} WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def save(self, task_id: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=True)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self._table} (task_id, data, updated_at) VALUES (?, ?, ?)",
            (task_id, payload, _utc_now()),
        )
        self._conn.commit()


class PlanningMemory:
    def __init__(self, cfg: PlanningConfig, memory=None) -> None:
        self.enabled = cfg.enabled
        self._cfg = cfg
        self._memory = memory
        self._plan_store = None
        self._findings_store = None
        self._progress_store = None
        if self.enabled:
            self._plan_store = _SQLiteDocStore(cfg.db_path, "plan_state")
            self._findings_store = _SQLiteDocStore(cfg.findings_db_path or cfg.db_path, "findings_state")
            self._progress_store = _SQLiteDocStore(cfg.progress_db_path or cfg.db_path, "progress_state")
        self._cache: dict[str, dict[str, Any]] = {}

    def ensure_task(self, task: TaskSpec) -> dict[str, Any]:
        if not self.enabled:
            return {}
        state = self._load_state(task.task_id)
        if state:
            return state
        state = self._create_state(task)
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="init")
        return state

    def mark_phase(self, task: TaskSpec, phase_title: str, status: str) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        for phase in state["plan"]["phases"]:
            if phase["title"] == phase_title:
                phase["status"] = status
                state["plan"]["current_phase"] = phase_title
                break
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note=f"phase:{phase_title}:{status}")

    def add_decision(self, task: TaskSpec, decision: str, rationale: str) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        state["plan"]["decisions"].append({"decision": decision, "rationale": rationale})
        state["findings"]["technical_decisions"].append({"decision": decision, "rationale": rationale})
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="decision")

    def add_error(self, task: TaskSpec, error: str, resolution: str | None = None) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        errors = state["plan"]["errors"]
        attempt = 1
        if errors and errors[-1].get("error") == error:
            attempt = int(errors[-1].get("attempt", 0) or 0) + 1
        errors.append({"error": error, "attempt": attempt, "resolution": resolution or ""})
        state["progress"]["error_log"].append(
            {"timestamp": _utc_now(), "error": error, "attempt": attempt, "resolution": resolution or ""}
        )
        state["findings"]["issues"].append({"issue": error, "resolution": resolution or ""})
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="error")

    def add_finding(self, task: TaskSpec, section: str, text: str) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        findings = state["findings"].setdefault(section, [])
        findings.append(text)
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="finding")

    def add_progress(self, task: TaskSpec, action: str, files: list[str] | None = None) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        state["progress"]["actions"].append(
            {"timestamp": _utc_now(), "action": action, "files": files or []}
        )
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="progress")

    def add_test_result(
        self, task: TaskSpec, test: str, expected: str, actual: str, status: str
    ) -> None:
        if not self.enabled:
            return
        state = self.ensure_task(task)
        state["progress"]["tests"].append(
            {"test": test, "expected": expected, "actual": actual, "status": status}
        )
        self._save_state(task.task_id, state)
        self._write_summary(task, state, note="test")

    def render_prompt_block(self, task: TaskSpec) -> str:
        if not self.enabled or not self._cfg.include_in_prompts:
            return ""
        state = self.ensure_task(task)
        plan = state["plan"]
        findings = state["findings"]
        progress = state["progress"]
        max_items = max(1, self._cfg.max_items)
        phase_lines = [f"- {phase['title']}: {phase['status']}" for phase in plan["phases"]]
        decisions = plan["decisions"][-max_items:]
        errors = plan["errors"][-max_items:]
        recent_actions = progress["actions"][-max_items:]
        lines = [
            "### Planning Memory (Summary)",
            "Planning-with-memory is enabled; do not create task_plan.md/findings.md/progress.md files.",
            f"Goal: {plan['goal']}",
            f"Current Phase: {plan['current_phase']}",
            "Phases:",
            *phase_lines,
        ]
        if decisions:
            lines.append("Recent Decisions:")
            for item in decisions:
                lines.append(f"- {item['decision']} ({item['rationale']})")
        if errors:
            lines.append("Recent Errors:")
            for item in errors:
                lines.append(f"- {item['error']} (attempt {item['attempt']})")
        if recent_actions:
            lines.append("Recent Progress:")
            for item in recent_actions:
                lines.append(f"- {item['timestamp']}: {item['action']}")
        if findings.get("requirements"):
            lines.append("Requirements (from findings):")
            for item in findings["requirements"][-max_items:]:
                lines.append(f"- {item}")
        research = findings.get("research_findings") or []
        if research:
            lines.append("Research Findings:")
            for item in research[-max_items:]:
                lines.append(f"- {item}")
        resources = findings.get("resources") or []
        if resources:
            lines.append("Resources:")
            for item in resources[-max_items:]:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _load_state(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self._cache:
            return self._cache[task_id]
        if not self._plan_store or not self._findings_store or not self._progress_store:
            return None
        plan = self._plan_store.load(task_id)
        findings = self._findings_store.load(task_id)
        progress = self._progress_store.load(task_id)
        if not plan or not findings or not progress:
            return None
        state = {"plan": plan, "findings": findings, "progress": progress}
        self._cache[task_id] = state
        return state

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        if not self._plan_store or not self._findings_store or not self._progress_store:
            return
        self._plan_store.save(task_id, state["plan"])
        self._findings_store.save(task_id, state["findings"])
        self._progress_store.save(task_id, state["progress"])
        self._cache[task_id] = state

    def _create_state(self, task: TaskSpec) -> dict[str, Any]:
        requirements = [task.goal]
        requirements.extend(task.constraints or [])
        requirements.extend(task.success_criteria or [])
        plan = {
            "task_id": task.task_id,
            "goal": task.goal,
            "current_phase": _DEFAULT_PHASES[0]["title"],
            "phases": json.loads(json.dumps(_DEFAULT_PHASES, ensure_ascii=True)),
            "questions": [],
            "decisions": [],
            "errors": [],
            "notes": [
                "Update phase status as you progress: pending → in_progress → complete.",
                "Re-read the plan before major decisions.",
                "Log all errors; do not repeat failed actions.",
            ],
        }
        findings = {
            "requirements": requirements,
            "research_findings": [],
            "technical_decisions": [],
            "issues": [],
            "resources": [],
            "visual_findings": [],
        }
        progress = {
            "session": _utc_now().split(" ")[0],
            "actions": [],
            "tests": [],
            "error_log": [],
        }
        return {"plan": plan, "findings": findings, "progress": progress}

    def _write_summary(self, task: TaskSpec, state: dict[str, Any], note: str) -> None:
        if not self._cfg.store_to_memory or not self._memory or not self._memory.enabled:
            return
        summary = self.render_prompt_block(task)
        if not summary:
            return
        label = f"plan:{task.task_id}"
        self._memory.write_global(
            summary,
            labels=["planning", label, note],
            category="plan",
        )
