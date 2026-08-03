"""Typed IR for AgentCo-Op.

Pydantic models used across the compiler, runtime, and skill registry.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DifficultyT = Literal["trivial", "simple", "moderate", "complex", "open_ended"]
RiskT = Literal["low", "medium", "high"]
BackendT = Literal["llm", "mcp", "python_sandbox", "sandbox_repo", "human_review"]
MemoryScopeT = Literal["private", "shared_read", "shared_write", "artifact_only"]
RoleT = Literal[
    "router",
    "planner",
    "specialist",
    "programmer",
    "retriever",
    "extractor",
    "solver",
    "sandbox_agent",
    "tool",
    "reviewer",
    "integrator",
    "formatter",
    "human_review",
]


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = 20000
    max_cost_usd: Optional[float] = None
    max_wall_time_s: int = 900
    max_iterations: int = 3


class TaskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    raw_task: str
    domain: list[str] = Field(default_factory=list)
    answer_type: str = "short_answer"
    objective: str = "solve"
    difficulty: DifficultyT = "moderate"
    decomposition_need: float = Field(default=0.0, ge=0.0, le=1.0)
    tool_need: float = Field(default=0.0, ge=0.0, le=1.0)
    retrieval_need: float = Field(default=0.0, ge=0.0, le=1.0)
    repo_execution_need: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_available: str = "none"  # exact | unit_test | deterministic_grader | rubric | none
    risk_level: RiskT = "low"
    budget: Budget = Field(default_factory=Budget)
    output_schema: dict[str, Any] | None = None
    constraints: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None  # One-paragraph summary, no hidden CoT.


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    role: str
    backend: BackendT
    skill_refs: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    memory_scope: MemoryScopeT = "private"
    prompt_template: str | None = None
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = 300
    max_retries: int = 1
    confidence_policy: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    condition: str | None = None
    payload_map: dict[str, str] = Field(default_factory=dict)


class GatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    trigger: str
    threshold: float | None = None
    action: str
    max_activations: int = 1
    scope: Literal["node", "graph"] = "node"
    node_ids: list[str] = Field(default_factory=list)


class EvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_type: str = "short_answer"
    normalization: str | None = None  # e.g. gsm8k_numeric, hotpotqa_em, drop_em_f1
    grader: str | None = None          # deterministic grader id, e.g. pytest, exact_match
    required_evidence: bool = False
    answer_schema: dict[str, Any] | None = None


class MemoryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scratch_scope: Literal["per_node", "per_role"] = "per_node"
    blackboard_keys: list[str] = Field(default_factory=list)
    artifact_dir: str = "artifacts"
    persist_trace: bool = True


class WorkflowBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint_id: str
    task_profile: TaskProfile
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    gate_policies: list[GatePolicy] = Field(default_factory=list)
    memory_plan: MemoryPlan = Field(default_factory=MemoryPlan)
    eval_contract: EvalContract = Field(default_factory=EvalContract)
    budget: Budget = Field(default_factory=Budget)
    topology_level: int = 0  # L0..L7 from architecture.md §4.3
    complexity: float = 0.0
    provenance: list[str] = Field(default_factory=list)


class NodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    ok: bool = True
    output: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    logs_summary: str = ""
    errors: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


class RunState(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    blueprint_id: str
    tokens_used: int = 0
    cost_used_usd: float = 0.0
    elapsed_s: float = 0.0
    patches_applied: int = 0
    gate_activations: dict[str, int] = Field(default_factory=dict)
    node_retries: dict[str, int] = Field(default_factory=dict)
    node_results: dict[str, NodeResult] = Field(default_factory=dict)
    terminated: bool = False
    termination_reason: str | None = None


# ---------------------------------------------------------------------------
# Skill objects
# ---------------------------------------------------------------------------


class MetaSkill(BaseModel):
    """A compilation rule for choosing a topology."""

    model_config = ConfigDict(extra="allow")

    name: str
    kind: Literal["meta_skill"] = "meta_skill"
    version: str = "0.1"
    tags: list[str] = Field(default_factory=list)
    complexity_level: int = 1              # L0..L7 topology ladder
    complexity_penalty: float = 0.3
    intent: str = ""
    task_signals: dict[str, Any] = Field(default_factory=dict)
    when_to_use: list[str] = Field(default_factory=list)
    when_not_to_use: list[str] = Field(default_factory=list)
    topology_template: dict[str, Any] = Field(default_factory=dict)
    gates: list[dict[str, Any]] = Field(default_factory=list)
    memory_policy: str = "isolated_scratch_plus_artifact_blackboard"
    expected_benefit: str = ""
    requires_verification: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_path: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, v):
        return str(v) if v is not None else "0.1"


class AgentSkill(BaseModel):
    """A capability contract describing a node backend."""

    model_config = ConfigDict(extra="allow")

    name: str
    kind: Literal["agent_skill"] = "agent_skill"
    backend_type: BackendT
    capabilities: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    requirements: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    cost_class: str = "low"
    risk_level: RiskT = "low"
    applicable_tags: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None


__all__ = [
    "Budget",
    "TaskProfile",
    "NodeSpec",
    "EdgeSpec",
    "GatePolicy",
    "EvalContract",
    "MemoryPlan",
    "WorkflowBlueprint",
    "NodeResult",
    "RunState",
    "MetaSkill",
    "AgentSkill",
]
