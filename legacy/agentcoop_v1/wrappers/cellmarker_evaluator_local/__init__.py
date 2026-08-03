"""CellMarker evaluator (join-agent) local-Python adapter.

Implements the join + evaluation step specified in `docs/experiments/case_study_2.md`
§13–§14: take Seurat's RNA top-N marker JSON and Signac's ATAC top-N
marker JSON, harmonise gene symbols, compute per-cell-type
intersection / union, parse a CellMarker 2.0 mouse marker file,
filter to skin tissues, build gold marker sets, and report
precision / recall / collaboration-gain metrics.

This adapter is the inaugural example of a `join_agent` in the
`parallel_then_join` topology supported by
`agentcoop.core.repo_collaboration` since Session 7.3.
"""

from agentcoop.core.env_manager import register_required_packages
from agentcoop.wrappers.cellmarker_evaluator_local.adapter import (
    invoke_cellmarker_evaluator_local,
    register,
)

register_required_packages(
    "CellMarkerEvaluator",
    [
        "numpy",
        "pandas",
        "matplotlib",
        "openpyxl",
    ],
)

register()
