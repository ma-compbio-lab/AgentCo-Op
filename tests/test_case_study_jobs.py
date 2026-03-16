from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image


class _FakeAnnData:
    def __init__(self) -> None:
        self.X = np.array(
            [
                [1.0, 0.0, 2.0],
                [0.0, 3.0, 1.0],
                [4.0, 1.0, 0.0],
                [2.0, 2.0, 1.0],
            ],
            dtype=float,
        )
        self.obs = pd.DataFrame(index=[f"cell_{idx}" for idx in range(4)])
        self.var_names = ["GeneA", "GeneB", "GeneC"]
        self.uns: dict[str, object] = {}
        self.obsm: dict[str, np.ndarray] = {}

    @property
    def n_obs(self) -> int:
        return int(self.X.shape[0])

    @property
    def n_vars(self) -> int:
        return int(self.X.shape[1])

    def copy(self) -> "_FakeAnnData":
        other = _FakeAnnData()
        other.X = self.X.copy()
        other.obs = self.obs.copy(deep=True)
        other.var_names = list(self.var_names)
        other.uns = {key: value for key, value in self.uns.items()}
        other.obsm = {key: value.copy() for key, value in self.obsm.items()}
        return other

    def write_h5ad(self, path: str) -> None:
        Path(path).write_bytes(b"fake-h5ad")


def test_scanpy_paul15_job_load_dataset_supports_transfer_targets() -> None:
    dataset_calls: list[str] = []

    class _FakeDatasets:
        def paul15(self) -> _FakeAnnData:
            dataset_calls.append("paul15")
            data = _FakeAnnData()
            data.obs["paul15_clusters"] = pd.Categorical(["1Ery", "1Ery", "2GMP", "2GMP"])
            return data

        def moignard15(self) -> _FakeAnnData:
            dataset_calls.append("moignard15")
            data = _FakeAnnData()
            data.obs["exp_groups"] = pd.Categorical(["PS", "NP", "HF", "4SG"])
            data.uns["iroot"] = 0
            return data

        def krumsiek11(self) -> _FakeAnnData:
            dataset_calls.append("krumsiek11")
            data = _FakeAnnData()
            data.obs["cell_type"] = pd.Categorical(["progenitor", "Mo", "Ery", "Mk"])
            data.uns["iroot"] = 1
            return data

    fake_scanpy = types.SimpleNamespace(datasets=_FakeDatasets())
    module = importlib.import_module("dynaforge.case_studies.scanpy_paul15_job")

    paul15_data, paul15_meta = module._load_dataset(fake_scanpy, "paul15")
    moignard_data, moignard_meta = module._load_dataset(fake_scanpy, "moignard15")
    krumsiek_data, krumsiek_meta = module._load_dataset(fake_scanpy, "krumsiek11")

    assert paul15_data.n_obs == 4
    assert paul15_meta["group_key"] == "paul15_clusters"
    assert moignard_data.uns["iroot"] == 0
    assert moignard_meta["group_key"] == "exp_groups"
    assert krumsiek_data.uns["iroot"] == 1
    assert krumsiek_meta["group_key"] == "cell_type"
    assert dataset_calls == ["paul15", "moignard15", "krumsiek11"]


def test_spatial_job_loaders_support_seqfish_dataset() -> None:
    dataset_calls: list[str] = []

    class _FakeDatasets:
        def visium_hne_adata_crop(self) -> _FakeAnnData:
            dataset_calls.append("visium")
            return _FakeAnnData()

        def seqfish(self) -> _FakeAnnData:
            dataset_calls.append("seqfish")
            data = _FakeAnnData()
            data.obs["celltype_mapped_refined"] = pd.Categorical(["A", "A", "B", "B"])
            return data

    fake_squidpy = types.SimpleNamespace(datasets=_FakeDatasets())
    cluster_module = importlib.import_module("dynaforge.case_studies.scanpy_visium_cluster_job")
    interaction_module = importlib.import_module("dynaforge.case_studies.squidpy_visium_interactions_job")

    _, cluster_meta = cluster_module._load_dataset(fake_squidpy, "seqfish")
    _, interaction_meta = interaction_module._load_dataset(fake_squidpy, "seqfish")

    assert cluster_meta["dataset_id"] == "seqfish"
    assert cluster_meta["raw_filename"] == "seqfish_raw.h5ad"
    assert interaction_meta["dataset_id"] == "seqfish"
    assert interaction_meta["raw_filename"] == "seqfish.h5ad"
    assert dataset_calls == ["seqfish", "seqfish"]


def test_scanpy_paul15_job_stringifies_uns_keys() -> None:
    module = importlib.import_module("dynaforge.case_studies.scanpy_paul15_job")

    sanitized = module._stringify_uns_keys({"highlights": {0: {"gene": "Gata1"}}, "meta": [1, {2: "x"}]})

    assert "highlights" in sanitized
    assert "0" in sanitized["highlights"]
    assert sanitized["highlights"]["0"]["gene"] == "Gata1"
    assert sanitized["meta"][1]["2"] == "x"


