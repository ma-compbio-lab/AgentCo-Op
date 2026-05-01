"""GeneAgent local-Python adapter.

Registers an LLM-driven gene-set interpretation adapter that mirrors the
GeneAgent capability used in case_study_1.md without requiring the full
upstream container. When the Docker daemon is up, the orchestrator
instead invokes the upstream wrapper via `agentcoop/wrappers/geneagent/`.

Kept in a separate `geneagent_local` package so the existing
`agentcoop/wrappers/geneagent/` (Docker-only adapter) is untouched.
"""

from agentcoop.wrappers.geneagent_local.adapter import (
    invoke_geneagent_local,
    register,
)

register()
