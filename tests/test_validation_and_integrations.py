from __future__ import annotations

import json
import subprocess
import sys
import types
import urllib.parse
import venv
from pathlib import Path

from agentcoop.ir import (
    BudgetSpec,
    MCPServerRef,
    ModelProvider,
    ModelSpec,
    NodeKind,
    NodeSpec,
    Repo2RunSpec,
    SandboxBackend,
    SandboxSpec,
    SkillRef,
    TaskSpec,
    ToolDiscoveryMode,
    ToolRef,
    WorkflowBlueprint,
)
from agentcoop.integrations.web_search import BUILTIN_WEB_SEARCH_SERVER, DuckDuckGoWebSearchClient
from agentcoop.integrations.sandbox import DockerSandboxRunner, LocalVenvSandboxRunner, SandboxError
from agentcoop.integrations.tool_registry import ToolCandidate
from agentcoop.runtime.executor import BlueprintExecutor
from agentcoop.runtime.llm import LLMRouter
from agentcoop.runtime.skills import SkillRegistry
from agentcoop.runtime.tool_scout import ToolScout
from agentcoop.runtime.validation import validate_json_payload


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


class SequencedLLMClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls = []

    def complete(self, model, messages, *, response_format=None):
        self.calls.append(
            {
                "model": model.name,
                "messages": messages,
                "response_format": response_format,
            }
        )
        if not self.responses:
            raise AssertionError("No more stub LLM responses configured")
        return self.responses.pop(0)


class StubMCPClient:
    def __init__(self):
        self.calls = []
        self.list_calls = []

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

    def list_tools(self, server_ref, *, sandbox_runner=None):
        self.list_calls.append(server_ref.name)
        if server_ref.name == BUILTIN_WEB_SEARCH_SERVER:
            return [
                {
                    "name": f"{BUILTIN_WEB_SEARCH_SERVER}:web_search",
                    "server": BUILTIN_WEB_SEARCH_SERVER,
                    "tool": "web_search",
                    "description": "Search the web for recent or external evidence.",
                    "input_schema": {"type": "object"},
                }
            ]
        return []


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


def test_executor_injects_skills_into_prompt_and_trace(tmp_path) -> None:
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "json_discipline"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: structured-json-discipline\n"
        "description: Always produce schema-aligned JSON.\n"
        "---\n\n"
        "Return valid JSON and preserve required keys.\n",
        encoding="utf-8",
    )

    stub = StubLLMClient(
        response={
            "content": '{"output":{"result":{"ok":true}},"confidence":0.93,"summary":"done"}',
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
        }
    )
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=stub, allow_offline_fallback=False),
        skill_registry=SkillRegistry(search_paths=[skill_root], project_root=tmp_path),
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="skill-llm", title="Skill", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                skills=[SkillRef(path=str(skill_dir / "SKILL.md"))],
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert "Reusable skills are active for this node." in stub.calls[0]["messages"][0]["content"]
    assert "Always produce schema-aligned JSON." in stub.calls[0]["messages"][0]["content"]
    assert report.traces[0].trace["skills"]["applied"][0]["path"].endswith("SKILL.md")


def test_executor_allows_optional_missing_skill(tmp_path) -> None:
    stub = StubLLMClient(
        response={
            "content": '{"output":{"result":{"ok":true}},"confidence":0.82,"summary":"done"}',
            "usage": {"prompt_tokens": 8, "completion_tokens": 5},
        }
    )
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=stub, allow_offline_fallback=False),
        skill_registry=SkillRegistry(search_paths=[], project_root=tmp_path),
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="optional-skill", title="Skill", description="Desc"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                skills=[SkillRef(name="missing-skill", optional=True)],
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert report.traces[0].trace["skills"]["missing_optional"][0]["name"] == "missing-skill"


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


