from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from dynaforge.case_studies.visium_multi_agent_monolith_job import run_pipeline


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


mcp = FastMCP(
    name=os.environ.get("MCP_SERVER_NAME", "visium-multi-agent-monolith"),
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVER_NAME", "visium-multi-agent-monolith"),
        "domain": "spatial-transcriptomics",
        "case_study": "visium_multi_agent_monolith",
        "outputs": ["cluster figures", "spatial figures", "combined summary JSON"],
    }


@mcp.tool()
def generate_joint_report(
    task: Dict[str, Any],
    workflow_meta: Optional[Dict[str, Any]] = None,
    scanpy_repo: str = "scanpy",
    squidpy_repo: str = "squidpy",
    cluster_config: Optional[Dict[str, Any]] = None,
    interaction_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    hints = dict(task.get("hints", {}))
    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            summary = run_pipeline(
                output_dir=hints["output_dir"],
                cluster_figure_path=hints["cluster_figure_path"],
                marker_figure_path=hints["marker_figure_path"],
                cluster_summary_path=hints["cluster_summary_path"],
                annotated_data_path=hints["annotated_data_path"],
                spatial_figure_path=hints["spatial_figure_path"],
                interaction_figure_path=hints["interaction_figure_path"],
                interaction_summary_path=hints["interaction_summary_path"],
                summary_path=hints["summary_path"],
                raw_data_path=hints["raw_data_path"],
                scanpy_repo=scanpy_repo or "scanpy",
                squidpy_repo=squidpy_repo or "squidpy",
                cluster_config=cluster_config or {},
                interaction_config=interaction_config or {},
            )
        return {
            "status": "ok",
            "scanpy_repo": scanpy_repo,
            "squidpy_repo": squidpy_repo,
            "summary": summary,
            "workflow_meta": workflow_meta or {},
        }
    except Exception as exc:  # pragma: no cover
        _log("visium-multi-agent-monolith tool error:", repr(exc))
        return {
            "status": "error",
            "error_type": "tool_runtime_error",
            "message": str(exc),
            "scanpy_repo": scanpy_repo,
            "squidpy_repo": squidpy_repo,
        }


def main() -> None:
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
