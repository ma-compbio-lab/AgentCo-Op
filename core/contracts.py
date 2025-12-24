from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

ContentType = Literal["text", "json", "tool_call", "tool_result", "file_ref"]


class TaskSpec(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    constraints: list[str] = []
    success_criteria: list[str] = []
    budget_tokens: int = 8000
    budget_usd: Optional[float] = None
    max_latency_s: Optional[float] = None
    input_modalities: list[str] = ["text"]
    output_modalities: list[str] = ["text"]


class AgentSpec(BaseModel):
    agent_id: str
    name: str
    description: str
    capabilities: list[str]
    input_types: list[str]
    output_types: list[str]
    tool_allowlist: list[str] = []
    cost_hint: dict[str, Any] = {}


class Message(BaseModel):
    msg_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=datetime.utcnow)
    sender: str
    receiver: str
    content_type: ContentType
    content: Any
    meta: dict[str, Any] = {}


class SubTask(BaseModel):
    sub_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    instructions: str
    assigned_to: str
    depends_on: list[str] = []


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    protocol: Literal["pipeline", "roundtable", "debate", "loop", "hybrid"]
    active_agents: list[str]
    subtasks: list[SubTask]
    edges: list[tuple[str, str]] = []
    acceptance_tests: list[str] = []
    budget_tokens: int = 8000
    model_hint: Optional[str] = None
    max_rounds: int = 8
    hooks_enabled: bool = True
    meta: dict[str, Any] = {}


class JudgeReport(BaseModel):
    ok: bool
    score: float = 0.0
    issues: list[str] = []
    suggested_patch: Optional[dict[str, Any]] = None


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=datetime.utcnow)
    event_type: str
    data: dict[str, Any] = {}
