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
    module = importlib.import_module("agentcoop.case_studies.scanpy_paul15_job")

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
    cluster_module = importlib.import_module("agentcoop.case_studies.scanpy_visium_cluster_job")
    interaction_module = importlib.import_module("agentcoop.case_studies.squidpy_visium_interactions_job")

    _, cluster_meta = cluster_module._load_dataset(fake_squidpy, "seqfish")
    _, interaction_meta = interaction_module._load_dataset(fake_squidpy, "seqfish")

    assert cluster_meta["dataset_id"] == "seqfish"
    assert cluster_meta["raw_filename"] == "seqfish_raw.h5ad"
    assert interaction_meta["dataset_id"] == "seqfish"
    assert interaction_meta["raw_filename"] == "seqfish.h5ad"
    assert dataset_calls == ["seqfish", "seqfish"]


def test_scanpy_paul15_job_stringifies_uns_keys() -> None:
    module = importlib.import_module("agentcoop.case_studies.scanpy_paul15_job")

    sanitized = module._stringify_uns_keys({"highlights": {0: {"gene": "Gata1"}}, "meta": [1, {2: "x"}]})

    assert "highlights" in sanitized
    assert "0" in sanitized["highlights"]
    assert sanitized["highlights"]["0"]["gene"] == "Gata1"
    assert sanitized["meta"][1]["2"] == "x"


def test_spatial_interaction_job_can_render_manual_seqfish_layout(tmp_path: Path) -> None:
    module = importlib.import_module("agentcoop.case_studies.squidpy_visium_interactions_job")
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


def test_spatial_panel_common_prefers_symbol_column_and_alias_matches(monkeypatch) -> None:
    module = importlib.import_module("agentcoop.case_studies.spatial_panel_common")
    fake_scipy = types.ModuleType("scipy")
    fake_sparse = types.ModuleType("scipy.sparse")
    fake_sparse.issparse = lambda _: False
    fake_scipy.sparse = fake_sparse
    monkeypatch.setitem(sys.modules, "scipy", fake_scipy)
    monkeypatch.setitem(sys.modules, "scipy.sparse", fake_sparse)

    class _PanelAnnData:
        def __init__(self) -> None:
            self.X = np.array(
                [
                    [8.0, 0.0, 0.0],
                    [7.0, 0.0, 0.0],
                    [0.0, 9.0, 0.0],
                    [0.0, 8.0, 0.0],
                ],
                dtype=float,
            )
            self.obs = pd.DataFrame(
                {
                    "cell_label": pd.Categorical(["A", "A", "B", "B"]),
                    "sample": pd.Categorical(["s1", "s2", "s1", "s2"]),
                },
                index=[f"cell_{idx}" for idx in range(4)],
            )
            self.var = pd.DataFrame(
                {
                    "SYMBOL": ["GeneA", "GeneB", "GeneA_dup"],
                },
                index=["ENSG1", "ENSG2", "ENSG3"],
            )
            self.var_names = self.var.index

        def copy(self):
            other = _PanelAnnData()
            other.X = self.X.copy()
            other.obs = self.obs.copy(deep=True)
            other.var = self.var.copy(deep=True)
            other.var_names = other.var.index
            return other

        def __getitem__(self, item):
            if isinstance(item, tuple):
                obs_idx, var_idx = item
            else:
                obs_idx, var_idx = item, self.var_names
            if isinstance(obs_idx, pd.Series):
                obs_mask = obs_idx.values
            else:
                obs_mask = np.asarray(obs_idx)
            if hasattr(var_idx, "tolist"):
                requested = [str(v) for v in var_idx.tolist()]
            else:
                requested = [str(v) for v in list(var_idx)]
            var_positions = [list(self.var_names).index(name) for name in requested]
            other = _PanelAnnData()
            other.X = self.X[np.asarray(obs_mask), :][:, var_positions]
            other.obs = self.obs.loc[self.obs.index[np.asarray(obs_mask)]].copy()
            other.var = self.var.loc[requested].copy()
            other.var_names = other.var.index
            return other

        @property
        def n_obs(self) -> int:
            return int(self.X.shape[0])

        @property
        def n_vars(self) -> int:
            return int(self.X.shape[1])

    adata = _PanelAnnData()
    panel_df, backup_df, summary = module.build_panel_design(
        adata,
        label_col="cell_label",
        batch_col="sample",
        panel_size=2,
        backup_gene_count=1,
        excluded_genes=[],
        min_cells_per_label=1,
    )
    alias_lookup = module._build_feature_alias_lookup(
        types.SimpleNamespace(
            var=pd.DataFrame({"gene_ids": ["ENSG1", "ENSG2"]}, index=["GeneA", "GeneB"]),
            var_names=pd.Index(["GeneA", "GeneB"]),
        )
    )

    assert summary["gene_identifier_source"] == "symbol_preferred"
    assert "GeneA" in panel_df["gene"].tolist()
    assert alias_lookup["ENSG1"] == "GeneA"
    assert alias_lookup["GENEA"] == "GeneA"
    assert backup_df.shape[0] == 1


