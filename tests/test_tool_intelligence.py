from core.contracts import TaskSpec
from core.tool_intelligence import _constraints_block_tools, _extract_tool_hint, _should_search_tools


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
