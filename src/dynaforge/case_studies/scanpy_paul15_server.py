from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from dynaforge.case_studies.scanpy_paul15_job import DEFAULT_ANALYSIS_CONFIG, run_pipeline


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


mcp = FastMCP(
    name=os.environ.get("MCP_SERVER_NAME", "scanpy-paul15"),
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVER_NAME", "scanpy-paul15"),
        "domain": "single-cell",
        "case_study": "paul15_trajectory",
        "candidate_repos": ["scanpy", "scanpy-tutorials"],
        "outputs": [
            "trajectory figure PNG",
            "PAGA graph PNG",
            "dynamic gene trends PNG",
            "summary JSON",
            "raw h5ad snapshot",
        ],
        "default_analysis_config": DEFAULT_ANALYSIS_CONFIG,
    }


@mcp.tool()
def generate_trajectory(
    task: Dict[str, Any],
    workflow_meta: Optional[Dict[str, Any]] = None,
    selected_repo: str = "scanpy",
    analysis_config: Optional[Dict[str, Any]] = None,
    target_figure_description: str = "",
    reference_figure_path: str = "",
    reference_summary_path: str = "",
) -> Dict[str, Any]:
    hints = dict(task.get("hints", {}))
    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            summary = run_pipeline(
                output_dir=hints["output_dir"],
                figure_path=hints["figure_path"],
                paga_figure_path=hints["paga_figure_path"],
                gene_trend_figure_path=hints.get("gene_trend_figure_path"),
                summary_path=hints["summary_path"],
                raw_data_path=hints.get("raw_data_path"),
                selected_repo=selected_repo or "scanpy",
                config=analysis_config or {},
                figure_title=hints.get("figure_title", "Paul15 Trajectory"),
                paga_title=hints.get("paga_title", "Paul15 PAGA Graph"),
            )
        return {
            "status": "ok",
            "selected_repo": selected_repo,
            "target_figure_description": target_figure_description,
            "reference_figure_path": reference_figure_path,
            "reference_summary_path": reference_summary_path,
            "summary": summary,
            "workflow_meta": workflow_meta or {},
        }
    except Exception as exc:  # pragma: no cover - exercised in live runs
        _log("scanpy-paul15 tool error:", repr(exc))
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
