from __future__ import annotations

import os
import sys
from typing import Any, Dict

from dynaforge.integrations.web_search import DuckDuckGoWebSearchClient


def _log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("Install the dynaforge[mcp] extra to run the builtin web-search MCP server") from exc

    client = DuckDuckGoWebSearchClient()
    mcp = FastMCP(
        name=os.environ.get("MCP_SERVER_NAME", "builtin-web-search"),
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
        _log("web_search", query)
        return client.search(query, max_results=max_results)

    @mcp.tool()
    def fetch_page(url: str, max_chars: int = 4000) -> Dict[str, Any]:
        _log("fetch_page", url)
        return client.fetch_page(url, max_chars=max_chars)

    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
