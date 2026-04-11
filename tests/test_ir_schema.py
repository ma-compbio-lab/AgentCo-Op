from __future__ import annotations

import pytest

from agentcoop.ir import (
    EdgeSpec,
    MCPServerRef,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    SkillPromptMode,
    SkillRef,
    TaskSpec,
    ToolDiscoveryMode,
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


def test_direct_sandbox_tool_node_may_skip_mcp_server_resolution() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="t2b", title="Task", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="tool-node",
                kind=NodeKind.tool,
                role="Tool Node",
                tools=[ToolRef(server="missing-server", tool="run")],
                meta={"direct_sandbox_handler": True},
            )
        ],
        base_edges=[],
    )

    assert blueprint.node_map()["tool-node"].meta["direct_sandbox_handler"] is True


def test_agent_nodes_require_model() -> None:
    with pytest.raises(ValueError):
        NodeSpec(node_id="agent", kind=NodeKind.agent, role="Planner")


def test_agent_node_may_enable_registry_tool_discovery_without_prebound_tools() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="t2c", title="Task", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                tool_discovery={
                    "mode": ToolDiscoveryMode.registry,
                    "include_web_search": True,
                    "server_allowlist": ["builtin-web-search"],
                },
            )
        ],
        base_edges=[],
    )

    assert blueprint.node_map()["planner"].tool_discovery is not None
    assert blueprint.node_map()["planner"].tool_discovery.mode == ToolDiscoveryMode.registry


def test_skill_ref_requires_name_or_path() -> None:
    with pytest.raises(ValueError):
        SkillRef()


def test_agent_node_accepts_skill_bindings() -> None:
    node = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="Planner",
        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
        skills=[SkillRef(name="structured-json-discipline", prompt_mode=SkillPromptMode.summary)],
    )

    assert node.skills[0].name == "structured-json-discipline"


def test_tool_node_rejects_skill_bindings() -> None:
    with pytest.raises(ValueError):
        NodeSpec(
            node_id="tool-node",
            kind=NodeKind.tool,
            role="Tool Node",
            tools=[ToolRef(server="local-tools", tool="run")],
            skills=[SkillRef(name="structured-json-discipline")],
        )


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


def test_subgraph_edges_may_reference_base_nodes() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="t4", title="Task", description="Desc"),
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
                "edges": [
                    EdgeSpec(edge_id="plan_to_review", src="planner", dst="reviewer"),
                ],
                "entry_nodes": ["reviewer"],
                "exit_nodes": ["reviewer"],
                "default_enabled": False,
            }
        ],
    )

    assert blueprint.subgraph_map()["sg_review"].edges[0].src == "planner"


from agentcoop.ir.schema import ReviewPolicy, RetryPolicy, SubgraphSpec


def test_review_policy_defaults_to_verify_then_correct() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.cache, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
    )
    assert sg.review_policy == ReviewPolicy.verify_then_correct


def test_retry_policy_defaults_to_retry_then_escalate() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.cache, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
    )
    assert sg.retry_policy == RetryPolicy.retry_then_escalate


def test_subgraph_accepts_explicit_policies() -> None:
    sg = SubgraphSpec(
        subgraph_id="sg_test",
        purpose="test",
        nodes=[NodeSpec(node_id="n1", kind=NodeKind.cache, role="R")],
        edges=[],
        entry_nodes=["n1"],
        exit_nodes=["n1"],
        review_policy=ReviewPolicy.re_solve,
        retry_policy=RetryPolicy.escalate_immediately,
        max_retries=2,
    )
    assert sg.review_policy == ReviewPolicy.re_solve
    assert sg.retry_policy == RetryPolicy.escalate_immediately
    assert sg.max_retries == 2
