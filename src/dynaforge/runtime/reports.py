from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from dynaforge.ir.schema import ArtifactRef, BudgetSpec, FailureType

JsonDict = Dict[str, Any]


class ExecutionCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usd: float = Field(default=0.0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_time_s: float = Field(default=0.0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    container_starts: int = Field(default=0, ge=0)

    def add(self, other: "ExecutionCost") -> "ExecutionCost":
        return ExecutionCost(
            usd=self.usd + other.usd,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            wall_time_s=self.wall_time_s + other.wall_time_s,
            tool_calls=self.tool_calls + other.tool_calls,
            container_starts=self.container_starts + other.container_starts,
        )


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remaining_usd: Optional[float] = Field(default=None)
    remaining_tokens: Optional[int] = Field(default=None)
    remaining_wall_time_s: Optional[int] = Field(default=None)
    remaining_tool_calls: Optional[int] = Field(default=None)
    remaining_container_starts: Optional[int] = Field(default=None)


class ContractViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    contract_type: Literal["input_schema", "output_schema", "invariant"]
    message: str


class HardCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    passed: bool
    exit_code: Optional[int] = Field(default=None)
    stdout: str = ""
    stderr: str = ""
    failure_type: FailureType = FailureType.none


class SoftJudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge_id: str
    score: float
    passed: bool
    raw_votes: List[float] = Field(default_factory=list)
    rationale: str = ""


class NodeTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    role: str
    status: Literal["success", "failed", "skipped", "cached"]
    attempt: int = Field(default=1, ge=1)
    inputs: JsonDict = Field(default_factory=dict)
    outputs: JsonDict = Field(default_factory=dict)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    trace: JsonDict = Field(default_factory=dict)
    cost: ExecutionCost = Field(default_factory=ExecutionCost)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    failure_type: FailureType = FailureType.none
    error: str = ""


class NodeExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outputs: JsonDict = Field(default_factory=dict)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    trace: JsonDict = Field(default_factory=dict)
    cost: ExecutionCost = Field(default_factory=ExecutionCost)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    failure_type: FailureType = FailureType.none
    error: str = ""
    cached: bool = False


class BlameCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["node", "edge", "tool", "sandbox", "eval"]
    target_id: str
    score: float = Field(ge=0, le=1)
    reason: str


class ExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    task_id: str
    success: bool
    failure_type: FailureType = FailureType.none
    traces: List[NodeTrace] = Field(default_factory=list)
    hard_checks: List[HardCheckResult] = Field(default_factory=list)
    soft_judges: List[SoftJudgeResult] = Field(default_factory=list)
    contract_violations: List[ContractViolation] = Field(default_factory=list)
    blame_candidates: List[BlameCandidate] = Field(default_factory=list)
    activated_gates: List[str] = Field(default_factory=list)
    active_subgraphs: List[str] = Field(default_factory=list)
    cost: ExecutionCost = Field(default_factory=ExecutionCost)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    summary: str = ""
    node_results: Dict[str, NodeExecutionResult] = Field(default_factory=dict)
    events: List[JsonDict] = Field(default_factory=list)
    meta: JsonDict = Field(default_factory=dict)


class BudgetLedger:
    """Tracks budget consumption and exposes remaining capacity for gates/patching."""

    def __init__(self, spec: BudgetSpec):
        self._spec = spec
        self._consumed = ExecutionCost()

    @property
    def consumed(self) -> ExecutionCost:
        return deepcopy(self._consumed)

    def snapshot(self) -> BudgetSnapshot:
        remaining_tokens: Optional[int] = None
        if self._spec.max_total_tokens is not None:
            remaining_tokens = max(
                self._spec.max_total_tokens - self._consumed.input_tokens - self._consumed.output_tokens,
                0,
            )
        remaining_usd: Optional[float] = None
        if self._spec.max_total_usd is not None:
            remaining_usd = max(self._spec.max_total_usd - self._consumed.usd, 0.0)
        remaining_wall_time_s: Optional[int] = None
        if self._spec.max_wall_time_s is not None:
            remaining_wall_time_s = max(int(self._spec.max_wall_time_s - self._consumed.wall_time_s), 0)
        remaining_tool_calls: Optional[int] = None
        if self._spec.max_tool_calls is not None:
            remaining_tool_calls = max(self._spec.max_tool_calls - self._consumed.tool_calls, 0)
        remaining_container_starts: Optional[int] = None
        if self._spec.max_container_starts is not None:
            remaining_container_starts = max(
                self._spec.max_container_starts - self._consumed.container_starts,
                0,
            )
        return BudgetSnapshot(
            remaining_usd=remaining_usd,
            remaining_tokens=remaining_tokens,
            remaining_wall_time_s=remaining_wall_time_s,
            remaining_tool_calls=remaining_tool_calls,
            remaining_container_starts=remaining_container_starts,
        )

    def consume(self, cost: ExecutionCost) -> None:
        self._consumed = self._consumed.add(cost)

    def exceeds_budget(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.remaining_usd is not None and snapshot.remaining_usd <= 0 and self._spec.max_total_usd is not None:
            return True
        if snapshot.remaining_tokens is not None and snapshot.remaining_tokens <= 0 and self._spec.max_total_tokens is not None:
            return True
        if (
            snapshot.remaining_wall_time_s is not None
            and snapshot.remaining_wall_time_s <= 0
            and self._spec.max_wall_time_s is not None
        ):
            return True
        if (
            snapshot.remaining_tool_calls is not None
            and snapshot.remaining_tool_calls <= 0
            and self._spec.max_tool_calls is not None
        ):
            return True
        if (
            snapshot.remaining_container_starts is not None
            and snapshot.remaining_container_starts <= 0
            and self._spec.max_container_starts is not None
        ):
            return True
        return False
