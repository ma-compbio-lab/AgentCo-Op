from __future__ import annotations

import csv
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict
from urllib.request import urlopen


JsonDict = Dict[str, Any]


CELL2LOCATION_MOUSE_BRAIN_VISIUM_ZIP = (
    "https://cell2location.cog.sanger.ac.uk/tutorial/mouse_brain_visium_wo_cloupe_data.zip"
)
CELL2LOCATION_MOUSE_BRAIN_RAW_SCRNA = (
    "https://cell2location.cog.sanger.ac.uk/tutorial/mouse_brain_snrna/all_cells_20200625.h5ad"
)
CELL2LOCATION_MOUSE_BRAIN_LABELS = (
    "https://cell2location.cog.sanger.ac.uk/tutorial/mouse_brain_snrna/"
    "snRNA_annotation_astro_subtypes_refined59_20200823.csv"
)
CELL2LOCATION_MOUSE_BRAIN_REFERENCE_SIGNATURES = (
    "https://cell2location.cog.sanger.ac.uk/tutorial/mouse_brain_snrna/regression_model/"
    "RegressionGeneBackgroundCoverageTorch_65covariates_40532cells_12819genes/sc.h5ad"
)

CELL2LOCATION_MOUSE_BRAIN_LAYER_ANNOTATIONS = {
    slide: (
        "https://raw.githubusercontent.com/vitkl/cell2location_paper/master/notebooks/selected_results/"
        f"mouse_visium_snrna/manual_SSp_layers/SSp_ManLayerAnn_{slide}.csv"
    )
    for slide in ("ST8059048", "ST8059049", "ST8059050", "ST8059051", "ST8059052")
}

TENX_BREAST_BASE = (
    "https://cf.10xgenomics.com/samples/spatial-exp/1.2.0/"
    "Parent_Visium_Human_BreastCancer"
)
TENX_BREAST_FILTERED_H5 = f"{TENX_BREAST_BASE}/Parent_Visium_Human_BreastCancer_filtered_feature_bc_matrix.h5"
TENX_BREAST_SPATIAL_TAR = f"{TENX_BREAST_BASE}/Parent_Visium_Human_BreastCancer_spatial.tar.gz"
TENX_BREAST_IMAGE = f"{TENX_BREAST_BASE}/Parent_Visium_Human_BreastCancer_image.tif"
TENX_BREAST_METRICS = f"{TENX_BREAST_BASE}/Parent_Visium_Human_BreastCancer_metrics_summary.csv"
TENX_BREAST_WEB_SUMMARY = f"{TENX_BREAST_BASE}/Parent_Visium_Human_BreastCancer_web_summary.html"


def ensure_cell2location_mouse_brain_assets(root_dir: str | Path) -> JsonDict:
    root = Path(root_dir).resolve()
    downloads_dir = root / "downloads"
    raw_dir = root / "raw"
    labels_dir = root / "labels"
    spatial_dir = root / "spatial"
    reference_dir = root / "reference"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    spatial_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)

    visium_zip = downloads_dir / "mouse_brain_visium_wo_cloupe_data.zip"
    _download_file(CELL2LOCATION_MOUSE_BRAIN_VISIUM_ZIP, visium_zip)
    visium_unpack_dir = spatial_dir / "mouse_brain_visium_wo_cloupe_data"
    if not (visium_unpack_dir / "Visium_mouse.csv").exists():
        _extract_zip(visium_zip, spatial_dir)

    raw_scrna_path = raw_dir / "all_cells_20200625.h5ad"
    labels_path = labels_dir / "snRNA_annotation_astro_subtypes_refined59_20200823.csv"
    reference_signatures_path = reference_dir / "mouse_brain_reference_signatures.h5ad"
    _download_file(CELL2LOCATION_MOUSE_BRAIN_RAW_SCRNA, raw_scrna_path)
    _download_file(CELL2LOCATION_MOUSE_BRAIN_LABELS, labels_path)
    _download_file(CELL2LOCATION_MOUSE_BRAIN_REFERENCE_SIGNATURES, reference_signatures_path)

    manual_layers_dir = labels_dir / "manual_layers"
    manual_layers_dir.mkdir(parents=True, exist_ok=True)
    manual_layer_paths: dict[str, str] = {}
    for slide_id, url in CELL2LOCATION_MOUSE_BRAIN_LAYER_ANNOTATIONS.items():
        path = manual_layers_dir / f"{slide_id}.csv"
        _download_file(url, path)
        manual_layer_paths[slide_id] = str(path)

    visium_manifest = _read_visium_manifest(visium_unpack_dir / "Visium_mouse.csv")
    return {
        "dataset_family": "cell2location_mouse_brain",
        "spatial_root": str(visium_unpack_dir),
        "visium_manifest_path": str(visium_unpack_dir / "Visium_mouse.csv"),
        "visium_slides": visium_manifest,
        "raw_scrna_h5ad_path": str(raw_scrna_path),
        "reference_signatures_h5ad_path": str(reference_signatures_path),
        "labels_csv_path": str(labels_path),
        "manual_layer_paths": manual_layer_paths,
        "source_urls": {
            "visium_zip": CELL2LOCATION_MOUSE_BRAIN_VISIUM_ZIP,
            "raw_scrna": CELL2LOCATION_MOUSE_BRAIN_RAW_SCRNA,
            "labels": CELL2LOCATION_MOUSE_BRAIN_LABELS,
            "reference_signatures": CELL2LOCATION_MOUSE_BRAIN_REFERENCE_SIGNATURES,
            "manual_layers": dict(CELL2LOCATION_MOUSE_BRAIN_LAYER_ANNOTATIONS),
        },
    }


