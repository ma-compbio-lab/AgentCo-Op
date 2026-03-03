from __future__ import annotations

import pytest

from dynaforge.ir import (
    EdgeSpec,
    MCPServerRef,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    TaskSpec,
    ToolRef,
    Transport,
    WorkflowBlueprint,
)


def test_valid_blueprint_with_tool_binding() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="t1", title="Task", description="Desc"),
        mcp_servers=[
            MCPServerRef(
                name="local-tools",
                transport=Transport.stdio,
                stdio_cmd=["python", "server.py"],
            )
        ],
        base_nodes=[
            NodeSpec(
                node_id="tool-node",
                kind=NodeKind.tool,
                role="Tool Node",
                tools=[ToolRef(server="local-tools", tool="run")],
            )
        ],
        base_edges=[],
    )

    assert blueprint.node_map()["tool-node"].tools[0].tool == "run"


def test_invalid_blueprint_rejects_unknown_server() -> None:
    with pytest.raises(ValueError):
        WorkflowBlueprint(
            task=TaskSpec(task_id="t2", title="Task", description="Desc"),
            base_nodes=[
                NodeSpec(
                    node_id="tool-node",
                    kind=NodeKind.tool,
                    role="Tool Node",
                    tools=[ToolRef(server="missing-server", tool="run")],
                )
            ],
            base_edges=[],
        )


def test_agent_nodes_require_model() -> None:
    with pytest.raises(ValueError):
        NodeSpec(node_id="agent", kind=NodeKind.agent, role="Planner")


def test_subgraph_integrity() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="t3", title="Task", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            )
        ],
        base_edges=[],
        subgraphs=[
            {
                "subgraph_id": "sg_review",
                "purpose": "Review path",
                "nodes": [
                    NodeSpec(
                        node_id="reviewer",
                        kind=NodeKind.evaluator,
                        role="Reviewer",
                        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                    )
                ],
                "edges": [],
                "entry_nodes": ["reviewer"],
                "exit_nodes": ["reviewer"],
                "default_enabled": False,
            }
        ],
    )

    assert blueprint.subgraph_map()["sg_review"].purpose == "Review path"

