from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from agentcoop.case_studies.scanpy_visium_cluster_job import DEFAULT_ANALYSIS_CONFIG, run_pipeline


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


mcp = FastMCP(
    name=os.environ.get("MCP_SERVER_NAME", "scanpy-visium-cluster"),
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVER_NAME", "scanpy-visium-cluster"),
        "domain": "spatial-transcriptomics",
        "case_study": "visium_multi_agent_collaboration",
        "outputs": ["cluster UMAP PNG", "marker heatmap PNG", "annotated h5ad", "summary JSON"],
        "default_analysis_config": DEFAULT_ANALYSIS_CONFIG,
    }


@mcp.tool()
def generate_clusters(
    task: Dict[str, Any],
    workflow_meta: Optional[Dict[str, Any]] = None,
    selected_repo: str = "scanpy",
    analysis_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    hints = dict(task.get("hints", {}))
    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            summary = run_pipeline(
                output_dir=hints["output_dir"],
                cluster_figure_path=hints["cluster_figure_path"],
                marker_figure_path=hints["marker_figure_path"],
                summary_path=hints["cluster_summary_path"],
                annotated_data_path=hints["annotated_data_path"],
                raw_data_path=hints["raw_data_path"],
                selected_repo=selected_repo or "scanpy",
                config=analysis_config or {},
            )
        return {
            "status": "ok",
            "selected_repo": selected_repo,
            "summary": summary,
            "workflow_meta": workflow_meta or {},
        }
    except Exception as exc:  # pragma: no cover
        _log("scanpy-visium-cluster tool error:", repr(exc))
        return {
            "status": "error",
            "error_type": "tool_runtime_error",
            "message": str(exc),
            "selected_repo": selected_repo,
        }


def main() -> None:
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