def test_cell2location_reference_signature_extractor_supports_regression_mod() -> None:
    module = importlib.import_module("agentcoop.case_studies.cell2location_transfer_job")

    class _FakeReference:
        def __init__(self) -> None:
            self.uns = {
                "regression_mod": {
                    "fact_names": np.array(["sample_s1", "annotation_1_Astro", "annotation_1_Oligo"]),
                    "var_names": np.array(["ENSG1", "ENSG2"]),
                    "post_sample_means": {
                        "gene_factors": np.array(
                            [
                                [1.0, 2.0],
                                [3.0, 4.0],
                                [5.0, 6.0],
                            ],
                            dtype=float,
                        ),
                        "sample_scaling": np.array([[2.0], [4.0]], dtype=float),
                    },
                }
            }
            self.var_names = pd.Index(["ENSG1", "ENSG2"])
            self.var = pd.DataFrame(index=self.var_names)
            self.varm = {}
            self.raw = None

    inf_aver = module._extract_reference_signatures(_FakeReference())

    assert list(inf_aver.columns) == ["Astro", "Oligo"]
    assert list(inf_aver.index) == ["ENSG1", "ENSG2"]
    assert float(inf_aver.loc["ENSG1", "Astro"]) == 9.0
    assert float(inf_aver.loc["ENSG2", "Oligo"]) == 18.0


def test_cell2location_alignment_can_use_spatial_gene_ids() -> None:
    module = importlib.import_module("agentcoop.case_studies.cell2location_transfer_job")

    class _FakeSpatial:
        def __init__(self) -> None:
            self.var_names = pd.Index(["GeneA", "GeneB", "GeneC"])
            self.var = pd.DataFrame({"gene_ids": ["ENSG1", "ENSG2", "ENSG3"]}, index=self.var_names)

        def copy(self):
            other = _FakeSpatial()
            other.var_names = self.var_names.copy()
            other.var = self.var.copy(deep=True)
            return other

        def __getitem__(self, item):
            _, var_idx = item
            if hasattr(var_idx, "tolist"):
                requested = [str(v) for v in var_idx.tolist()]
            else:
                requested = [str(v) for v in list(var_idx)]
            if all(name in list(self.var_names) for name in requested):
                selected_names = requested
                other = _FakeSpatial()
                other.var_names = pd.Index(selected_names)
                other.var = self.var.loc[selected_names].copy()
                return other
            mask = np.asarray(var_idx, dtype=bool)
            selected_names = self.var.index[mask].tolist()
            other = _FakeSpatial()
            other.var_names = pd.Index(selected_names)
            other.var = self.var.loc[selected_names].copy()
            return other

        def var_names_make_unique(self) -> None:
            self.var_names = pd.Index(self.var_names.astype(str))
            self.var.index = self.var_names

    cell_state_df = pd.DataFrame({"Astro": [1.0, 2.0], "Oligo": [3.0, 4.0]}, index=["ENSG1", "ENSG2"])
    aligned, aligned_df, shared = module._align_spatial_and_reference_genes(_FakeSpatial(), cell_state_df)

    assert shared == 2
    assert list(aligned.var_names) == ["ENSG1", "ENSG2"]
    assert list(aligned.var["symbol_var_name"]) == ["GeneA", "GeneB"]
    assert list(aligned_df.index) == ["ENSG1", "ENSG2"]


