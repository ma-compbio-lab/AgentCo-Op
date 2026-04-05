from __future__ import annotations

LEGACY_SCANPY_PBMC3K_CASE_IDS = frozenset(
    {
        "scanpy_pbmc3k_umap",
    }
)

LEGACY_SCANPY_TRAJECTORY_CASE_IDS = frozenset(
    {
        "scanpy_paul15_trajectory",
        "scanpy_paul15_paper_figure",
        "scanpy_paul15_specialized_collaboration",
        "scanpy_moignard15_method_transfer",
        "scanpy_moignard15_specialized_collaboration",
        "scanpy_krumsiek11_method_transfer",
        "scanpy_krumsiek11_specialized_collaboration",
    }
)

LEGACY_VISIUM_MULTI_AGENT_CASE_IDS = frozenset(
    {
        "visium_multi_agent_collaboration",
        "visium_multi_agent_monolith",
        "seqfish_multi_agent_collaboration",
        "seqfish_multi_agent_monolith",
    }
)

LEGACY_SQUIDPY_CASE_IDS = frozenset(
    {
        "squidpy_visium_hne_spatial",
        "squidpy_visium_hne_interactions",
        "squidpy_seqfish_method_transfer",
    }
)

LEGACY_CASE_DEFAULT_RUN_NAMES = {
    "scanpy_pbmc3k_umap": "scanpy_pbmc3k_case",
    "scanpy_paul15_trajectory": "scanpy_paul15_case",
    "scanpy_paul15_paper_figure": "scanpy_paul15_paper_figure_case",
    "scanpy_paul15_specialized_collaboration": "scanpy_paul15_specialized_collaboration_case",
    "scanpy_moignard15_method_transfer": "scanpy_moignard15_transfer_case",
    "scanpy_moignard15_specialized_collaboration": "scanpy_moignard15_specialized_collaboration_case",
    "scanpy_krumsiek11_method_transfer": "scanpy_krumsiek11_transfer_case",
    "scanpy_krumsiek11_specialized_collaboration": "scanpy_krumsiek11_specialized_collaboration_case",
    "visium_multi_agent_collaboration": "visium_multi_agent_collaboration_case",
    "visium_multi_agent_monolith": "visium_multi_agent_monolith_case",
    "seqfish_multi_agent_collaboration": "seqfish_multi_agent_collaboration_case",
    "seqfish_multi_agent_monolith": "seqfish_multi_agent_monolith_case",
    "squidpy_visium_hne_spatial": "squidpy_visium_case",
    "squidpy_visium_hne_interactions": "squidpy_visium_interactions_case",
    "squidpy_seqfish_method_transfer": "squidpy_seqfish_transfer_case",
}


def legacy_case_family(case_id: str) -> str | None:
    if case_id in LEGACY_SCANPY_PBMC3K_CASE_IDS:
        return "scanpy_pbmc3k"
    if case_id in LEGACY_SCANPY_TRAJECTORY_CASE_IDS:
        return "scanpy_trajectory"
    if case_id in LEGACY_VISIUM_MULTI_AGENT_CASE_IDS:
        return "visium_multi_agent"
    if case_id in LEGACY_SQUIDPY_CASE_IDS:
        return "squidpy"
    return None


def is_legacy_case_id(case_id: str) -> bool:
    return legacy_case_family(case_id) is not None


def legacy_case_default_run_name(case_id: str) -> str:
    return LEGACY_CASE_DEFAULT_RUN_NAMES.get(case_id, "case_study")
