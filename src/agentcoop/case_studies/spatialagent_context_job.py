from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np

from agentcoop.case_studies.job_utils import ensure_output_dir, load_job_request, read_secret, write_job_result


OBJECTIVE_GENESETS = {
    "Carnevale22_Adenosine": ["ENTPD1", "NT5E", "ADORA2A", "TGFB1", "CXCL12", "VEGFA", "COL1A1"],
    "IFNG": ["IFNG", "CXCL9", "CXCL10", "GZMB", "PRF1", "NKG7", "STAT1"],
    "IL2": ["IL2", "IL7R", "ICOS", "LTB", "MAL", "CD27"],
    "Steinhart_crispra_GD2_D22": ["PDCD1", "CTLA4", "TIGIT", "HAVCR2", "LAG3", "TOX", "EOMES"],
}

OBJECTIVE_ALIASES = {
    "Carnevale22_Adenosine": "Carnevale22_Adenosine",
    "IFNG": "IFNG",
    "IL2": "IL2",
    "Steinhart": "Steinhart_crispra_GD2_D22",
    "Steinhart_crispra_GD2_D22": "Steinhart_crispra_GD2_D22",
}


def _resolve_breast_assets(hints: Mapping[str, Any]) -> dict[str, Any]:
    input_assets = dict(hints.get("input_assets", {})) if isinstance(hints.get("input_assets", {}), Mapping) else {}
    bundle = input_assets.get("tenx_visium_breast_cancer_assets", {})
    if not isinstance(bundle, Mapping) or not bundle:
        raise RuntimeError("tenx_visium_breast_cancer_assets are required in task.hints.input_assets")
    return dict(bundle)


def _load_breast_spatial_adata(assets: Mapping[str, Any]) -> Any:
    import scanpy as sc

    h5ad_path = str(assets.get("h5ad_path", "")).strip()
    if h5ad_path:
        return sc.read_h5ad(h5ad_path)
    visium_root = str(assets.get("visium_root", "")).strip()
    if not visium_root:
        raise RuntimeError("breast spatial assets require either h5ad_path or visium_root")
    return sc.read_visium(visium_root, count_file="filtered_feature_bc_matrix.h5")


def _safe_score(adata: Any, genes: list[str]) -> np.ndarray:
    present = [gene for gene in genes if gene in adata.var_names]
    if not present:
        return np.zeros(adata.n_obs, dtype=float)
    import scanpy as sc

    key = f"score_{abs(hash(tuple(present))) % 100000}"
    sc.tl.score_genes(adata, gene_list=present, score_name=key, use_raw=False)
    return np.asarray(adata.obs[key], dtype=float)