def test_skill_allowed_tools_becomes_runtime_constraint(tmp_path) -> None:
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "calculator_only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: calculator-only\n"
        "description: Use only the calculator tool for this node.\n"
        "allowed_tools:\n"
        "  - local-tools:calculator\n"
        "---\n\n"
        "Use the calculator tool when external tools are needed.\n",
        encoding="utf-8",
    )

    llm_client = SequencedLLMClient(
        responses=[
            {
                "content": '{"action":"call_tool","tool":"local-tools:calculator","arguments":{"expression":"2+2"}}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            },
            {
                "content": '{"output":{"decision":"done"},"confidence":0.84,"summary":"used constrained tool"}',
                "usage": {"prompt_tokens": 12, "completion_tokens": 9},
            },
        ]
    )
    mcp_client = StubMCPClient()
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=llm_client, allow_offline_fallback=False),
        mcp_client=mcp_client,
        skill_registry=SkillRegistry(search_paths=[skill_root], project_root=tmp_path),
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="skill-tool-policy", title="Skill Tool Policy", description="Desc"),
        mcp_servers=[
            {
                "name": "local-tools",
                "transport": "stdio",
                "stdio_cmd": ["python", "server.py"],
            }
        ],
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                tools=[
                    {"server": "local-tools", "tool": "calculator"},
                    {"server": "local-tools", "tool": "web_search"},
                ],
                skills=[SkillRef(path=str(skill_dir / "SKILL.md"))],
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert mcp_client.calls[0]["requested_tool"] == "local-tools:calculator"
    policy = report.traces[0].trace["tool_discovery"]["skill_tool_policy"]
    assert policy["enabled"] is True
    assert policy["post_filter_candidate_count"] == 1
    assert policy["blocked_candidates"][0]["tool"] == "web_search"


def test_skill_allowed_tools_rejects_disallowed_tool_request(tmp_path) -> None:
    class StrictStubMCPClient(StubMCPClient):
        def call_bound_tool(self, bound_tools, requested_tool, arguments, *, blueprint, sandbox_runner=None):
            normalized_allowed = {tool.tool for tool in bound_tools} | {f"{tool.server}:{tool.tool}" for tool in bound_tools}
            if requested_tool not in normalized_allowed:
                raise RuntimeError(f"Tool '{requested_tool}' is not in the node's bound tool set")
            return super().call_bound_tool(
                bound_tools,
                requested_tool,
                arguments,
                blueprint=blueprint,
                sandbox_runner=sandbox_runner,
            )

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "calculator_only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: calculator-only\n"
        "allowed_tools:\n"
        "  - local-tools:calculator\n"
        "---\n\n"
        "Use only calculator.\n",
        encoding="utf-8",
    )

    llm_client = SequencedLLMClient(
        responses=[
            {
                "content": '{"action":"call_tool","tool":"local-tools:web_search","arguments":{"query":"latest"}}',
                "usage": {"prompt_tokens": 9, "completion_tokens": 7},
            }
        ]
    )
    mcp_client = StrictStubMCPClient()
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=llm_client, allow_offline_fallback=False),
        mcp_client=mcp_client,
        skill_registry=SkillRegistry(search_paths=[skill_root], project_root=tmp_path),
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="skill-tool-reject", title="Skill Tool Reject", description="Desc"),
        mcp_servers=[
            {
                "name": "local-tools",
                "transport": "stdio",
                "stdio_cmd": ["python", "server.py"],
            }
        ],
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                tools=[
                    {"server": "local-tools", "tool": "calculator"},
                    {"server": "local-tools", "tool": "web_search"},
                ],
                skills=[SkillRef(path=str(skill_dir / "SKILL.md"))],
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert not report.success
    assert report.failure_type.value == "tool_runtime_error"
    assert "not in the node's bound tool set" in (report.node_results["planner"].error or "")


