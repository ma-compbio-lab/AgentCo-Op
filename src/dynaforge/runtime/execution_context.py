# src/dynaforge/runtime/execution_context.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Set

if TYPE_CHECKING:
    from dynaforge.integrations.mcp_client import MCPClientManager
    from dynaforge.integrations.sandbox import SandboxRunner
    from dynaforge.integrations.tool_registry import ToolRegistry
    from dynaforge.runtime.cache import ArtifactStore
    from dynaforge.runtime.llm import LLMRouter
    from dynaforge.runtime.reports import NodeExecutionResult
    from dynaforge.runtime.skills import SkillRegistry
    from dynaforge.runtime.tool_scout import ToolScout
    from dynaforge.ir.schema import WorkflowBlueprint

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
