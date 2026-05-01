"""TissueAgent wrapper — registers a local-Python adapter for the
`agentcoop collaborate` CLI in --no-docker mode.

The Docker manifest + Dockerfile are kept alongside this module and are
used whenever the Docker daemon is available; the local-Python adapter
exercises the same logic on the host so the case study is reproducible
without containers.

The wrapper also declares its required PyPI packages so AgentCo-Op's
EnvManager can install them automatically before invocation — the user
does NOT need to run `pip install` themselves.
"""

from agentcoop.core.env_manager import register_required_packages
from agentcoop.wrappers.tissueagent.adapter import (
    invoke_tissueagent_local,
    register,
)

register_required_packages(
    "TissueAgent",
    [
        # Core scientific stack — likely already installed in most envs.
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        # Real h5ad I/O — installed automatically when missing.
        "anndata",
        "scanpy",
        "h5py",
        ("PyYAML", "yaml"),  # YAML I/O for AgentCard manifests.
    ],
)

register()