def test_executor_discovers_registry_tools_for_agent_nodes() -> None:
    llm_client = SequencedLLMClient(
        responses=[
            {
                "content": '{"action":"call_tool","tool":"builtin-web-search:web_search","arguments":{"query":"latest treatment guidelines"}}',
                "usage": {"prompt_tokens": 12, "completion_tokens": 10},
            },
            {
                "content": '{"output":{"decision":"done"},"confidence":0.83,"summary":"used discovered search tool"}',
                "usage": {"prompt_tokens": 14, "completion_tokens": 11},
            },
        ]
    )
    mcp_client = StubMCPClient()
    executor = BlueprintExecutor(
        llm_router=LLMRouter(client=llm_client, allow_offline_fallback=False),
        mcp_client=mcp_client,
        allow_offline_fallback=False,
    )
    blueprint = WorkflowBlueprint(
        task=TaskSpec(task_id="discover-tools", title="Discovery", description="Need current evidence"),
        base_nodes=[
            NodeSpec(
                node_id="planner",
                kind=NodeKind.agent,
                role="Planner",
                description="Use the web when the task needs current external evidence.",
                model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
                tool_discovery={
                    "mode": ToolDiscoveryMode.registry,
                    "include_web_search": True,
                    "server_allowlist": [BUILTIN_WEB_SEARCH_SERVER],
                    "max_candidate_tools": 3,
                },
            )
        ],
        base_edges=[],
    )

    report = executor.execute_blueprint(blueprint)

    assert report.success
    assert BUILTIN_WEB_SEARCH_SERVER in mcp_client.list_calls
    assert mcp_client.calls[0]["requested_tool"] == "builtin-web-search:web_search"
    assert report.traces[0].trace["tool_discovery"]["enabled"] is True
    assert report.traces[0].trace["tool_loop"][0]["tool"] == "builtin-web-search:web_search"


def test_tool_scout_uses_skill_text_to_rank_web_search_higher() -> None:
    scout = ToolScout()
    node = NodeSpec(
        node_id="planner",
        kind=NodeKind.agent,
        role="Planner",
        model=ModelSpec(provider=ModelProvider.openai, name="gpt-4.1-mini"),
        description="Neutral planning node",
        tool_discovery={"mode": ToolDiscoveryMode.registry, "max_candidate_tools": 1},
    )
    candidates = [
        ToolCandidate(
            ref=ToolRef(server="local", tool="calculator"),
            descriptor={"name": "local:calculator", "description": "Compute arithmetic expressions."},
            source="registry",
        ),
        ToolCandidate(
            ref=ToolRef(server=BUILTIN_WEB_SEARCH_SERVER, tool="web_search"),
            descriptor={"name": "builtin-web-search:web_search", "description": "Search the web for recent evidence."},
            source="registry",
        ),
    ]

    selected, summary = scout.select_candidates(
        node,
        {"task": {"title": "Route selection", "description": "Pick a tool."}},
        candidates,
        discovery=node.tool_discovery,
        skill_text="Use web search to gather the latest external evidence before answering.",
    )

    assert selected[0].ref.server == BUILTIN_WEB_SEARCH_SERVER
    assert summary["search_intent"] is True


