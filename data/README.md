# `data/`

Holds raw dataset dumps and AFlow-aligned splits. The actual JSONL files
are **not** tracked by git (they are large and regeneratable); only this
README and `data_hashes.json` are committed so reproducibility survives a
fresh checkout.

## Layout

```
data/
├── README.md             (this file)
├── data_hashes.json      (SHA-256 of every raw JSONL — checked in)
├── raw/                  (gitignored)
│   ├── gsm8k/{train,test}.jsonl
│   ├── math/{train,test}.jsonl
│   ├── mbpp/{train,validation,test,prompt}.jsonl
│   ├── hotpotqa/{train,validation}.jsonl
│   ├── drop/{train,validation}.jsonl
│   └── humaneval/HumanEval.jsonl
└── aflow_aligned/        (gitignored)
    ├── gsm8k/{validation,test}.jsonl
    ├── math/{validation,test}.jsonl
    ├── humaneval/{validation,test}.jsonl
    ├── mbpp/{validation,test}.jsonl
    ├── hotpotqa/{validation,test}.jsonl
    └── drop/{validation,test}.jsonl
```

## Regenerate

```bash
# Populate data/raw (HuggingFace + external/human-eval)
python scripts/download_datasets.py

# Build AFlow-aligned splits (seed=42, 20/80 val/test, caps per experiments.md §2.1)
python -m agentcoop.benchmarks.aflow_splits
```

## Known version drift

- **MATH level-5 subset**: `experiments.md` expects 617 examples across
  Counting & Probability, Number Theory, Prealgebra, and Precalculus. The
  `EleutherAI/hendrycks_math` HF release yields **605** (12 dropped as
  duplicates upstream). This is consistent with the HF dataset card and
  acceptable for reproducibility; `AFlow` publishes the original 617 and
  the 12-row gap is reported in metrics transparently.
- **HotpotQA / DROP** use the validation split as the source pool; the
  seed=42 importer reproduces the same 1000-sample cap AFlow reports.
