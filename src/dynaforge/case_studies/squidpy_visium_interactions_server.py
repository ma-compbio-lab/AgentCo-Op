from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from dynaforge.case_studies.squidpy_visium_interactions_job import DEFAULT_ANALYSIS_CONFIG, run_pipeline


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


mcp = FastMCP(
    name=os.environ.get("MCP_SERVER_NAME", "squidpy-visium-interactions"),
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVER_NAME", "squidpy-visium-interactions"),
        "domain": "spatial-transcriptomics",
        "case_study": "squidpy_visium_hne_interactions",
        "candidate_repos": ["squidpy", "scanpy"],
        "outputs": ["Neighborhood enrichment PNG", "interaction summary JSON", "raw h5ad snapshot"],
        "default_analysis_config": DEFAULT_ANALYSIS_CONFIG,
    }


@mcp.tool()
def generate_interaction_report(
    task: Dict[str, Any],
    workflow_meta: Optional[Dict[str, Any]] = None,
    selected_repo: str = "squidpy",
    analysis_config: Optional[Dict[str, Any]] = None,
    target_figure_description: str = "",
    reference_figure_path: str = "",
    reference_summary_path: str = "",
) -> Dict[str, Any]:
    hints = dict(task.get("hints", {}))
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
            summary = run_pipeline(
                output_dir=hints["output_dir"],
                figure_path=hints["figure_path"],
                interaction_figure_path=hints.get("interaction_figure_path"),
                summary_path=hints["summary_path"],
                raw_data_path=hints.get("raw_data_path"),
                selected_repo=selected_repo or "squidpy",
                config=analysis_config or {},
                figure_title=hints.get("figure_title", "Visium Neighborhood Enrichment"),
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
        _log("squidpy-visium-interactions tool error:", repr(exc))
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
