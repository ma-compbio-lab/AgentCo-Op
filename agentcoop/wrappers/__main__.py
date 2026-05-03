"""Generic CLI for invoking any registered AgentCo-Op adapter.

Used by `RepoCollaborationOrchestrator` when running in `--docker` mode:
the orchestrator drops a JSON request on disk, then `docker run`s the
sandbox image with `python -m agentcoop.wrappers <agent-name> <invoke.json>`.

Reads the request, dispatches to the adapter registered for that agent
(side-effect registered by `agentcoop.wrappers/__init__.py`), writes the
response next to the invoke file as `<stem>.response.json`, and also
echoes the response JSON to stdout so the caller can parse without
re-reading the file.

This module is generic — any Python adapter that registers itself with
`agentcoop.core.repo_collaboration.register_local_adapter` is callable
from the same image.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Side-effect: registers every adapter shipped with the package.
import agentcoop.wrappers  # noqa: F401
from agentcoop.core.repo_collaboration import get_local_adapter


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentcoop.wrappers")
    p.add_argument("agent_name", help="Registered adapter name (e.g. Seurat, Signac, TissueAgent).")
    p.add_argument("invoke_json", type=Path, help="Path to the request JSON.")
    args = p.parse_args(argv)

    adapter = get_local_adapter(args.agent_name)
    if adapter is None:
        resp = {
            "status": "failed",
            "summary": f"no adapter registered for {args.agent_name!r}",
            "artifacts": {},
            "warnings": [f"register_local_adapter({args.agent_name!r}) was never called"],
        }
        print(json.dumps(resp))
        return 2

    invocation = json.loads(args.invoke_json.read_text(encoding="utf-8"))
    response = adapter(invocation)

    out_path = args.invoke_json.with_name(args.invoke_json.stem + ".response.json")
    out_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
    print(json.dumps(response))
    return 0 if (response or {}).get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
