from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonDict = Dict[str, Any]


class Transport(str, Enum):
    stdio = "stdio"
    streamable_http = "streamable-http"
    sse = "sse"


class NodeKind(str, Enum):
    agent = "agent"
    tool = "tool"
    router = "router"
    evaluator = "evaluator"
    cache = "cache"


class ModelProvider(str, Enum):
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    deepseek = "deepseek"
    local = "local"
    other = "other"


class FailureType(str, Enum):
    none = "none"
    env_missing_dep = "env_missing_dep"
    env_version_conflict = "env_version_conflict"
    tool_schema_mismatch = "tool_schema_mismatch"
    tool_runtime_error = "tool_runtime_error"
    file_missing = "file_missing"
    output_contract_violation = "output_contract_violation"
    low_confidence = "low_confidence"
    reasoning_inconsistency = "reasoning_inconsistency"
    timeout = "timeout"
    budget_exceeded = "budget_exceeded"
    unknown = "unknown"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: Optional[str] = Field(default=None)
    uri: Optional[str] = Field(default=None)
    mime: Optional[str] = Field(default=None)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    meta: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_identity(self) -> "ArtifactRef":
        if not self.sha256 and not self.uri:
            raise ValueError("ArtifactRef requires at least one of sha256 or uri")
        return self


class IOContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_schema: Optional[JsonDict] = Field(default=None)
    output_schema: Optional[JsonDict] = Field(default=None)
    invariants: List[str] = Field(default_factory=list)


class BudgetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_usd: Optional[float] = Field(default=None, ge=0)
    max_total_tokens: Optional[int] = Field(default=None, ge=0)
    max_wall_time_s: Optional[int] = Field(default=None, ge=1)
    max_tool_calls: Optional[int] = Field(default=None, ge=0)
    max_container_starts: Optional[int] = Field(default=None, ge=0)
    per_node_usd_soft_cap: Optional[float] = Field(default=None, ge=0)


class SafetySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_network: bool = False
    network_allowlist: List[str] = Field(default_factory=list)
    cpu_quota: Optional[float] = Field(default=None, ge=0.1)
    mem_limit_mb: Optional[int] = Field(default=None, ge=64)
    timeout_s: Optional[int] = Field(default=None, ge=1)
    max_output_mb: Optional[int] = Field(default=None, ge=1)


class MountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_path: str
    container_path: str
    read_only: bool = False


class SandboxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_id: str
    image: str
    entrypoint: Optional[List[str]] = Field(default=None)
    cmd: Optional[List[str]] = Field(default=None)
    workdir: Optional[str] = Field(default=None)
    env: Dict[str, str] = Field(default_factory=dict)
    mounts: List[MountSpec] = Field(default_factory=list)
    safety: SafetySpec = Field(default_factory=SafetySpec)
    mcp_transport: Transport = Transport.streamable_http
    mcp_url: Optional[str] = Field(default=None)


class MCPServerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Transport
    url: Optional[str] = Field(default=None)
    stdio_cmd: Optional[List[str]] = Field(default=None)
    env: Dict[str, str] = Field(default_factory=dict)
    sandbox: Optional[SandboxSpec] = Field(default=None)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "MCPServerRef":
        if self.transport in (Transport.streamable_http, Transport.sse):
            if not self.url and not self.sandbox:
                raise ValueError("HTTP/SSE MCP servers require url or sandbox")
        if self.transport == Transport.stdio:
            if not self.stdio_cmd and not self.sandbox:
                raise ValueError("stdio MCP servers require stdio_cmd or sandbox")
        return self


class ToolRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str
    tool: str
    timeout_s: Optional[int] = Field(default=None, ge=1)
    retry: int = Field(default=0, ge=0, le=5)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider
    name: str
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    usd_per_1k_input: Optional[float] = Field(default=None, ge=0)
    usd_per_1k_output: Optional[float] = Field(default=None, ge=0)
    meta: JsonDict = Field(default_factory=dict)


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: NodeKind
    role: str
    description: str = ""
    model: Optional[ModelSpec] = Field(default=None)
    system_prompt: Optional[str] = Field(default=None)
    tools: List[ToolRef] = Field(default_factory=list)
    sandbox: Optional[SandboxSpec] = Field(default=None)
    io: IOContract = Field(default_factory=IOContract)
    max_steps: int = Field(default=8, ge=1)
    stop_conditions: List[str] = Field(default_factory=list)
    cacheable: bool = False
    cost_hint_usd: Optional[float] = Field(default=None, ge=0)
    meta: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind(self) -> "NodeSpec":
        if self.kind in (NodeKind.agent, NodeKind.evaluator, NodeKind.router) and not self.model:
            raise ValueError(f"{self.kind.value} nodes require a model")
        if self.kind == NodeKind.tool and not self.tools:
            raise ValueError("tool nodes require at least one ToolRef")
        return self


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    src: str
    dst: str
    mapping: JsonDict = Field(default_factory=dict)
    condition: Optional[str] = Field(default=None)


class ConditionOp(str, Enum):
    lt = "<"
    le = "<="
    eq = "=="
    ne = "!="
    ge = ">="
    gt = ">"
    contains = "contains"
    in_ = "in"


class AtomicCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    op: ConditionOp
    value: Any


