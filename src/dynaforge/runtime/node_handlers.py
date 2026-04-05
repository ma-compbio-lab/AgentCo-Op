# src/dynaforge/runtime/node_handlers.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from dynaforge.ir.schema import FailureType, NodeKind, NodeSpec
from dynaforge.runtime.direct_sandbox import execute_direct_sandbox_node
from dynaforge.runtime.reports import ExecutionCost, NodeExecutionResult

if TYPE_CHECKING:
    from dynaforge.runtime.execution_context import NodeExecutionContext

JsonDict = Dict[str, Any]


def handle_llm_node(
    node: NodeSpec,
    inputs: JsonDict,
    context: "NodeExecutionContext",
) -> NodeExecutionResult:
    """Handles agent, evaluator, and router nodes via LLMRouter."""
    return context.llm_router.run_node(node, inputs, context)


def handle_tool_node(
    node: NodeSpec,
    inputs: JsonDict,
    context: "NodeExecutionContext",
) -> NodeExecutionResult:
    """Handles tool nodes: direct sandbox or MCP call."""
    if node.meta.get("direct_sandbox_handler"):
        return execute_direct_sandbox_node(node, inputs, context)

    if not node.tools:
        return NodeExecutionResult(
            failure_type=FailureType.tool_runtime_error,
            error=f"Tool node {node.node_id} has no bound tools",
        )

    tool_ref = f"{node.tools[0].server}:{node.tools[0].tool}"
    tool_result = context.mcp_client.call_bound_tool(
        node.tools,
        tool_ref,
        inputs,
        blueprint=context.blueprint,
        sandbox_runner=context.sandbox_runner,
    )
    return NodeExecutionResult(
        outputs={"result": tool_result},
        trace={"live_mcp": True, "server": node.tools[0].server, "tool": node.tools[0].tool},
        cost=ExecutionCost(tool_calls=1),
        confidence=0.9,
    )


def dispatch_node(
    node: NodeSpec,
    inputs: JsonDict,
    context: "NodeExecutionContext",
) -> NodeExecutionResult:
    """Dispatch a node to the appropriate handler. Raises ValueError for unknown or unhandled kinds.

    NodeKind.cache nodes must have a custom handler registered via the handlers dict
    in BlueprintExecutor.execute_blueprint() — they are not handled by the default dispatcher.
    """
    if node.kind in {NodeKind.agent, NodeKind.evaluator, NodeKind.router}:
        return handle_llm_node(node, inputs, context)
    if node.kind == NodeKind.tool:
        return handle_tool_node(node, inputs, context)
    raise ValueError(
        f"No default handler for NodeKind '{node.kind.value}' (node_id={node.node_id!r}). "
        "Register a custom handler via BlueprintExecutor.execute_blueprint(handlers=...)."
    )
