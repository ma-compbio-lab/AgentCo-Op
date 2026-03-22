from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from dynaforge.case_studies.job_utils import ensure_output_dir, load_job_request, read_secret, write_job_result


OBJECTIVE_ALIASES = {
    "Carnevale22_Adenosine": "Carnevale22_Adenosine",
    "IFNG": "IFNG",
    "IL2": "IL2",
    "Steinhart": "Steinhart_crispra_GD2_D22",
    "Steinhart_crispra_GD2_D22": "Steinhart_crispra_GD2_D22",
}


def _parse_analyze_stdout(stdout: str) -> dict[str, Any]:
    result: dict[str, Any] = {"raw_stdout": stdout}
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Model: "):
            result["summary_line"] = stripped
            break
    return result


def _normalize_objective(label: str) -> str:
    normalized = OBJECTIVE_ALIASES.get(str(label).strip(), "").strip()
    if not normalized:
        raise RuntimeError(f"Unsupported BioDiscoveryAgent objective: {label}")
    return normalized


def _resolve_breast_assets(hints: Mapping[str, Any]) -> dict[str, Any]:
    input_assets = dict(hints.get("input_assets", {})) if isinstance(hints.get("input_assets", {}), Mapping) else {}
    bundle = input_assets.get("tenx_visium_breast_cancer_assets", {})
    if not isinstance(bundle, Mapping) or not bundle:
        raise RuntimeError("tenx_visium_breast_cancer_assets are required for spatial grounding")
    return dict(bundle)


def _load_breast_spatial_adata(assets: Mapping[str, Any]) -> Any:
    import anndata as ad

    h5ad_path = str(assets.get("h5ad_path", "")).strip()
    if not h5ad_path:
        raise RuntimeError("Breast spatial grounding requires a packaged h5ad_path asset")
    return ad.read_h5ad(h5ad_path)


