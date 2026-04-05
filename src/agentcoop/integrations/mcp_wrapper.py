from __future__ import annotations


FASTMCP_SERVER_TEMPLATE = """from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


mcp = FastMCP(
    name=os.environ.get("MCP_SERVER_NAME", "specialized-agent"),
    stateless_http=True,
    json_response=True,
)


def agent_run(task: str, inputs: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ok",
        "summary": f"Handled task: {task[:80]}",
        "outputs": [],
        "trace": {"steps": 1},
    }


@mcp.tool()
def describe_capabilities() -> Dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVER_NAME", "specialized-agent"),
        "domain": os.environ.get("AGENT_DOMAIN", "bio"),
        "inputs": ["ArtifactRef[]", "free-text task"],
        "outputs": ["structured JSON + artifacts"],
    }


@mcp.tool()
def run(task: str, inputs: Optional[List[Dict[str, Any]]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    inputs = inputs or []
    params = params or {}
    try:
        result = agent_run(task=task, inputs=inputs, params=params)
        json.dumps(result)
        return result
    except Exception as exc:
        tb = traceback.format_exc()
        log("Agent error:", repr(exc))
        log(tb)
        return {
            "status": "error",
            "error_type": "tool_runtime_error",
            "message": str(exc),
            "traceback": tb,
        }


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
"""


def render_fastmcp_server_template() -> str:
    return FASTMCP_SERVER_TEMPLATE

