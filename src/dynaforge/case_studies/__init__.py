"""Case-study helpers for open-ended experiments."""

from dynaforge.case_studies.scanpy_pbmc3k_job import (
    DEFAULT_ANALYSIS_CONFIG as SCANPY_PBMC3K_ANALYSIS_CONFIG,
    run_pipeline as run_scanpy_pbmc3k_pipeline,
)
from dynaforge.case_studies.squidpy_visium_job import (
    DEFAULT_ANALYSIS_CONFIG as SQUIDPY_VISIUM_ANALYSIS_CONFIG,
    run_pipeline as run_squidpy_visium_pipeline,
)

DEFAULT_ANALYSIS_CONFIG = SCANPY_PBMC3K_ANALYSIS_CONFIG
run_pipeline = run_scanpy_pbmc3k_pipeline

__all__ = [
    "DEFAULT_ANALYSIS_CONFIG",
    "SCANPY_PBMC3K_ANALYSIS_CONFIG",
    "SQUIDPY_VISIUM_ANALYSIS_CONFIG",
    "run_pipeline",
    "run_scanpy_pbmc3k_pipeline",
    "run_squidpy_visium_pipeline",
]