def _render_objective_scores(score_table: dict[str, float], output_path: Path) -> None:
    labels = list(score_table.keys())
    values = [float(score_table[label]) for label in labels]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, values, color=["#6A994E", "#386FA4", "#7B2CBF", "#BC4749"])
    ax.set_ylabel("context score")
    ax.set_title("Spatially Routed Perturbation Objectives")
    ax.tick_params(axis="x", rotation=25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_objective(label: str) -> str:
    normalized = OBJECTIVE_ALIASES.get(str(label).strip(), "").strip()
    if not normalized:
        raise RuntimeError(f"Unsupported routed objective: {label}")
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SpatialAgent-backed spatial context routing job.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, _, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    assets = _resolve_breast_assets(hints)
    spatialagent_cfg = dict(hints.get("input_assets", {}).get("spatialagent", {})) if isinstance(hints.get("input_assets", {}).get("spatialagent", {}), Mapping) else {}

    import scanpy as sc

    adata = _load_breast_spatial_adata(assets)
    adata.var_names_make_unique()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars))
    use_hvg = adata[:, adata.var["highly_variable"]].copy() if "highly_variable" in adata.var else adata.copy()
    sc.pp.scale(use_hvg, max_value=10.0)
    sc.tl.pca(use_hvg, svd_solver="arpack", n_comps=min(30, use_hvg.n_vars, max(2, use_hvg.n_obs - 1)))
    sc.pp.neighbors(use_hvg, n_neighbors=min(12, max(3, use_hvg.n_obs - 1)))
    sc.tl.leiden(use_hvg, resolution=0.55, key_added="context_cluster", flavor="igraph", n_iterations=2, directed=False)
    adata.obs["context_cluster"] = use_hvg.obs["context_cluster"].astype(str).tolist()

    raw_scores = {objective: _safe_score(adata, genes) for objective, genes in OBJECTIVE_GENESETS.items()}
    objective_scores = {objective: float(np.percentile(values, 85)) for objective, values in raw_scores.items()}
    objective_scores["Carnevale22_Adenosine"] += 0.15 * objective_scores.get("Steinhart", 0.0)
    routed_objective = max(objective_scores.items(), key=lambda item: float(item[1]))[0]
    routing_confidence = float(
        max(objective_scores.values()) - sorted(objective_scores.values(), reverse=True)[1]
        if len(objective_scores) > 1
        else objective_scores[routed_objective]
    )

    selected_score = raw_scores[routed_objective]
    adata.obs["selected_context_score"] = selected_score
    niche_map_path = output_dir / "suppressive_niche_map.png"
    sc.pl.spatial(adata, color="selected_context_score", spot_size=1.1, show=False)
    fig = plt.gcf()
    fig.suptitle(f"Spatial context score: {routed_objective}", y=1.02)
    fig.savefig(niche_map_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    objective_figure_path = output_dir / "objective_rationale_figure.png"
    _render_objective_scores(objective_scores, objective_figure_path)

    agent_result_text = ""
    agent_json: dict[str, Any] | None = None
    spatialagent_invoked = False
    api_key = read_secret("openai_api_key")
    if api_key:
        try:
            from spatialagent.agent import SpatialAgent, make_llm

            spatialagent_invoked = True
            query = (
                "You are routing a tumor spatial transcriptomics case to one perturbation-design objective.\n"
                f"Candidate objectives: {list(OBJECTIVE_GENESETS)}.\n"
                f"Objective scores: {json.dumps(objective_scores, ensure_ascii=True)}.\n"
                f"Top routed objective by heuristic: {routed_objective}.\n"
                "Return a <conclude> block containing JSON with keys "
                "`routed_objective`, `rationale`, and `confidence`."
            )
            import os

            os.environ["OPENAI_API_KEY"] = api_key
            llm = make_llm(
                str(spatialagent_cfg.get("model", "gpt-5")).strip() or "gpt-5",
                streaming=False,
                track_cost=False,
                use_azure=False,
            )
            agent = SpatialAgent(
                llm=llm,
                tools=[],
                data_path=str(output_dir / "spatialagent_data"),
                save_path=str(output_dir / "spatialagent_run"),
                tool_retrieval=False,
                skill_retrieval=False,
                auto_interpret_figures=False,
                web_search_model=None,
            )
            state = agent.run(query, config={"recursion_limit": 8, "thread_id": "agentcoop-spatial-context"})
            messages = state.get("messages", []) if isinstance(state, Mapping) else []
            if messages:
                last = messages[-1]
                content = getattr(last, "content", "")
                if isinstance(content, str):
                    agent_result_text = content
                    agent_json = _extract_json_object(content)
        except Exception as exc:
            agent_result_text = f"SpatialAgent invocation failed: {exc}"

    final_objective = routed_objective
    rationale = f"Heuristic spatial-context routing favored {routed_objective}."
    used_heuristic_fallback = False
    if agent_json:
        final_objective = str(agent_json.get("routed_objective", routed_objective)).strip() or routed_objective
        rationale = str(agent_json.get("rationale", rationale))
    elif spatialagent_invoked:
        used_heuristic_fallback = True

    final_objective = _normalize_objective(final_objective)
    heuristic_objective = _normalize_objective(routed_objective)

    context_report = {
        "selected_repo": "SpatialAgent",
        "selected_specialists": ["SpatialAgent"],
        "dataset_id": "tenx_visium_breast_cancer",
        "routed_objective": final_objective,
        "heuristic_objective": heuristic_objective,
        "routing_confidence": routing_confidence,
        "objective_scores": objective_scores,
        "spatialagent_invoked": spatialagent_invoked,
        "used_heuristic_fallback": used_heuristic_fallback,
        "spatialagent_raw_output": agent_result_text,
        "rationale": rationale,
        "figure_paths": {
            "suppressive_niche_map_path": str(niche_map_path),
            "objective_rationale_figure_path": str(objective_figure_path),
        },
    }
    report_path = Path(output_dir / "spatial_context_report.json")
    report_path.write_text(json.dumps(context_report, ensure_ascii=True, indent=2), encoding="utf-8")
    routed_path = Path(output_dir / "routed_objective.json")
    routed_path.write_text(
        json.dumps(
            {
                "routed_objective": final_objective,
                "heuristic_objective": heuristic_objective,
                "routing_confidence": routing_confidence,
                "objective_scores": objective_scores,
                "rationale": rationale,
                "used_heuristic_fallback": used_heuristic_fallback,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    outputs = {
        "selected_repo": "SpatialAgent",
        "selected_specialists": ["SpatialAgent"],
        "spatial_context_report_path": str(report_path),
        "routed_objective_path": str(routed_path),
        "routed_objective": final_objective,
        "routing_confidence": routing_confidence,
        "objective_scores": objective_scores,
        "summary": "Spatial context routing completed.",
    }
    artifacts = [
        {"path": str(report_path), "mime": "application/json"},
        {"path": str(routed_path), "mime": "application/json"},
        {"path": str(niche_map_path), "mime": "image/png"},
        {"path": str(objective_figure_path), "mime": "image/png"},
    ]
    trace = {
        "specialist": "SpatialAgent",
        "spatialagent_invoked": spatialagent_invoked,
        "used_heuristic_fallback": used_heuristic_fallback,
        "routed_objective": final_objective,
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "spatial_context_report_path": str(report_path),
            "routed_objective_path": str(routed_path),
            "suppressive_niche_map_path": str(niche_map_path),
            "objective_rationale_figure_path": str(objective_figure_path),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=min(0.95, max(0.55, 0.6 + routing_confidence)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
