from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

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
    task_type: Literal["auto", "coding", "research", "analysis", "general"] = "auto"
    prompt_verbosity: Literal["minimal", "normal", "verbose"] = "normal"
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


class RoutingDimension(BaseModel):
    name: str
    value: str = ""
    impact: Literal["low", "medium", "high"] = "medium"
    rationale: str = ""


class AdaptiveRoutingDecision(BaseModel):
    mode: Literal["single_agent", "multi_agent"] = "multi_agent"
    enable_tool_search: bool = False
    need_web_search: bool = False
    estimated_agents: int = 4
    confidence: float = 0.5
    reason: str = ""
    recommended_protocol: Optional[Literal["pipeline", "roundtable", "debate", "loop", "hybrid"]] = None
    dimensions: list[RoutingDimension] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, value: Any) -> str:
        if value is None:
            return "multi_agent"
        text = str(value).strip().lower()
        if text in {"single", "single_agent", "single-agent", "baseline"}:
            return "single_agent"
        if text in {"multi", "multi_agent", "multi-agent", "orchestrated"}:
            return "multi_agent"
        return "multi_agent"

    @field_validator("enable_tool_search", "need_web_search", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "on"}

    @field_validator("estimated_agents", mode="before")
    @classmethod
    def _coerce_estimated_agents(cls, value: Any) -> int:
        try:
            number = int(value)
        except Exception:
            number = 4
        return max(1, min(8, number))

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except Exception:
            number = 0.5
        return max(0.0, min(1.0, number))


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

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _coerce_allowed_domains(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
            return items or [value]
        return value


class EvidenceItem(BaseModel):
    title: Optional[str] = None
    url: str
    snippet: Optional[str] = None
    source_type: Optional[str] = None


class EvidencePack(BaseModel):
    request: EvidenceRequest
    summary: str
    citations: list[EvidenceItem] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def _coerce_citations(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict) and "items" in value:
            value = value.get("items")
        if not isinstance(value, list):
            return []
        cleaned: list[Any] = []
        for item in value:
            if isinstance(item, (EvidenceItem, dict)):
                cleaned.append(item)
        return cleaned


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

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, value: Any) -> str:
        if value is None:
            return "pypi"
        text = str(value).strip().lower()
        if text in {"repo", "repository", "github", "gh"}:
            return "github_repo"
        if text in {"package", "python_package"}:
            return "pypi"
        return text


class ToolCandidates(BaseModel):
    candidates: list[ToolCandidate] = Field(default_factory=list)

    @field_validator("candidates", mode="before")
    @classmethod
    def _coerce_candidates(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict) and "items" in value:
            value = value.get("items")
        if isinstance(value, dict) and "candidates" in value:
            value = value.get("candidates")
        if not isinstance(value, list):
            return []
        return value


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

    @field_validator("limits", mode="before")
    @classmethod
    def _coerce_limits(cls, value: Any) -> dict[str, Any] | ContainerLimits:
        if value is None:
            return {}
        return value


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
    required_mcp_servers: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    require_approval: bool = False

    @field_validator("depends_on", mode="before")
    @classmethod
    def _coerce_depends_on(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]


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

    @field_validator("suggested_patch", mode="before")
    @classmethod
    def _coerce_suggested_patch(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"note": value}
        return {"note": str(value)}


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)


TaskSpec.model_rebuild()