class TriggerExpr(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all_of: List[Union["TriggerExpr", AtomicCondition]] = Field(default_factory=list)
    any_of: List[Union["TriggerExpr", AtomicCondition]] = Field(default_factory=list)
    not_: Optional[Union["TriggerExpr", AtomicCondition]] = Field(default=None, alias="not")

    @model_validator(mode="after")
    def validate_non_empty(self) -> "TriggerExpr":
        if not self.all_of and not self.any_of and self.not_ is None:
            raise ValueError("TriggerExpr must contain at least one logical branch")
        return self


TriggerExpr.model_rebuild()


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str
    trigger: TriggerExpr
    enable_subgraphs: List[str] = Field(default_factory=list)
    disable_edges: List[str] = Field(default_factory=list)
    cooldown_steps: int = Field(default=0, ge=0)
    max_activations: int = Field(default=1, ge=1)


class SubgraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subgraph_id: str
    purpose: str = ""
    nodes: List[NodeSpec]
    edges: List[EdgeSpec]
    entry_nodes: List[str]
    exit_nodes: List[str]
    default_enabled: bool = False

    @field_validator("nodes")
    @classmethod
    def validate_unique_nodes(cls, value: List[NodeSpec]) -> List[NodeSpec]:
        node_ids = [node.node_id for node in value]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("subgraph contains duplicate node IDs")
        return value

    @field_validator("edges")
    @classmethod
    def validate_unique_edges(cls, value: List[EdgeSpec]) -> List[EdgeSpec]:
        edge_ids = [edge.edge_id for edge in value]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("subgraph contains duplicate edge IDs")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> "SubgraphSpec":
        node_ids = {node.node_id for node in self.nodes}
        for edge in self.edges:
            if edge.src not in node_ids or edge.dst not in node_ids:
                raise ValueError(f"subgraph edge {edge.edge_id} references missing node")
        for node_id in self.entry_nodes + self.exit_nodes:
            if node_id not in node_ids:
                raise ValueError(f"subgraph references unknown boundary node {node_id}")
        return self


class HardCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    description: str
    sandbox: Optional[SandboxSpec] = Field(default=None)
    cmd: List[str]
    timeout_s: int = Field(default=120, ge=1)
    expected_exit_codes: List[int] = Field(default_factory=lambda: [0])
    produces: List[str] = Field(default_factory=list)


class SoftJudge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge_id: str
    description: str
    rubric: str
    model: ModelSpec
    n_votes: int = Field(default=3, ge=1, le=9)
    aggregation: Literal["mean", "median", "majority", "min"] = "median"
    scale: Literal["0-1", "1-5", "1-10"] = "1-10"
    pass_threshold: float = Field(default=7.0, ge=0)


class TraceAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    description: str
    expr: str


class EvalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard_checks: List[HardCheck] = Field(default_factory=list)
    soft_judges: List[SoftJudge] = Field(default_factory=list)
    trace_assertions: List[TraceAssertion] = Field(default_factory=list)


class PatchOpType(str, Enum):
    replace_model = "ReplaceModel"
    swap_tool = "SwapTool"
    insert_node = "InsertNode"
    remove_node = "RemoveNode"
    insert_edge = "InsertEdge"
    remove_edge = "RemoveEdge"
    add_gate = "AddGate"
    prune_subgraph = "PruneSubgraph"
    tighten_contract = "TightenContract"
    relax_contract = "RelaxContract"
    change_sandbox = "ChangeSandbox"
    add_hard_check = "AddHardCheck"
    add_soft_judge = "AddSoftJudge"


class PatchOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: PatchOpType
    target: Optional[str] = Field(default=None)
    params: JsonDict = Field(default_factory=dict)
    reason: str = ""


class PatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    ops: List[PatchOp]
    expected_effect: str = ""
    risk: Literal["low", "medium", "high"] = "low"
    estimated_delta_usd: Optional[float] = Field(default=None)


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str
    description: str
    inputs: List[ArtifactRef] = Field(default_factory=list)
    hints: JsonDict = Field(default_factory=dict)


class WorkflowBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ir_version: str = "0.1.0"
    task: TaskSpec
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    mcp_servers: List[MCPServerRef] = Field(default_factory=list)
    base_nodes: List[NodeSpec]
    base_edges: List[EdgeSpec]
    subgraphs: List[SubgraphSpec] = Field(default_factory=list)
    gates: List[GateSpec] = Field(default_factory=list)
    eval: EvalSpec = Field(default_factory=EvalSpec)
    tags: Set[str] = Field(default_factory=set)
    meta: JsonDict = Field(default_factory=dict)

    @field_validator("base_nodes")
    @classmethod
    def validate_unique_base_nodes(cls, value: List[NodeSpec]) -> List[NodeSpec]:
        node_ids = [node.node_id for node in value]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate base node IDs")
        return value

    @field_validator("base_edges")
    @classmethod
    def validate_unique_base_edges(cls, value: List[EdgeSpec]) -> List[EdgeSpec]:
        edge_ids = [edge.edge_id for edge in value]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate base edge IDs")
        return value

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> "WorkflowBlueprint":
        base_ids = {node.node_id for node in self.base_nodes}
        for edge in self.base_edges:
            if edge.src not in base_ids or edge.dst not in base_ids:
                raise ValueError(f"base edge {edge.edge_id} references unknown node")
        subgraph_ids = {subgraph.subgraph_id for subgraph in self.subgraphs}
        if len(subgraph_ids) != len(self.subgraphs):
            raise ValueError("duplicate subgraph IDs")
        for gate in self.gates:
            for subgraph_id in gate.enable_subgraphs:
                if subgraph_id not in subgraph_ids:
                    raise ValueError(f"gate {gate.gate_id} references unknown subgraph {subgraph_id}")
        server_ids = {server.name for server in self.mcp_servers}
        for node in self.all_nodes():
            for tool in node.tools:
                if tool.server not in server_ids:
                    raise ValueError(f"node {node.node_id} references unknown MCP server {tool.server}")
        return self

    def all_nodes(self) -> List[NodeSpec]:
        nodes: List[NodeSpec] = list(self.base_nodes)
        for subgraph in self.subgraphs:
            nodes.extend(subgraph.nodes)
        return nodes

    def all_edges(self) -> List[EdgeSpec]:
        edges: List[EdgeSpec] = list(self.base_edges)
        for subgraph in self.subgraphs:
            edges.extend(subgraph.edges)
        return edges

    def node_map(self) -> Dict[str, NodeSpec]:
        return {node.node_id: node for node in self.all_nodes()}

    def edge_map(self) -> Dict[str, EdgeSpec]:
        return {edge.edge_id: edge for edge in self.all_edges()}

    def subgraph_map(self) -> Dict[str, SubgraphSpec]:
        return {subgraph.subgraph_id: subgraph for subgraph in self.subgraphs}

    def default_active_subgraphs(self) -> Set[str]:
        return {subgraph.subgraph_id for subgraph in self.subgraphs if subgraph.default_enabled}

