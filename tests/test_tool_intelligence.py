import asyncio
from types import SimpleNamespace

from config import ToolConfig
from core.contracts import TaskSpec
from core.tool_intelligence import (
    _coerce_candidate,
    _coerce_tool_plan,
    _constraints_block_tools,
    _extract_tool_hint,
    _should_search_tools,
)


def test_extract_tool_hint_from_github():
    task = TaskSpec(goal="Use https://github.com/org/repo for this task.")
    candidate = _extract_tool_hint(task)
    assert candidate is not None
    assert candidate.kind == "github_repo"
    assert candidate.repo_url == "https://github.com/org/repo"


def test_extract_tool_hint_from_pip():
    task = TaskSpec(goal="Install via pip install fastapi and proceed.")
    candidate = _extract_tool_hint(task)
    assert candidate is not None
    assert candidate.kind == "pypi"
    assert candidate.name == "fastapi"


def test_constraints_block_tool_search():
    task = TaskSpec(goal="Do it", constraints=["Do not use external libraries."])
    assert _constraints_block_tools(task.constraints) is True
    assert _should_search_tools(task) is False


def test_allow_tool_search_on_overrides_auto():
    task = TaskSpec(goal="Just solve", allow_tool_search="on")
    assert _should_search_tools(task) is True


def test_coerce_candidate_accepts_repo_alias():
    candidate = _coerce_candidate(
        {
            "kind": "repo",
            "name": "org/example",
            "repo_url": "https://github.com/org/example",
        }
    )
    assert candidate is not None
    assert candidate.kind == "github_repo"


def test_coerce_tool_plan_handles_null_limits():
    fallback = _coerce_candidate(
        {
            "kind": "pypi",
            "name": "requests",
        }
    )
    assert fallback is not None
    plan = _coerce_tool_plan(
        {
            "install_strategy": "pip",
            "container_spec": {"base_image": "python:3.10-slim", "limits": None},
            "run_commands": ["python -V"],
        },
        fallback=fallback,
    )
    assert plan is not None
    assert plan.selected_tool.name == "requests"
    assert plan.container_spec.limits is not None


def test_prepare_tool_plan_skips_when_runtime_unavailable():
    from core.tool_intelligence import prepare_tool_plan

    task = TaskSpec(
        goal="Install via pip install requests and use it.",
        allow_tool_search="on",
    )
    ctx = SimpleNamespace(
        docker_runtime=None,
        cache=None,
        mcp_manager=None,
        memory=None,
    )
    result = asyncio.run(
        prepare_tool_plan(
            task,
            ctx,
            agent_pool={},
            tool_cfg=ToolConfig(enabled=True, use_docker=True),
        )
    )
    assert result is None
