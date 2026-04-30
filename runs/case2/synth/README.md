# Case Study 2 — Parallel single-cell perturbation specialists

Synthetic Norman-like dataset (50 genes × 12 perturbations, seed 42) run
through 7 specialists in parallel:

- 4 simple baselines: `perturbed_mean`, `matching_mean`,
  `crispr_informed_mean`, `ridge`.
- 3 GPU-stub model adapters: `GEARS`, `scGPT`, `scFoundation`. Each is a
  self-contained adapter (no agentcoop import) so it can ship inside its
  own Docker image. The stubs return synthetic predictions with the
  agreed `perturbation_prediction_v1` schema; real implementations would
  run the corresponding repo's inference.

(Geneformer is intentionally excluded — it's an embedding model, not a
direct perturbation-prediction specialist; per `case_study.md` §3.3 we do
not force it.)

## Outputs

| File | Producer |
|---|---|
| `dataset.json` | `agentcoop perturb synth` |
| `predictions/<model>__<pert>.json` × 84 (= 7 × 12) | `agentcoop perturb run` + per-model adapters |
| `metrics.json` | `agentcoop perturb evaluate` |
| `ensemble_validation.json`, `ensemble_rank.json`, `ensemble_weighted.json` | `agentcoop perturb ensemble` |
| `final_report.md`, `final_report.json` | `scripts/case2_analyzer.py` |

## Per-model averages (n=12 perturbations)

Top by `pearson_delta_top20`:

| Model | pearson_delta_top20 | precision@k | RMSE |
|---|---:|---:|---:|
| perturbed_mean | 0.096 | 0.354 | 0.533 |
| matching_mean | 0.096 | 0.354 | 0.533 |
| ridge | 0.096 | 0.354 | 0.514 |
| GEARS | 0.094 | 0.467 | 0.659 |
| scFoundation | 0.094 | 0.467 | 0.632 |
| scGPT | 0.094 | 0.467 | 0.607 |
| crispr_informed_mean | 0.000 | 0.858 | 0.511 |

Note: scores are uninformative because the dataset is synthetic. The
point is the *pipeline* — heterogeneous models compared fairly + LLM-
driven structured benchmark report.

## Reproduce

```bash
python -m agentcoop.cli perturb synth --dataset synthetic_norman \
  --out runs/case2/synth/dataset.json --n-genes 50 --n-perts 12 --seed 42

# Run baselines
python -m agentcoop.cli perturb run \
  --dataset-path runs/case2/synth/dataset.json \
  --out runs/case2/synth/predictions

# Run GPU-stub adapters (loop in shell):
for model in gears scgpt scfoundation; do
  for pert in P00 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11; do
    mkdir -p /tmp/perturb_io/$model
    cat > /tmp/perturb_io/$model/req.json <<EOF
{"command": "predict_perturbation",
 "params": {"seed": 1, "dataset": "synthetic_norman",
            "perturbation": "$pert", "n_genes": 50}}
EOF
    python agentcoop/wrappers/$model/adapter.py \
      --input /tmp/perturb_io/$model/req.json \
      --output runs/case2/synth/predictions/${model}__${pert}.json
  done
done

python -m agentcoop.cli perturb evaluate \
  --predictions runs/case2/synth/predictions \
  --dataset-path runs/case2/synth/dataset.json \
  --out runs/case2/synth/metrics.json

for s in validation_winner rank_fusion weighted_average; do
  python -m agentcoop.cli perturb ensemble \
    --predictions runs/case2/synth/predictions \
    --dataset-path runs/case2/synth/dataset.json \
    --strategy $s \
    --out runs/case2/synth/ensemble_${s%%_*}.json
done

export OPENAI_API_KEY=sk-...
python scripts/case2_analyzer.py runs/case2/synth
```

For real Norman / Replogle K562 data, replace the synth/run steps with
`agentcoop perturb download` (requires pertpy + figshare access) and
build the GPU images via `scripts/build_repo_images.sh` so the adapters
actually invoke GEARS / scGPT / scFoundation.

## Findings

The LLM analyzer correctly notes that on this synthetic dataset:
- Foundation models and simple baselines tie on `pearson_delta_top20`.
- `crispr_informed_mean` has the highest `precision@k` despite zero
  correlation → a high-recall-low-correlation regime.
- The rank-fusion ensemble strategy was the chosen integrator output.

These are exactly the kind of heads-up notes the analyzer is supposed to
generate without overclaiming. Token usage: 1 776 in / 915 out.
