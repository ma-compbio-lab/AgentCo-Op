"""AgentCo-Op wrappers — sandboxed external agents.

Importing this package side-effect registers the per-repo local-Python
adapters so the `agentcoop collaborate` CLI can run the case studies in
`--no-docker` mode.

Each subpackage / module ships:
- `manifest.yaml` — typed agent card (capabilities, container, schema).
- `Dockerfile.agentcoop` — container build (used when Docker is available).
- `adapter.py` — local-Python implementation invoked when Docker is off.
"""

from __future__ import annotations

# Side-effect imports register adapters with `repo_collaboration._ADAPTERS`.
from agentcoop.wrappers import tissueagent  # noqa: F401
from agentcoop.wrappers import geneagent_local  # noqa: F401
