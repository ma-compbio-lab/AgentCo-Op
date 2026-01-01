from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

ContentType = Literal["text", "json", "tool_call", "tool_result", "file_ref"]


class TaskSpec(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    chat_context: Optional[str] = None
    budget_tokens: int = 8000
    budget_usd: Optional[float] = None
    max_latency_s: Optional[float] = None
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    allow_web_search_for_spec: Literal["auto", "on", "off"] = "auto"
    spec_max_search_queries: int = 2
    allow_tool_search: Literal["auto", "on", "off"] = "auto"
    tool_max_candidates: int = 5
    tool_max_search_queries: int = 3
    spec_source: Literal["user", "auto", "mixed"] = "user"
    spec_confidence: float = 0.0
    spec_notes: Optional[str] = None
    spec_evidence: list["EvidencePack"] = Field(default_factory=list)


class TaskSpecPatch(BaseModel):
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    spec_notes: str = ""
    spec_confidence: float = 0.7
    used_web_search: bool = False


class AgentSpec(BaseModel):
    agent_id: str
    name: str
    description: str
    capabilities: list[str]
    input_types: list[str]
    output_types: list[str]
    tool_allowlist: list[str] = Field(default_factory=list)
    cost_hint: dict[str, Any] = Field(default_factory=dict)


class EvidenceRequest(BaseModel):
    query: str
    freshness: str = "any"
    allowed_domains: list[str] = Field(default_factory=list)
    max_sources: int = 10
    require_citations: bool = True


class EvidenceItem(BaseModel):
    title: Optional[str] = None
    url: str
    snippet: Optional[str] = None
    source_type: Optional[str] = None


class EvidencePack(BaseModel):
    request: EvidenceRequest
    summary: str
    citations: list[EvidenceItem] = Field(default_factory=list)


class ToolCandidate(BaseModel):
    kind: Literal["pypi", "github_repo", "cli", "api"]
    name: str
    version: Optional[str] = None
    repo_url: Optional[str] = None
    commit_sha: Optional[str] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    license: Optional[str] = None
    maintained_score: Optional[float] = None
    risk_flags: list[str] = Field(default_factory=list)


class ToolCandidates(BaseModel):
    candidates: list[ToolCandidate] = Field(default_factory=list)


class ContainerLimits(BaseModel):
    cpus: Optional[float] = None
    memory_mb: Optional[int] = None
    pids: Optional[int] = None
    timeout_s: Optional[int] = None


class ContainerSpec(BaseModel):
    base_image: str = "python:3.10-slim"
    dockerfile_path: Optional[str] = None
    context_dir: Optional[str] = None
    workdir: Optional[str] = "/workspace"
    build_args: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    limits: ContainerLimits = Field(default_factory=ContainerLimits)
    build_allow_net: bool = True
    run_allow_net: bool = False


class ToolPlan(BaseModel):
    selected_tool: ToolCandidate
    docs_links: list[str] = Field(default_factory=list)
    install_strategy: str = "pip"
    container_spec: ContainerSpec = Field(default_factory=ContainerSpec)
    run_commands: list[str] = Field(default_factory=list)
    verify_commands: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)


class ToolExecutionResult(BaseModel):
    status: Literal["success", "fail"]
    stdout: str = ""
    stderr: str = ""
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    artifacts: list[str] = Field(default_factory=list)
    error_signature: Optional[str] = None
    cost: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    msg_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: str
    receiver: str
    content_type: ContentType
    content: Any
    meta: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    from_node: str
    to_node: str


class SubTask(BaseModel):
    sub_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    instructions: str
    assigned_to: str
    depends_on: list[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    protocol: Literal["pipeline", "roundtable", "debate", "loop", "hybrid"]
    active_agents: list[str]
    subtasks: list[SubTask]
    edges: list[Edge] = Field(default_factory=list)
    needs_web_search: bool = False
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    tool_plan: Optional[ToolPlan] = None
    acceptance_tests: list[str] = Field(default_factory=list)
    budget_tokens: int = 8000
    model_hint: Optional[str] = None
    max_rounds: int = 8
    hooks_enabled: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class JudgeReport(BaseModel):
    ok: bool
    score: float = 0.0
    issues: list[str] = Field(default_factory=list)
    suggested_patch: Optional[dict[str, Any]] = None


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)


TaskSpec.model_rebuild()
