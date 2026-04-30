# Case Study 1 — Bulk RNA-seq → GeneAgent

End-to-end vertical-collaboration pipeline using the airway dexamethasone
dataset shape (synthetic DE TSV; same column set as
`scripts/case1_airway_de.R` produces from Bioconductor `airway`).

## Pipeline

```
synthetic DE TSV
  → bio.select_markers (padj ≤ 0.05, |log2fc| ≥ 1.0, top-100)
  → bio.enrich (hypergeometric ORA over a bundled Hallmark-style table)
  → GeneAgent wrapper (stub returning canned functional labels)
  → IntegratorReviewer (LLM, gpt-4o-mini)
  → final_report.md + final_report.json
```

## Outputs in this directory

| File | Producer |
|---|---|
| `de_results.tsv` | `scripts/case1_synthetic_de.py` |
| `gene_sets/up_genes.json`, `gene_sets/down_genes.json` | `agentcoop bio select-markers` |
| `enrichment/up.json`, `enrichment/down.json` | `agentcoop bio enrich` |
| `geneagent_up.json`, `geneagent_down.json` | `agentcoop run-node geneagent` |
| `final_report.json`, `final_report.md` | `scripts/case1_integrator.py` |

## Reproduce

```bash
python scripts/case1_synthetic_de.py runs/case1/airway
python -m agentcoop.cli bio select-markers \
  --de-results runs/case1/airway/de_results.tsv \
  --padj-max 0.05 --abs-log2fc-min 1.0 --top-k 100 \
  --out runs/case1/airway/gene_sets
python -m agentcoop.cli bio enrich --gene-set runs/case1/airway/gene_sets/up_genes.json --out runs/case1/airway/enrichment/up.json
python -m agentcoop.cli bio enrich --gene-set runs/case1/airway/gene_sets/down_genes.json --out runs/case1/airway/enrichment/down.json
python -m agentcoop.cli run-node geneagent \
  --input runs/case1/airway/gene_sets/up_genes.json \
  --context "Airway smooth muscle cells treated with dexamethasone vs untreated control" \
  --out runs/case1/airway/geneagent_up.json
python -m agentcoop.cli run-node geneagent \
  --input runs/case1/airway/gene_sets/down_genes.json \
  --context "Airway smooth muscle cells treated with dexamethasone vs untreated control" \
  --out runs/case1/airway/geneagent_down.json
export OPENAI_API_KEY=sk-...
python scripts/case1_integrator.py runs/case1/airway
```

For real airway data, run `scripts/case1_airway_de.R` instead of
`case1_synthetic_de.py`. The downstream pipeline is identical.

## Findings

The integrator correctly flagged the synthetic GeneAgent's labels as
*unsupported* (the stub returns canned themes without enrichment-grounded
evidence). This is the verifier doing its job: in production, a real
GeneAgent run would attach database citations that the integrator could
verify.

Token usage: 448 input / 361 output (single LLM call). Cost ≈ $0.0003.
