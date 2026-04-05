# src/agentcoop/runtime/execution_context.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Set

if TYPE_CHECKING:
    from agentcoop.integrations.mcp_client import MCPClientManager
    from agentcoop.integrations.sandbox import SandboxRunner
    from agentcoop.integrations.tool_registry import ToolRegistry
    from agentcoop.runtime.cache import ArtifactStore
    from agentcoop.runtime.llm import LLMRouter
    from agentcoop.runtime.reports import NodeExecutionResult
    from agentcoop.runtime.skills import SkillRegistry
    from agentcoop.runtime.tool_scout import ToolScout
    from agentcoop.ir.schema import WorkflowBlueprint

JsonDict = Dict[str, Any]


@dataclass
class NodeExecutionContext:
    blueprint: "WorkflowBlueprint"
    artifact_store: "ArtifactStore"
    active_subgraphs: Set[str]
    node_results: Dict[str, "NodeExecutionResult"]
    budget_remaining: JsonDict
    llm_router: "LLMRouter"
    mcp_client: "MCPClientManager"
    tool_registry: "ToolRegistry"
    tool_scout: "ToolScout"
    skill_registry: "SkillRegistry"
    sandbox_runner: "SandboxRunner"
    metadata: JsonDict = field(default_factory=dict)
