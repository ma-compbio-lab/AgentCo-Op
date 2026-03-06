from __future__ import annotations

import types

from dynaforge.ir import (
    BudgetSpec,
    MCPServerRef,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    Repo2RunSpec,
    SandboxSpec,
    TaskSpec,
    WorkflowBlueprint,
)
from dynaforge.integrations.sandbox import DockerSandboxRunner
from dynaforge.runtime.executor import BlueprintExecutor
from dynaforge.runtime.llm import LLMRouter
from dynaforge.runtime.validation import validate_json_payload


class StubLLMClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def complete(self, model, messages, *, response_format=None):
        self.calls.append(
            {
                "model": model.name,
                "messages": messages,
                "response_format": response_format,
            }
        )
        return self.response


class StubMCPClient:
    def __init__(self):
        self.calls = []

    def call_bound_tool(self, bound_tools, requested_tool, arguments, *, blueprint, sandbox_runner=None):
        self.calls.append(
            {
                "requested_tool": requested_tool,
                "arguments": arguments,
                "task_id": blueprint.task.task_id,
            }
        )
        return {"echo": arguments, "tool": requested_tool}

    def describe_tools(self, bound_tools, *, blueprint, sandbox_runner=None):
        return [{"name": f"{tool.server}:{tool.tool}", "server": tool.server, "tool": tool.tool} for tool in bound_tools]


def test_validate_json_payload_handles_nested_schema() -> None:
    schema = {
        "type": "object",
        "required": ["result"],
        "properties": {
            "result": {
                "type": "object",
                "required": ["score"],
                "properties": {
                    "score": {"type": "number"},
                },
            }
        },
    }

    errors = validate_json_payload(schema, {"result": {"score": "bad"}})

    assert errors
    assert "number" in errors[0]


def test_executor_uses_stub_llm_router_for_agent_nodes() -> None:
    stub = StubLLMClient(
        response={
            "content": '{"output":{"result":{"ok":true}},"confidence":0.91,"summary":"done"}',
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }
    )
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=stub, allow_offline_fallback=False),
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="live-llm", title="LLM", description="Desc"),
        budget=BudgetSpec(max_total_usd=2.0),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                io={"output_schema": {"type": "object", "required": ["result"]}},
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert stub.calls
    assert report.traces[0].trace["live_llm"] is True


def test_executor_uses_mcp_client_for_tool_nodes() -> None:
    mcp_client = StubMCPClient()
    executor = BlueprintExecutor(mcp_client=mcp_client)
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="live-mcp", title="MCP", description="Desc"),
        mcp_servers=[
            {
                "name": "local-tools",
                "transport": "stdio",
                "stdio_cmd": ["python", "server.py"],
            }
        ],
        base_nodes=[
            NodeSpec(
                node_id="tool",
                kind=NodeKind.tool,
                role="Tool",
                tools=[{"server": "local-tools", "tool": "run"}],
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert mcp_client.calls[0]["requested_tool"] == "local-tools:run"
    assert report.node_results["tool"].outputs["result"]["tool"] == "local-tools:run"


def test_trace_assertions_support_richer_expressions() -> None:
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="trace-eval", title="Trace", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
            )
        ],
        base_edges=[],
        eval={
            "trace_assertions": [
                {
                    "assertion_id": "a1",
                    "description": "Check successful count and tokens",
                    "expr": "len(trace.successful_nodes) == 1 and trace.node_costs['planner']['output_tokens'] >= 0",
                }
            ]
        },
    )

    report = BlueprintExecutor().execute_blueprint(blueprint)

    assert report.success


def test_docker_sandbox_runner_invokes_repo2run_and_docker(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, capture_output, text, check, timeout=None, env=None):
        calls.append({"cmd": list(cmd), "check": check, "env": env})
        stdout = "container-123\n" if list(cmd)[:3] == ["docker", "run", "-d"] else ""
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr("dynaforge.integrations.sandbox.subprocess.run", fake_run)

    runner = DockerSandboxRunner()
    sandbox = SandboxSpec(
        sandbox_id="bio",
        image="dynaforge:bio",
        cmd=["python", "server.py"],
        repo2run=Repo2RunSpec(
            source="https://example.com/repo.git",
            build_cmd=["repo2run", "https://example.com/repo.git"],
            image_tag="dynaforge:bio",
        ),
    )

    materialized = runner.ensure_materialized(sandbox)
    runner.run_in_sandbox(sandbox, ["python", "-c", "print('ok')"])
    server_ref = runner.materialize_server_ref(
        MCPServerRef(
            name="toolbox",
            transport="stdio",
            sandbox=sandbox,
            stdio_cmd=["python", "server.py"],
        )
    )

    assert materialized.container_id == "container-123"
    assert calls[0]["cmd"] == ["repo2run", "https://example.com/repo.git"]
    assert calls[1]["cmd"][:3] == ["docker", "run", "-d"]
    assert calls[2]["cmd"][:2] == ["docker", "exec"]
    assert server_ref.stdio_cmd[:4] == ["docker", "exec", "-i", "dynaforge-bio"]