def test_spatial_interaction_job_can_render_manual_seqfish_layout(tmp_path: Path) -> None:
    module = importlib.import_module("dynaforge.case_studies.squidpy_visium_interactions_job")
    adata = _FakeAnnData()
    adata.obs["cluster"] = pd.Categorical(["A", "A", "B", "B"])
    adata.obsm["spatial"] = np.array([[0.0, 0.0], [1.0, 0.2], [0.3, 1.4], [1.1, 1.2]], dtype=float)

    figure_path = tmp_path / "manual_seqfish_layout.png"
    module._render_spatial_layout(
        adata=adata,
        cluster_key="cluster",
        figure_path=figure_path,
        dpi=100,
        title="seqFISH Layout",
    )

    assert figure_path.exists()
    image = Image.open(figure_path)
    assert image.size[0] > 0
    assert image.size[1] > 0


def test_scanpy_visium_cluster_job_bootstraps_missing_raw_data(monkeypatch, tmp_path: Path) -> None:
    dataset_calls = {"count": 0}

    def fake_dataset() -> _FakeAnnData:
        dataset_calls["count"] += 1
        return _FakeAnnData()

    def fake_read_h5ad(path: str) -> _FakeAnnData:
        assert Path(path).exists()
        return _FakeAnnData()

    def fake_pca(adata: _FakeAnnData, svd_solver: str = "arpack") -> None:
        adata.obsm["X_pca"] = np.ones((adata.n_obs, 3), dtype=float)

    def fake_leiden(adata: _FakeAnnData, *, key_added: str, **_: object) -> None:
        adata.obs[key_added] = pd.Categorical(["0", "0", "1", "1"])

    def fake_rank_genes_groups(adata: _FakeAnnData, *, groupby: str, method: str = "wilcoxon") -> None:
        del groupby, method
        adata.uns["rank_genes_groups"] = {
            "names": {
                "0": ["GeneA", "GeneB", "GeneC"],
                "1": ["GeneC", "GeneB", "GeneA"],
            }
        }

    def fake_umap(*args: object, **kwargs: object):
        del args, kwargs
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(4, 4))
        ax.scatter([0, 1], [0, 1])
        ax.set_title("fake-umap")
        return fig

    fake_scanpy = types.ModuleType("scanpy")
    fake_scanpy.settings = types.SimpleNamespace(autoshow=False, verbosity=0)
    fake_scanpy.read_h5ad = fake_read_h5ad
    fake_scanpy.pp = types.SimpleNamespace(
        normalize_total=lambda *args, **kwargs: None,
        log1p=lambda *args, **kwargs: None,
        highly_variable_genes=lambda *args, **kwargs: None,
        scale=lambda *args, **kwargs: None,
        neighbors=lambda *args, **kwargs: None,
    )
    fake_scanpy.tl = types.SimpleNamespace(
        pca=fake_pca,
        umap=lambda *args, **kwargs: None,
        leiden=fake_leiden,
        rank_genes_groups=fake_rank_genes_groups,
    )
    fake_scanpy.pl = types.SimpleNamespace(umap=fake_umap)

    fake_squidpy = types.ModuleType("squidpy")
    fake_squidpy.datasets = types.SimpleNamespace(visium_hne_adata_crop=fake_dataset)

    fake_scipy = types.ModuleType("scipy")
    fake_sparse = types.ModuleType("scipy.sparse")
    fake_sparse.issparse = lambda _: False
    fake_scipy.sparse = fake_sparse

    monkeypatch.setitem(sys.modules, "scanpy", fake_scanpy)
    monkeypatch.setitem(sys.modules, "squidpy", fake_squidpy)
    monkeypatch.setitem(sys.modules, "scipy", fake_scipy)
    monkeypatch.setitem(sys.modules, "scipy.sparse", fake_sparse)
    monkeypatch.setattr("importlib.metadata.version", lambda name: f"fake-{name}")

    module = importlib.import_module("dynaforge.case_studies.scanpy_visium_cluster_job")

    output_dir = tmp_path / "out"
    raw_data_path = tmp_path / "data" / "visium_raw.h5ad"
    summary = module.run_pipeline(
        output_dir=output_dir,
        cluster_figure_path=output_dir / "cluster.png",
        marker_figure_path=output_dir / "marker.png",
        summary_path=output_dir / "cluster_summary.json",
        annotated_data_path=output_dir / "annotated.h5ad",
        raw_data_path=raw_data_path,
        selected_repo="scanpy",
        config={"cluster_key": "scanpy_leiden", "marker_top_n": 1, "marker_cluster_limit": 2},
    )

    assert dataset_calls["count"] == 1
    assert raw_data_path.exists()
    assert summary["cluster_count"] == 2
    assert summary["marker_gene_count"] == 2
    assert Path(summary["cluster_figure_path"]).exists()
    assert Path(summary["marker_figure_path"]).exists()
    assert Path(summary["annotated_data_path"]).exists()

    image = Image.open(summary["cluster_figure_path"])
    assert image.size[0] > 0
    assert image.size[1] > 0