def _score_predicted_genes(adata: Any, genes: list[str]) -> pd.DataFrame:
    from scipy import sparse

    gene_to_index = {str(name).upper(): idx for idx, name in enumerate(map(str, adata.var_names))}
    matrix = adata.X
    rows: list[dict[str, Any]] = []
    for rank, gene in enumerate(genes, start=1):
        idx = gene_to_index.get(str(gene).upper())
        if idx is None:
            rows.append(
                {
                    "gene": str(gene),
                    "original_rank": rank,
                    "present_in_spatial": False,
                    "spot_detection_rate": 0.0,
                    "mean_expression": 0.0,
                    "grounding_score": 0.0,
                }
            )
            continue
        column = matrix[:, idx]
        if sparse.issparse(column):
            dense = np.asarray(column.toarray()).ravel()
        else:
            dense = np.asarray(column).ravel()
        spot_detection_rate = float((dense > 0).mean()) if dense.size else 0.0
        mean_expression = float(dense.mean()) if dense.size else 0.0
        grounding_score = 0.7 * spot_detection_rate + 0.3 * min(1.0, np.log1p(max(mean_expression, 0.0)))
        rows.append(
            {
                "gene": str(gene),
                "original_rank": rank,
                "present_in_spatial": True,
                "spot_detection_rate": spot_detection_rate,
                "mean_expression": mean_expression,
                "grounding_score": grounding_score,
            }
        )
    frame = pd.DataFrame.from_records(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values(["grounding_score", "spot_detection_rate", "original_rank"], ascending=[False, False, True]).reset_index(drop=True)
    frame["revised_rank"] = np.arange(1, len(frame) + 1)
    return frame


def _render_grounding_figure(adata: Any, ranked_frame: pd.DataFrame, output_path: Path) -> None:
    top_present = ranked_frame[ranked_frame["present_in_spatial"]].head(6)
    if top_present.empty or "spatial" not in getattr(adata, "obsm", {}):
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.barh(ranked_frame.head(8)["gene"][::-1], ranked_frame.head(8)["grounding_score"][::-1], color="#386FA4")
        ax.set_title("Spatial grounding scores for predicted genes")
        ax.set_xlabel("grounding score")
        fig.tight_layout()
        fig.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return

    coords = np.asarray(adata.obsm["spatial"])
    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    axes = axes.flatten()
    gene_to_index = {str(name).upper(): idx for idx, name in enumerate(map(str, adata.var_names))}
    from scipy import sparse

    for axis, (_, row) in zip(axes, top_present.iterrows()):
        gene = str(row["gene"])
        idx = gene_to_index.get(gene.upper())
        if idx is None:
            axis.axis("off")
            continue
        column = adata.X[:, idx]
        values = np.asarray(column.toarray()).ravel() if sparse.issparse(column) else np.asarray(column).ravel()
        scatter = axis.scatter(coords[:, 0], coords[:, 1], c=values, cmap="magma", s=8)
        axis.set_title(gene)
        axis.set_xticks([])
        axis.set_yticks([])
        fig.colorbar(scatter, ax=axis, fraction=0.046, pad=0.04)
    for axis in axes[len(top_present) :]:
        axis.axis("off")
    fig.suptitle("Spatial grounding of top perturbation candidates", y=0.98)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _write_runtime_shims(repo_dir: Path) -> list[str]:
    created: list[str] = []
    shim_files = {
        "anthropic.py": (
            'HUMAN_PROMPT = "\\n\\nHuman:"\n'
            'AI_PROMPT = "\\n\\nAssistant:"\n'
            "class APIStatusError(Exception):\n    pass\n"
            "class _Unavailable:\n"
            "    def __getattr__(self, name):\n"
            '        raise RuntimeError("anthropic shim is unavailable for this sandboxed BioDiscoveryAgent run")\n'
            "class Anthropic:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        self.messages = _Unavailable()\n"
            "        self.completions = _Unavailable()\n"
        ),
        "tiktoken.py": (
            "class _Encoding:\n"
            "    def encode(self, text):\n"
            "        return list(str(text).encode('utf-8'))\n"
            "def get_encoding(_name):\n"
            "    return _Encoding()\n"
        ),
        "arxiv.py": (
            "class Search:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        self.args = args\n"
            "        self.kwargs = kwargs\n"
            "class Client:\n"
            "    def results(self, _search):\n"
            "        return []\n"
            "class SortCriterion:\n"
            "    Relevance = 'relevance'\n"
            "class SortOrder:\n"
            "    Descending = 'descending'\n"
        ),
        "scholarly.py": (
            "def search_pubs(_query):\n"
            "    return iter([{'title': 'No scholarly access in sandbox shim'}])\n"
            "def pprint(_obj):\n"
            "    return None\n"
        ),
        "pymed.py": (
            "class PubMed:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        pass\n"
            "    def query(self, *args, **kwargs):\n"
            "        return []\n"
        ),
    }
    langchain_dir = repo_dir / "langchain"
    langchain_init = langchain_dir / "__init__.py"
    langchain_tools = langchain_dir / "tools.py"
    if not langchain_init.exists():
        langchain_dir.mkdir(parents=True, exist_ok=True)
        langchain_init.write_text("from .tools import BaseTool, tool\n", encoding="utf-8")
        created.append(str(langchain_init))
    if not langchain_tools.exists():
        langchain_dir.mkdir(parents=True, exist_ok=True)
        langchain_tools.write_text(
            "class BaseTool:\n"
            "    pass\n\n"
            "def tool(*args, **kwargs):\n"
            "    def decorator(fn):\n"
            "        return fn\n"
            "    if args and callable(args[0]) and not kwargs:\n"
            "        return args[0]\n"
            "    return decorator\n",
            encoding="utf-8",
        )
        created.append(str(langchain_tools))
    for relative_path, content in shim_files.items():
        target = repo_dir / relative_path
        if target.exists():
            continue
        target.write_text(content, encoding="utf-8")
        created.append(str(target))
    return created


def _patch_biodiscovery_chat_model_support(repo_dir: Path) -> bool:
    llm_path = repo_dir / "LLM.py"
    if not llm_path.exists():
        return False
    original = llm_path.read_text(encoding="utf-8")
    target = 'if model.startswith("gpt-3.5") or model.startswith("gpt-4") or model.startswith("o1"):'
    replacement = (
        'if model.startswith("gpt-3.5") or model.startswith("gpt-4") or model.startswith("gpt-5") '
        'or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):'
    )
    if target not in original or replacement in original:
        return False
    llm_path.write_text(original.replace(target, replacement), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BioDiscoveryAgent perturbation-design job.")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    _, inputs, _, hints = load_job_request(args.input_json)
    output_dir = ensure_output_dir(hints, args.output_json)
    specialist_a = dict(inputs.get("specialist_a", {})) if isinstance(inputs.get("specialist_a", {}), Mapping) else {}
    routed_objective = _normalize_objective(
        str(specialist_a.get("routed_objective", "Carnevale22_Adenosine")).strip() or "Carnevale22_Adenosine"
    )
    bio_cfg = dict(hints.get("input_assets", {}).get("biodiscovery", {})) if isinstance(hints.get("input_assets", {}).get("biodiscovery", {}), Mapping) else {}

    repo_root = Path(str(hints.get("repo_root", ""))).resolve()
    repo_dir = repo_root / "BioDiscoveryAgent"
    if not repo_dir.exists():
        repo_dir = repo_root / "BioDiscoveryAgent".lower()
    if not repo_dir.exists():
        repo_dir = Path(str(hints.get("input_assets", {}).get("biodiscovery_repo_path", ""))).resolve()
    if not repo_dir.exists():
        raise RuntimeError("BioDiscoveryAgent repo path could not be resolved from case hints")

    model_name = str(bio_cfg.get("model", "gpt-4o")).strip() or "gpt-4o"
    steps = int(bio_cfg.get("steps", 3))
    num_genes = int(bio_cfg.get("num_genes", 32))
    task_name = str(bio_cfg.get("task", "perturb-genes-brief")).strip() or "perturb-genes-brief"
    essential = int(bio_cfg.get("essential", 1))

    openai_key = read_secret("openai_api_key")
    if not openai_key:
        raise RuntimeError("BioDiscoveryAgent job requires .secrets/openai_api_key")
    (repo_dir / "openai_api_key.txt").write_text(f"none:{openai_key}\n", encoding="utf-8")
    shim_files = _write_runtime_shims(repo_dir)
    patched_chat_support = _patch_biodiscovery_chat_model_support(repo_dir)

    run_name = "test"
    log_dir = model_name
    cli_cmd = [
        sys.executable,
        "research_assistant.py",
        "--task",
        task_name,
        "--model",
        model_name,
        "--data_name",
        routed_objective,
        "--steps",
        str(steps),
        "--num_genes",
        str(num_genes),
        "--log_dir",
        log_dir,
        "--run_name",
        run_name,
        "--python",
        sys.executable,
    ]
    completed = subprocess.run(
        cli_cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(os.environ),
            "PYTHONPATH": str(repo_dir)
            + (
                f"{os.pathsep}{os.environ['PYTHONPATH']}"
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "BioDiscoveryAgent CLI failed")

    prediction_dir = repo_dir / f"{log_dir}_{routed_objective}" / run_name
    sampled_genes_path = prediction_dir / f"sampled_genes_{steps}.npy"
    if not sampled_genes_path.exists():
        raise RuntimeError(f"BioDiscoveryAgent did not produce {sampled_genes_path}")
    predicted_genes = [str(item) for item in np.load(sampled_genes_path, allow_pickle=True).tolist()]

    analyze_cmd = [
        sys.executable,
        "analyze.py",
        "--model",
        model_name,
        "--dataset",
        routed_objective,
        "--rounds",
        str(steps),
        "--trials",
        "1",
        "--essential",
        str(essential),
    ]
    analysis = subprocess.run(
        analyze_cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(os.environ),
            "PYTHONPATH": str(repo_dir)
            + (
                f"{os.pathsep}{os.environ['PYTHONPATH']}"
                if os.environ.get("PYTHONPATH")
                else ""
            ),
        },
    )
    eval_payload = _parse_analyze_stdout(analysis.stdout)
    eval_payload["returncode"] = analysis.returncode
    eval_payload["stderr"] = analysis.stderr
    if analysis.returncode != 0:
        raise RuntimeError(analysis.stderr or analysis.stdout or "BioDiscoveryAgent analyze.py failed")

    ranked_path = output_dir / "ranked_gene_batch.tsv"
    pd.DataFrame({"rank": range(1, len(predicted_genes) + 1), "gene": predicted_genes}).to_csv(ranked_path, sep="\t", index=False)
    raw_predictions_path = output_dir / "biodiscovery_raw_predictions.json"
    raw_predictions_path.write_text(
        json.dumps(
            {
                "selected_repo": "BioDiscoveryAgent",
                "routed_objective": routed_objective,
                "model": model_name,
                "predicted_genes": predicted_genes,
                "cli_stdout": completed.stdout,
                "cli_stderr": completed.stderr,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    eval_path = output_dir / "hitrate_eval.json"
    eval_path.write_text(json.dumps(eval_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    breast_assets = _resolve_breast_assets(hints)
    adata = _load_breast_spatial_adata(breast_assets)
    grounding_frame = _score_predicted_genes(adata, predicted_genes)
    grounding_path = output_dir / "spatial_grounding_scores.tsv"
    grounding_frame.to_csv(grounding_path, sep="\t", index=False)
    revised_path = output_dir / "revised_ranked_gene_batch.tsv"
    grounding_frame.loc[:, ["revised_rank", "gene", "grounding_score", "spot_detection_rate", "mean_expression", "original_rank"]].to_csv(
        revised_path,
        sep="\t",
        index=False,
    )
    grounding_figure_path = output_dir / "final_gene_spatial_heatmap.png"
    _render_grounding_figure(adata, grounding_frame, grounding_figure_path)

    top_k = min(10, len(grounding_frame))
    before_mean = float(
        grounding_frame.sort_values("original_rank", ascending=True).head(top_k)["grounding_score"].mean()
    ) if top_k else 0.0
    after_mean = float(grounding_frame.head(top_k)["grounding_score"].mean()) if top_k else 0.0
    collaboration_gain = {
        "selected_repo": "BioDiscoveryAgent",
        "routed_objective": routed_objective,
        "top_k": top_k,
        "mean_grounding_before_rerank": before_mean,
        "mean_grounding_after_rerank": after_mean,
        "gain": float(after_mean - before_mean),
        "present_gene_fraction": float(grounding_frame["present_in_spatial"].mean()) if not grounding_frame.empty else 0.0,
    }
    collaboration_gain_path = output_dir / "collaboration_gain.json"
    collaboration_gain_path.write_text(json.dumps(collaboration_gain, ensure_ascii=True, indent=2), encoding="utf-8")

    outputs = {
        "selected_repo": "BioDiscoveryAgent",
        "selected_specialists": ["SpatialAgent", "BioDiscoveryAgent"],
        "routed_objective": routed_objective,
        "ranked_gene_batch_path": str(ranked_path),
        "revised_ranked_gene_batch_path": str(revised_path),
        "raw_predictions_path": str(raw_predictions_path),
        "hitrate_eval_path": str(eval_path),
        "spatial_grounding_scores_path": str(grounding_path),
        "grounding_figure_path": str(grounding_figure_path),
        "collaboration_gain_path": str(collaboration_gain_path),
        "predicted_genes": predicted_genes,
        "summary": "BioDiscoveryAgent perturbation design completed.",
    }
    artifacts = [
        {"path": str(ranked_path), "mime": "text/tab-separated-values"},
        {"path": str(revised_path), "mime": "text/tab-separated-values"},
        {"path": str(grounding_path), "mime": "text/tab-separated-values"},
        {"path": str(raw_predictions_path), "mime": "application/json"},
        {"path": str(eval_path), "mime": "application/json"},
        {"path": str(grounding_figure_path), "mime": "image/png"},
        {"path": str(collaboration_gain_path), "mime": "application/json"},
    ]
    trace = {
        "specialist": "BioDiscoveryAgent",
        "routed_objective": routed_objective,
        "model": model_name,
        "prediction_dir": str(prediction_dir),
        "shim_files": shim_files,
        "patched_chat_support": patched_chat_support,
        "grounding_gain": collaboration_gain["gain"],
    }
    write_job_result(
        output_json=args.output_json,
        outputs=outputs,
        summary={
            "ranked_gene_batch_path": str(ranked_path),
            "revised_ranked_gene_batch_path": str(revised_path),
            "raw_predictions_path": str(raw_predictions_path),
            "hitrate_eval_path": str(eval_path),
            "spatial_grounding_scores_path": str(grounding_path),
            "grounding_figure_path": str(grounding_figure_path),
            "collaboration_gain_path": str(collaboration_gain_path),
        },
        artifacts=artifacts,
        trace=trace,
        confidence=0.84 if analysis.returncode == 0 else 0.7,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
