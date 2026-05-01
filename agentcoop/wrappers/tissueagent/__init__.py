"""TissueAgent wrapper — registers a local-Python adapter for the
`agentcoop collaborate` CLI in --no-docker mode.

The Docker manifest + Dockerfile are kept alongside this module and are
used whenever the Docker daemon is available; the local-Python adapter
exercises the same logic on the host so the case study is reproducible
without containers.
"""

from agentcoop.wrappers.tissueagent.adapter import (
    invoke_tissueagent_local,
    register,
)

register()
