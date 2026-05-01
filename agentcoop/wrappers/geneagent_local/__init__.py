"""GeneAgent local-Python adapter.

Registers an LLM-driven gene-set interpretation adapter that mirrors the
GeneAgent capability used in case_study_1.md without requiring the full
upstream container. When the Docker daemon is up, the orchestrator
instead invokes the upstream wrapper via `agentcoop/wrappers/geneagent/`.

Kept in a separate `geneagent_local` package so the existing
`agentcoop/wrappers/geneagent/` (Docker-only adapter) is untouched.

The wrapper also declares its required PyPI packages so AgentCo-Op's
EnvManager can install them automatically before invocation.
"""

from agentcoop.core.env_manager import register_required_packages
from agentcoop.wrappers.geneagent_local.adapter import (
    invoke_geneagent_local,
    register,
)

register_required_packages(
    "GeneAgent",
    [
        # The local GeneAgent adapter only needs the in-tree LLM client +
        # standard library, but we list `httpx` explicitly so users in a
        # bare-Python env get it auto-installed if missing.
        "httpx",
    ],
)

register()
