from pathlib import Path

import pytest

from core.local_exec import ExecConfig, LocalExecutor, WriteApproval


def build_executor(workspace: Path, *, require_approval: bool) -> LocalExecutor:
    config = ExecConfig(
        enabled=True,
        workspace_root=str(workspace),
        require_approval=require_approval,
        max_output_chars=2000,
    )
    approval = WriteApproval(workspace)
    return LocalExecutor(config, approval)


def test_read_outside_workspace_allowed(tmp_path, tmp_path_factory):
    workspace = tmp_path
    outside = tmp_path_factory.mktemp("outside")
    sample = outside / "sample.txt"
    sample.write_text("hello", encoding="utf-8")

    executor = build_executor(workspace, require_approval=True)
    result = executor.execute(f"cat {sample}")
    assert "hello" in result["stdout"]


def test_write_outside_workspace_blocked(tmp_path, tmp_path_factory):
    workspace = tmp_path
    outside = tmp_path_factory.mktemp("outside2") / "blocked.txt"

    executor = build_executor(workspace, require_approval=False)
    with pytest.raises(PermissionError):
        executor.execute(f"touch {outside}")


def test_write_inside_workspace_allowed_without_prompt(tmp_path):
    workspace = tmp_path
    target = workspace / "ok.txt"

    executor = build_executor(workspace, require_approval=False)
    result = executor.execute(f"touch {target}")
    assert result["returncode"] == 0
    assert target.exists()