def ensure_tenx_breast_visium_assets(root_dir: str | Path) -> JsonDict:
    root = Path(root_dir).resolve()
    downloads_dir = root / "downloads"
    visium_dir = root / "visium_breast"
    spatial_dir = visium_dir / "spatial"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    spatial_dir.mkdir(parents=True, exist_ok=True)

    filtered_h5 = visium_dir / "filtered_feature_bc_matrix.h5"
    spatial_tar = downloads_dir / "Parent_Visium_Human_BreastCancer_spatial.tar.gz"
    image_tif = downloads_dir / "Parent_Visium_Human_BreastCancer_image.tif"
    metrics_csv = downloads_dir / "Parent_Visium_Human_BreastCancer_metrics_summary.csv"
    web_summary = downloads_dir / "Parent_Visium_Human_BreastCancer_web_summary.html"

    source_urls = {
        "filtered_feature_bc_matrix_h5": TENX_BREAST_FILTERED_H5,
        "spatial_tar_gz": TENX_BREAST_SPATIAL_TAR,
        "image_tif": TENX_BREAST_IMAGE,
        "metrics_csv": TENX_BREAST_METRICS,
        "web_summary": TENX_BREAST_WEB_SUMMARY,
    }
    try:
        _download_file(TENX_BREAST_FILTERED_H5, filtered_h5)
        _download_file(TENX_BREAST_SPATIAL_TAR, spatial_tar)
        _download_file(TENX_BREAST_IMAGE, image_tif)
        _download_file(TENX_BREAST_METRICS, metrics_csv)
        _download_file(TENX_BREAST_WEB_SUMMARY, web_summary)
        if not any(spatial_dir.iterdir()):
            _extract_tar_gz(spatial_tar, spatial_dir)
        return {
            "dataset_family": "tenx_visium_breast_cancer",
            "visium_root": str(visium_dir),
            "filtered_feature_bc_matrix_h5_path": str(filtered_h5),
            "spatial_dir": str(spatial_dir),
            "image_tif_path": str(image_tif),
            "metrics_csv_path": str(metrics_csv),
            "web_summary_path": str(web_summary),
            "source_urls": source_urls,
            "dataset_source": "tenx_raw_bundle",
        }
    except Exception:
        return _prepare_scanpy_breast_visium_fallback(visium_dir=visium_dir, source_urls=source_urls)


def _download_file(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=180) as response:
        destination.write_bytes(response.read())


def _prepare_scanpy_breast_visium_fallback(*, visium_dir: Path, source_urls: dict[str, str]) -> JsonDict:
    visium_dir.mkdir(parents=True, exist_ok=True)
    fallback_h5ad = visium_dir / "breast_visium_scanpy_fallback.h5ad"
    if not fallback_h5ad.exists():
        import scanpy as sc

        adata = sc.datasets.visium_sge(sample_id="V1_Breast_Cancer_Block_A_Section_1", include_hires_tiff=False)
        adata.write_h5ad(fallback_h5ad)
    return {
        "dataset_family": "tenx_visium_breast_cancer",
        "h5ad_path": str(fallback_h5ad),
        "visium_root": str(visium_dir),
        "source_urls": source_urls,
        "dataset_source": "scanpy_visium_sge_fallback",
        "sample_id": "V1_Breast_Cancer_Block_A_Section_1",
    }


def _extract_zip(archive_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(output_dir)


def _extract_tar_gz(archive_path: Path, output_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(output_dir)


def _read_visium_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [{str(k): str(v) for k, v in row.items()} for row in reader]