def test_cell2location_train_and_export_helpers_retry_without_use_gpu() -> None:
    module = importlib.import_module("agentcoop.case_studies.cell2location_transfer_job")

    class _FakeModel:
        def __init__(self) -> None:
            self.train_calls = []
            self.export_calls = []

        def train(self, **kwargs):
            self.train_calls.append(kwargs)
            if "use_gpu" in kwargs:
                raise TypeError("Trainer.__init__() got an unexpected keyword argument 'use_gpu'")
            return None

        def export_posterior(self, adata, *, sample_kwargs, add_to_obsm, use_quantiles):
            self.export_calls.append(
                {
                    "sample_kwargs": dict(sample_kwargs),
                    "add_to_obsm": list(add_to_obsm),
                    "use_quantiles": bool(use_quantiles),
                }
            )
            if "use_gpu" in sample_kwargs:
                raise TypeError("export_posterior() got an unexpected keyword argument 'use_gpu'")
            if use_quantiles and "num_samples" in sample_kwargs:
                raise TypeError("QuantileMixin._posterior_quantile() got an unexpected keyword argument 'num_samples'")
            return adata

    fake_model = _FakeModel()
    fake_adata = types.SimpleNamespace(n_obs=32)

    module._train_cell2location_model(fake_model, max_epochs=12)
    result = module._export_cell2location_posterior(fake_model, fake_adata, num_samples=7)

    assert result is fake_adata
    assert len(fake_model.train_calls) == 2
    assert fake_model.train_calls[-1]["accelerator"] == "cpu"
    assert len(fake_model.export_calls) == 3
    assert "use_gpu" not in fake_model.export_calls[-1]["sample_kwargs"]
    assert fake_model.export_calls[-1]["sample_kwargs"]["num_samples"] == 7
    assert fake_model.export_calls[-1]["use_quantiles"] is False
    assert fake_model.export_calls[-1]["add_to_obsm"] == ["means"]


def test_spatialagent_context_job_normalizes_supported_objectives() -> None:
    module = importlib.import_module("agentcoop.case_studies.spatialagent_context_job")

    assert module._normalize_objective("Steinhart") == "Steinhart_crispra_GD2_D22"
    assert module._normalize_objective("IFNG") == "IFNG"


def test_biodiscovery_job_normalizes_supported_objectives() -> None:
    module = importlib.import_module("agentcoop.case_studies.biodiscovery_perturbation_job")

    assert module._normalize_objective("Steinhart") == "Steinhart_crispra_GD2_D22"
    assert module._normalize_objective("Carnevale22_Adenosine") == "Carnevale22_Adenosine"


def test_cell2location_render_figures_backfills_total_counts(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("agentcoop.case_studies.cell2location_transfer_job")

    class _FakeAbundance(pd.DataFrame):
        @property
        def _constructor(self):  # pragma: no cover - pandas protocol
            return _FakeAbundance

    class _FakeSpatial:
        def __init__(self) -> None:
            self.X = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float)
            self.obs = pd.DataFrame(index=["spot1", "spot2"])
            self.obsm = {
                "q05_cell_abundance_w_sf": _FakeAbundance(
                    {"TypeA": [0.2, 0.4], "TypeB": [0.7, 0.3]},
                    index=["spot1", "spot2"],
                )
                }
            self.uns = {"mod": {"factor_names": ["TypeA", "TypeB"]}}

        @property
        def n_obs(self) -> int:
            return int(self.X.shape[0])

    calls: list[str] = []

    def fake_spatial(adata, color, **kwargs):
        del kwargs
        calls.append(str(color))
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(2, 2))
        ax.plot([0, 1], [0, 1])
        return fig

    fake_scanpy = types.SimpleNamespace(
        pl=types.SimpleNamespace(spatial=fake_spatial),
        pp=types.SimpleNamespace(neighbors=lambda *args, **kwargs: None),
        tl=types.SimpleNamespace(
            leiden=lambda adata, **kwargs: adata.obs.__setitem__("region_cluster", pd.Categorical(["0", "1"]))
        ),
    )
    monkeypatch.setitem(sys.modules, "scanpy", fake_scanpy)

    adata = _FakeSpatial()
    figure_paths = module._render_training_free_figures(adata, tmp_path, slide_id="slide")

    assert "total_counts" in adata.obs.columns
    assert float(adata.obs["total_counts"].iloc[0]) == 3.0
    assert Path(figure_paths["qc_spatial_path"]).exists()
    assert "total_counts" in calls


def test_biodiscovery_job_writes_runtime_shims(tmp_path: Path) -> None:
    module = importlib.import_module("agentcoop.case_studies.biodiscovery_perturbation_job")
    created = module._write_runtime_shims(tmp_path)

    assert (tmp_path / "anthropic.py").exists()
    assert (tmp_path / "tiktoken.py").exists()
    assert (tmp_path / "arxiv.py").exists()
    assert (tmp_path / "scholarly.py").exists()
    assert (tmp_path / "pymed.py").exists()
    assert (tmp_path / "langchain" / "tools.py").exists()
    assert created


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

    module = importlib.import_module("agentcoop.case_studies.scanpy_visium_cluster_job")

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
