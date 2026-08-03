"""Seurat local-Python adapter.

Mirrors the R wrapper specified in `docs/experiments/case_study_2.md` §10 using
scanpy + numpy + pandas in Python. The Docker manifest + Dockerfile
are kept alongside this module and used whenever the Docker daemon is
available; this local-Python adapter exercises the same logic on the
host so the case study is reproducible without containers.

The wrapper declares its required PyPI packages so AgentCo-Op's
EnvManager can install them automatically before invocation — the user
does NOT need to run `pip install` themselves.
"""

from agentcoop.core.env_manager import register_required_packages
from agentcoop.wrappers.seurat_local.adapter import (
    invoke_seurat_local,
    register,
)

register_required_packages(
    "Seurat",
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
