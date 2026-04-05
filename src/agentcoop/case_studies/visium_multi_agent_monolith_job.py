from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from agentcoop.case_studies.scanpy_visium_cluster_job import run_pipeline as run_cluster_pipeline
from agentcoop.case_studies.squidpy_visium_interactions_job import run_pipeline as run_interaction_pipeline


def run_pipeline(
    *,
    output_dir: str | Path,
    cluster_figure_path: str | Path,
    marker_figure_path: str | Path,
    cluster_summary_path: str | Path,
    annotated_data_path: str | Path,
    spatial_figure_path: str | Path,
    interaction_figure_path: str | Path,
    interaction_summary_path: str | Path,
    summary_path: str | Path,
    raw_data_path: str | Path,
    scanpy_repo: str = "scanpy",
    squidpy_repo: str = "squidpy",
    cluster_config: Mapping[str, Any] | None = None,
    interaction_config: Mapping[str, Any] | None = None,
    cluster_title: str = "Spatial Scanpy Clusters",
    marker_title: str = "Spatial Cluster Marker Heatmap",
    spatial_title: str = "Spatial Layout",
    interaction_title: str = "Spatial Neighborhood Enrichment",
) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    summary_path = Path(summary_path).resolve()
    raw_data_path = Path(raw_data_path).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    raw_data_path.parent.mkdir(parents=True, exist_ok=True)

    cluster_summary = run_cluster_pipeline(
        output_dir=output_dir,
        cluster_figure_path=cluster_figure_path,
        marker_figure_path=marker_figure_path,
        summary_path=cluster_summary_path,
        annotated_data_path=annotated_data_path,
        raw_data_path=raw_data_path,
        selected_repo=scanpy_repo,
        config=cluster_config or {},
        cluster_title=cluster_title,
        marker_title=marker_title,
    )
    interaction_cfg = dict(interaction_config or {})
    interaction_cfg.setdefault("cluster_key", cluster_summary["cluster_key"])
    interaction_cfg.setdefault("spatial_title", spatial_title)
    dataset_label = str(cluster_summary.get("dataset_label", interaction_cfg.get("dataset_id", "Spatial")))
    interaction_summary = run_interaction_pipeline(
        output_dir=output_dir,
        figure_path=spatial_figure_path,
        interaction_figure_path=interaction_figure_path,
        summary_path=interaction_summary_path,
        raw_data_path=raw_data_path,
        input_h5ad_path=annotated_data_path,
        selected_repo=squidpy_repo,
        config=interaction_cfg,
        figure_title=str(interaction_cfg.get("figure_title", interaction_title or f"{dataset_label} Neighborhood Enrichment")),
    )

    summary = {
        "scanpy_repo": scanpy_repo,
        "squidpy_repo": squidpy_repo,
        "cluster_summary_path": str(Path(cluster_summary_path).resolve()),
        "interaction_summary_path": str(Path(interaction_summary_path).resolve()),
        "summary_path": str(summary_path),
        "cluster_summary": cluster_summary,
        "interaction_summary": interaction_summary,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Visium multi-agent monolith baseline pipeline.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cluster-figure-path", required=True)
    parser.add_argument("--marker-figure-path", required=True)
    parser.add_argument("--cluster-summary-path", required=True)
    parser.add_argument("--annotated-data-path", required=True)
    parser.add_argument("--spatial-figure-path", required=True)
    parser.add_argument("--interaction-figure-path", required=True)
    parser.add_argument("--interaction-summary-path", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--raw-data-path", required=True)
    parser.add_argument("--scanpy-repo", default="scanpy")
    parser.add_argument("--squidpy-repo", default="squidpy")
    parser.add_argument("--cluster-config-json", default="")
    parser.add_argument("--interaction-config-json", default="")
    parser.add_argument("--cluster-title", default="Spatial Scanpy Clusters")
    parser.add_argument("--marker-title", default="Spatial Cluster Marker Heatmap")
    parser.add_argument("--spatial-title", default="Spatial Layout")
    parser.add_argument("--interaction-title", default="Spatial Neighborhood Enrichment")
    args = parser.parse_args(argv)

    cluster_config = json.loads(args.cluster_config_json) if args.cluster_config_json else {}
    interaction_config = json.loads(args.interaction_config_json) if args.interaction_config_json else {}
    summary = run_pipeline(
        output_dir=args.output_dir,
        cluster_figure_path=args.cluster_figure_path,
        marker_figure_path=args.marker_figure_path,
        cluster_summary_path=args.cluster_summary_path,
        annotated_data_path=args.annotated_data_path,
        spatial_figure_path=args.spatial_figure_path,
        interaction_figure_path=args.interaction_figure_path,
        interaction_summary_path=args.interaction_summary_path,
        summary_path=args.summary_path,
        raw_data_path=args.raw_data_path,
        scanpy_repo=args.scanpy_repo,
        squidpy_repo=args.squidpy_repo,
        cluster_config=cluster_config,
        interaction_config=interaction_config,
        cluster_title=args.cluster_title,
        marker_title=args.marker_title,
        spatial_title=args.spatial_title,
        interaction_title=args.interaction_title,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
