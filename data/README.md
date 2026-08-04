# `data/`

**Nothing in v2 reads this directory.** It is retained on disk from the v1 era in case a benchmark
over real biological data is built later. None of it is tracked by git.

The v2 benchmark suite ([`agentcoop/bench/suites/`](../agentcoop/bench/suites/)) is synthetic and
self-contained by design: every ground truth is knowable by construction, which is what lets the
harness say who was right. A suite whose answers had to be inferred from a model's output could not.

## What is here

| Path | Size | What it is |
|---|---:|---|
| `shareseq_skin/` | 9.0G | SHARE-seq mouse skin multiome |
| `pbmc_lite/` | 3.9G | 10x PBMC multiome subset |
| `heart_human/` | 1.4G | 10x human heart multiome |
| `raw/` | 720M | GSM8K / MATH / MBPP / HotpotQA / DROP / HumanEval dumps |
| `heart_merfish/` | 353M | Developing human heart MERFISH |
| `aflow_aligned/` | 11M | AFlow-aligned validation/test splits |
| `panglaodb/` | 1.2M | PanglaoDB marker reference |

## Regenerating

The importers that produced `raw/` and `aflow_aligned/` (`scripts/download_datasets.py` and
`agentcoop.benchmarks.aflow_splits`), and the SHA-256 manifest that pinned them, were part of v1 and
were removed along with it. They remain in git history at `94c3750`:

```bash
git show 94c3750:data/data_hashes.json
git checkout 94c3750 -- scripts/download_datasets.py
```

The genomics datasets were fetched by the v1 case-study setup scripts, also at that commit.