def test_duckduckgo_web_search_client_parses_html_results(monkeypatch) -> None:
    html_body = """
    <html>
      <body>
        <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpaper">Example Paper</a>
        <div class="result__snippet">A recent paper summary.</div>
      </body>
    </html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return html_body.encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=0: FakeResponse())

    client = DuckDuckGoWebSearchClient()
    payload = client.search("example query", max_results=3)

    assert payload["engine"] == "duckduckgo_html"
    assert payload["results"][0]["title"] == "Example Paper"
    assert payload["results"][0]["url"] == "https://example.com/paper"
    assert payload["results"][0]["snippet"] == "A recent paper summary."


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

    monkeypatch.setattr("agentcoop.integrations.sandbox.subprocess.run", fake_run)

    runner = DockerSandboxRunner()
    sandbox = SandboxSpec(
        sandbox_id="bio",
        image="agentcoop:bio",
        cmd=["python", "server.py"],
        repo2run=Repo2RunSpec(
            source="https://example.com/repo.git",
            build_cmd=["repo2run", "https://example.com/repo.git"],
            image_tag="agentcoop:bio",
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
    assert server_ref.stdio_cmd[:4] == ["docker", "exec", "-i", "agentcoop-bio"]


def test_local_venv_sandbox_runner_materializes_repo_and_executes(tmp_path) -> None:
    repo_dir = tmp_path / "demo_repo"
    package_dir = repo_dir / "demo_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (repo_dir / "setup.py").write_text(
        "from setuptools import setup, find_packages\n"
        "setup(name='demo-pkg', version='0.0.1', packages=find_packages())\n",
        encoding="utf-8",
    )
    script_path = tmp_path / "echo_env.py"
    script_path.write_text(
        "import os\n"
        "print(os.environ.get('DYNaforge_SANDBOX_REPO', ''))\n",
        encoding="utf-8",
    )

    runner = LocalVenvSandboxRunner(sandbox_root=tmp_path / "sandboxes", project_root=tmp_path)
    sandbox = SandboxSpec(
        sandbox_id="local-demo",
        image=f"python:{sys.version_info.major}.{sys.version_info.minor}",
        backend=SandboxBackend.local_venv,
        repo2run=Repo2RunSpec(source=str(repo_dir), build_cmd=["python", "-c", "print('noop build')"]),
    )

    materialized = runner.ensure_materialized(sandbox)
    completed = runner.run_in_sandbox(
        sandbox,
        ["python", "-c", "import demo_pkg; print(demo_pkg.VALUE)"],
    )
    server_ref = runner.materialize_server_ref(
        MCPServerRef(
            name="local-toolbox",
            transport="stdio",
            sandbox=sandbox,
            stdio_cmd=["python", str(script_path)],
        )
    )

    assert materialized.backend == SandboxBackend.local_venv
    assert completed.returncode == 0
    assert completed.stdout.strip() == "7"
    assert server_ref.stdio_cmd[0].endswith("python")
    assert server_ref.env["DYNaforge_SANDBOX_REPO"].endswith("repo")


def test_local_venv_sandbox_runner_tolerates_upgrade_bootstrap_failure(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "demo_repo"
    package_dir = repo_dir / "demo_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("VALUE = 3\n", encoding="utf-8")
    (repo_dir / "setup.py").write_text(
        "from setuptools import setup, find_packages\n"
        "setup(name='demo-pkg-upgrade-fail', version='0.0.1', packages=find_packages())\n",
        encoding="utf-8",
    )

    runner = LocalVenvSandboxRunner(sandbox_root=tmp_path / "sandboxes", project_root=tmp_path)
    sandbox = SandboxSpec(
        sandbox_id="local-demo-upgrade-fail",
        image=f"python:{sys.version_info.major}.{sys.version_info.minor}",
        backend=SandboxBackend.local_venv,
        repo2run=Repo2RunSpec(source=str(repo_dir), build_cmd=["python", "-c", "print('noop build')"]),
    )

    original_run_local = LocalVenvSandboxRunner._run_local

    def fake_run_local(self, cmd, *, cwd, check, env=None, timeout=None):
        command = list(cmd)
        if "--upgrade" in command and command[:4][-3:] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(command, 1, "", "offline bootstrap failure")
        return original_run_local(self, command, cwd=cwd, check=check, env=env, timeout=timeout)

    monkeypatch.setattr(LocalVenvSandboxRunner, "_run_local", fake_run_local)

    materialized = runner.ensure_materialized(sandbox)

    assert materialized.backend == SandboxBackend.local_venv
    assert Path(materialized.python_bin).exists()


def test_local_venv_sandbox_manifest_handles_missing_smoke_test_cmd(tmp_path) -> None:
    repo_dir = tmp_path / "demo_repo"
    repo_dir.mkdir(parents=True)

    sandbox = SandboxSpec(
        sandbox_id="local-demo-no-smoke",
        image=f"python:{sys.version_info.major}.{sys.version_info.minor}",
        backend=SandboxBackend.local_venv,
        repo2run=Repo2RunSpec(source=str(repo_dir), smoke_test_cmd=None),
    )

    manifest = LocalVenvSandboxRunner._materialization_manifest(sandbox)

    assert manifest["repo2run"]["source"] == str(repo_dir)
    assert manifest["repo2run"]["build_cmd"] == []
    assert manifest["repo2run"]["smoke_test_cmd"] == []


def test_local_venv_sandbox_manifest_compat_allows_legacy_missing_python_runtime(tmp_path) -> None:
    repo_dir = tmp_path / "demo_repo"
    repo_dir.mkdir(parents=True)
    sandbox = SandboxSpec(
        sandbox_id="legacy-manifest",
        image=f"python:{sys.version_info.major}.{sys.version_info.minor}",
        backend=SandboxBackend.local_venv,
        repo2run=Repo2RunSpec(source=str(repo_dir)),
    )

    expected = LocalVenvSandboxRunner._materialization_manifest(sandbox)
    legacy_manifest = dict(expected)
    legacy_manifest.pop("python_runtime", None)
    manifest_path = tmp_path / "materialization_manifest.json"
    manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")

    assert LocalVenvSandboxRunner._materialization_matches(manifest_path, expected) is True


def test_local_venv_sandbox_identifies_pip_like_commands() -> None:
    assert LocalVenvSandboxRunner._is_pip_like_command(["pip", "install", "mcp"]) is True
    assert LocalVenvSandboxRunner._is_pip_like_command(["/tmp/venv/bin/pip", "install", "mcp"]) is True
    assert LocalVenvSandboxRunner._is_pip_like_command(["python", "-m", "pip", "install", "mcp"]) is True
    assert LocalVenvSandboxRunner._is_pip_like_command(["python", "-m", "agentcoop.case_studies.scanpy_paul15_job"]) is False


def test_local_venv_sandbox_supports_explicit_python_env_runtime(tmp_path) -> None:
    external_env = tmp_path / "external-env"
    venv.EnvBuilder(with_pip=True).create(str(external_env))

    runner = LocalVenvSandboxRunner(sandbox_root=tmp_path / "sandboxes", project_root=tmp_path)
    sandbox = SandboxSpec(
        sandbox_id="explicit-runtime",
        image=f"python-env:{external_env}",
        backend=SandboxBackend.local_venv,
    )

    materialized = runner.ensure_materialized(sandbox)

    assert Path(materialized.python_bin) == LocalVenvSandboxRunner._python_bin(external_env)
    manifest = LocalVenvSandboxRunner._materialization_manifest(sandbox)
    assert manifest["python_runtime"] == f"python-env:{external_env.resolve()}"


def test_local_venv_sandbox_retries_editable_pip_install_without_build_isolation(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "demo_repo"
    repo_dir.mkdir(parents=True)
    sandbox = SandboxSpec(
        sandbox_id="editable-retry",
        image="python:3.13",
        backend=SandboxBackend.local_venv,
        repo2run=Repo2RunSpec(
            source=str(repo_dir),
            build_cmd=["python", "-m", "pip", "install", "-e", "."],
        ),
    )
    runner = LocalVenvSandboxRunner(sandbox_root=tmp_path / "sandboxes", project_root=tmp_path)
    python_bin = tmp_path / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True, exist_ok=True)
    python_bin.write_text("", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run_local(self, cmd, *, cwd, check, env=None, timeout=None):
        calls.append(list(cmd))
        if len(calls) == 1:
            raise SandboxError("Failed to build package while installing build dependencies: setuptools")
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(LocalVenvSandboxRunner, "_run_local", fake_run_local)

    runner._install_repo(tmp_path, repo_dir, sandbox, python_bin)

    assert calls[0] == [str(python_bin), "-m", "pip", "install", "-e", "."]
    assert "--no-build-isolation" in calls[1]
