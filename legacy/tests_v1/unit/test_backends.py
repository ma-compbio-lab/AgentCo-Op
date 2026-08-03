from __future__ import annotations

import pytest

from agentcoop.backends import (
    HumanReviewBackend,
    LLMBackend,
    MCPBackend,
    MockLLM,
    PythonSandboxBackend,
    RepoSandboxBackend,
    build_run_command,
)
from agentcoop.backends.base import NodeContext
from agentcoop.core.schema import NodeSpec


@pytest.mark.asyncio
async def test_llm_mock_canned() -> None:
    client = MockLLM()
    client.register("solver", {"answer": "221", "confidence": 0.9})
    backend = LLMBackend(client=client)
    node = NodeSpec(node_id="solver", role="solver", backend="llm")
    r = await backend.execute(node, {"q": "13*17"}, NodeContext(run_id="r", task_id="t"))
    assert r.output["answer"] == "221"
    assert r.confidence == 0.9


@pytest.mark.asyncio
async def test_python_sandbox_runs_simple_code() -> None:
    backend = PythonSandboxBackend()
    node = NodeSpec(node_id="c", role="tool", backend="python_sandbox")
    r = await backend.execute(node, {"code": "print(1+1)"}, NodeContext(run_id="r", task_id="t"))
    assert r.ok and "2" in r.output["stdout"]


@pytest.mark.asyncio
async def test_mcp_backend_missing_tool() -> None:
    backend = MCPBackend()
    node = NodeSpec(node_id="x", role="tool", backend="mcp", params={"tool": "notfound"})
    r = await backend.execute(node, {}, NodeContext(run_id="r", task_id="t"))
    assert not r.ok and "notfound" in " ".join(r.errors)


@pytest.mark.asyncio
async def test_repo_sandbox_dry_run() -> None:
    backend = RepoSandboxBackend()
    node = NodeSpec(node_id="r", role="sandbox_agent", backend="sandbox_repo")
    manifest = {
        "image": "agentcoop/spatial",
        "commit_sha": "abc",
        "digest": "sha256:abc",
        "network": "none",
    }
    r = await backend.execute(node, {"manifest": manifest}, NodeContext(run_id="r", task_id="t"))
    assert r.ok and r.output["mode"] == "dry_run"
    cmd = build_run_command(
        manifest=manifest,
        inputs_dir=".",
        outputs_dir=".",
        request_path="/inputs/request.json",
        result_path="/outputs/result.json",
    )
    assert "--network" in cmd and "none" in cmd


@pytest.mark.asyncio
async def test_repo_sandbox_rejects_bad_manifest() -> None:
    backend = RepoSandboxBackend()
    node = NodeSpec(node_id="r", role="sandbox_agent", backend="sandbox_repo")
    r = await backend.execute(node, {"manifest": {"image": "x"}}, NodeContext(run_id="r", task_id="t"))
    assert not r.ok


@pytest.mark.asyncio
async def test_human_review_auto_approves() -> None:
    backend = HumanReviewBackend(auto_approve=True)
    node = NodeSpec(node_id="hr", role="human_review", backend="human_review")
    r = await backend.execute(node, {"request": "x"}, NodeContext(run_id="r", task_id="t"))
    assert r.ok
