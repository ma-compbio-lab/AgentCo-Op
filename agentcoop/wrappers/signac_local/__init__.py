"""Signac local-Python adapter.

Mirrors the R wrapper specified in `docs/experiments/case_study_2.md` §11 using
scanpy + scipy + a small lazily-fetched mm10 gene-coordinate cache.
The Docker manifest + Dockerfile are kept alongside this module and
used whenever the Docker daemon is available; this local-Python
adapter exercises the same logic on the host so the case study is
reproducible without containers.

The wrapper declares its required PyPI packages so AgentCo-Op's
EnvManager can install them automatically before invocation.
"""

from agentcoop.core.env_manager import register_required_packages
from agentcoop.wrappers.signac_local.adapter import (
    invoke_signac_local,
    register,
)

register_required_packages(
    "Signac",
    [
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "anndata",
        "scanpy",
        ("PyYAML", "yaml"),
    ],
)

register()
