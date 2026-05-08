"""Case Study 2 — single-cell perturbation utilities.

Implements the framework-phase pieces of the `agentcoop run-case2 /
evaluate-perturb / ensemble-perturb / report-case2` pipeline
(docs/experiments/case_study.md §3). No deep models are trained here; we ship:

- a synthetic perturbation dataset generator (so tests work offline),
- unified prediction schema validation,
- three simple baselines (perturbed mean, matching mean, ridge),
- per-model evaluation with Pearson Delta + cosine + Precision@K,
- three ensemble strategies (validation winner, rank fusion, weighted).

Real foundation-model adapters (GEARS, scGPT, scFoundation, Geneformer)
are registered through the `agentcoop/wrappers/*/manifest.yaml` stubs
and are invoked via the `sandbox_repo` backend once their images exist.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Synthetic dataset (offline tests)
# ---------------------------------------------------------------------------


@dataclass
class PerturbDataset:
    dataset: str
    genes: list[str]
    perturbations: list[str]
    control_expression: list[float]
    perturbation_effects: dict[str, list[float]]

    def to_json(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "genes": self.genes,
            "perturbations": self.perturbations,
            "control_expression": self.control_expression,
            "perturbation_effects": self.perturbation_effects,
        }


def synthetic_dataset(
    name: str = "synthetic_norman",
    *,
    n_genes: int = 50,
    n_perturbations: int = 12,
    seed: int = 42,
) -> PerturbDataset:
    rng = random.Random(seed)
    genes = [f"G{i:03d}" for i in range(n_genes)]
    perturbations = [f"P{i:02d}" for i in range(n_perturbations)]
    control = [rng.gauss(5.0, 0.5) for _ in range(n_genes)]
    effects: dict[str, list[float]] = {}
    for pert in perturbations:
        # Each perturbation modifies 3-7 target genes.
        k = rng.randint(3, 7)
        target_idx = rng.sample(range(n_genes), k)
        delta = [0.0] * n_genes
        for i in target_idx:
            delta[i] = rng.choice([-1.0, 1.0]) * rng.uniform(0.5, 2.5)
        effects[pert] = delta
    return PerturbDataset(name, genes, perturbations, control, effects)


# ---------------------------------------------------------------------------
# Prediction schema
# ---------------------------------------------------------------------------


PREDICTION_REQUIRED_KEYS = {"model", "dataset", "split", "perturbation", "genes", "predicted_delta"}


def validate_prediction(pred: dict[str, Any]) -> list[str]:
    missing = sorted(PREDICTION_REQUIRED_KEYS - set(pred))
    if missing:
        return [f"missing_keys:{','.join(missing)}"]
    if len(pred["genes"]) != len(pred["predicted_delta"]):
        return ["genes/predicted_delta length mismatch"]
    return []


# ---------------------------------------------------------------------------
# Simple baselines
# ---------------------------------------------------------------------------


def perturbed_mean(
    dataset: PerturbDataset, pert: str, *, split: str = "test", seed: int = 1
) -> dict[str, Any]:
    """Predict the average effect across all seen perturbations."""
    seen = [d for p, d in dataset.perturbation_effects.items() if p != pert]
    if not seen:
        delta = [0.0] * len(dataset.genes)
    else:
        delta = [
            sum(vec[i] for vec in seen) / len(seen) for i in range(len(dataset.genes))
        ]
    return _pred(dataset, pert, split, seed, "perturbed_mean", delta)


def matching_mean(
    dataset: PerturbDataset, pert: str, *, split: str = "test", seed: int = 1
) -> dict[str, Any]:
    """For a pair like A+B, predict the sum of the single-gene effects if present."""
    if "+" in pert:
        parts = pert.split("+")
        vecs = [dataset.perturbation_effects.get(p) for p in parts if dataset.perturbation_effects.get(p)]
        if vecs:
            delta = [sum(v[i] for v in vecs) for i in range(len(dataset.genes))]
            return _pred(dataset, pert, split, seed, "matching_mean", delta)
    return perturbed_mean(dataset, pert, split=split, seed=seed) | {"model": "matching_mean"}


def crispr_informed_mean(
    dataset: PerturbDataset, pert: str, *, split: str = "test", seed: int = 1
) -> dict[str, Any]:
    """Biologically informed baseline: assumes the knocked-out gene drops."""
    delta = [0.0] * len(dataset.genes)
    if pert in dataset.genes:
        delta[dataset.genes.index(pert)] = -1.5
    return _pred(dataset, pert, split, seed, "crispr_informed_mean", delta)


def ridge_baseline(
    dataset: PerturbDataset,
    pert: str,
    *,
    split: str = "test",
    seed: int = 1,
    alpha: float = 1.0,
) -> dict[str, Any]:
    """Shrunken mean of seen effects (closed-form ridge equivalent)."""
    seen = [d for p, d in dataset.perturbation_effects.items() if p != pert]
    if not seen:
        return _pred(dataset, pert, split, seed, "ridge", [0.0] * len(dataset.genes))
    mean = [sum(v[i] for v in seen) / len(seen) for i in range(len(dataset.genes))]
    shrink = 1.0 / (1.0 + alpha)
    return _pred(dataset, pert, split, seed, "ridge", [shrink * m for m in mean])


def _pred(
    ds: PerturbDataset, pert: str, split: str, seed: int, model: str, delta: list[float]
) -> dict[str, Any]:
    return {
        "model": model,
        "dataset": ds.dataset,
        "split": split,
        "perturbation": pert,
        "genes": list(ds.genes),
        "predicted_delta": delta,
        "metadata": {"seed": seed},
    }


BASELINES = {
    "perturbed_mean": perturbed_mean,
    "matching_mean": matching_mean,
    "crispr_informed_mean": crispr_informed_mean,
    "ridge": ridge_baseline,
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n == 0:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def _cosine(x: list[float], y: list[float]) -> float:
    num = sum(xi * yi for xi, yi in zip(x, y))
    dx = math.sqrt(sum(xi ** 2 for xi in x))
    dy = math.sqrt(sum(yi ** 2 for yi in y))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def _rmse(x: list[float], y: list[float]) -> float:
    n = len(x)
    return math.sqrt(sum((xi - yi) ** 2 for xi, yi in zip(x, y)) / n) if n else 0.0


def _precision_at_k(pred: list[float], ref: list[float], k: int = 20) -> float:
    pred_idx = sorted(range(len(pred)), key=lambda i: abs(pred[i]), reverse=True)[:k]
    ref_idx = sorted(range(len(ref)), key=lambda i: abs(ref[i]), reverse=True)[:k]
    return len(set(pred_idx) & set(ref_idx)) / k


def evaluate_prediction(pred: dict[str, Any], dataset: PerturbDataset, *, top_k: int = 20) -> dict[str, Any]:
    pert = pred["perturbation"]
    ref = dataset.perturbation_effects.get(pert)
    if ref is None:
        return {"error": f"unknown perturbation {pert}"}
    # align genes
    gene_idx = {g: i for i, g in enumerate(dataset.genes)}
    aligned_pred = [0.0] * len(dataset.genes)
    for g, d in zip(pred["genes"], pred["predicted_delta"]):
        if g in gene_idx:
            aligned_pred[gene_idx[g]] = d
    # top-20 by gold magnitude
    top_idx = sorted(range(len(ref)), key=lambda i: abs(ref[i]), reverse=True)[:top_k]
    top_pred = [aligned_pred[i] for i in top_idx]
    top_ref = [ref[i] for i in top_idx]
    return {
        "model": pred["model"],
        "dataset": pred["dataset"],
        "perturbation": pert,
        "pearson_delta": _pearson(aligned_pred, ref),
        "pearson_delta_top20": _pearson(top_pred, top_ref),
        "cosine": _cosine(aligned_pred, ref),
        "rmse": _rmse(aligned_pred, ref),
        "precision_at_k": _precision_at_k(aligned_pred, ref, k=top_k),
        "gene_universe_coverage": len(set(pred["genes"]) & set(dataset.genes)) / len(dataset.genes),
    }


# ---------------------------------------------------------------------------
# Ensembles
# ---------------------------------------------------------------------------


def ensemble_validation_winner(metrics_by_model: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    avg = {
        m: (sum(x["pearson_delta_top20"] for x in rows) / len(rows) if rows else 0.0)
        for m, rows in metrics_by_model.items()
    }
    winner = max(avg, key=avg.get) if avg else None
    return {"strategy": "validation_winner", "winner": winner, "avg_pearson_delta_top20": avg}


def ensemble_rank_fusion(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    ranked: list[list[str]] = []
    for pred in predictions:
        order = sorted(
            pred["genes"],
            key=lambda g: abs(pred["predicted_delta"][pred["genes"].index(g)]),
            reverse=True,
        )
        ranked.append(order)
    scores: dict[str, float] = {}
    for order in ranked:
        for rank, g in enumerate(order):
            scores[g] = scores.get(g, 0.0) + 1.0 / (rank + 1)
    top = sorted(scores, key=scores.get, reverse=True)[:20]
    return {"strategy": "rank_fusion", "top_genes": top, "scores": scores}


def ensemble_weighted(
    predictions: list[dict[str, Any]],
    weights: list[float] | None = None,
) -> dict[str, Any]:
    if not predictions:
        return {"strategy": "weighted_average", "genes": [], "predicted_delta": []}
    if weights is None:
        weights = [1.0 / len(predictions)] * len(predictions)
    genes = predictions[0]["genes"]
    deltas = [0.0] * len(genes)
    for w, pred in zip(weights, predictions):
        for i, d in enumerate(pred["predicted_delta"]):
            deltas[i] += w * d
    return {"strategy": "weighted_average", "genes": genes, "predicted_delta": deltas, "weights": weights}


__all__ = [
    "PerturbDataset",
    "synthetic_dataset",
    "validate_prediction",
    "BASELINES",
    "evaluate_prediction",
    "ensemble_validation_winner",
    "ensemble_rank_fusion",
    "ensemble_weighted",
]
