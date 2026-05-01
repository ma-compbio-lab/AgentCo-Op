# Case Study 1 — TissueAgent × GeneAgent on real Farah MERFISH (real-data run)

Run produced by

```bash
agentcoop collaborate \
  --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

This is the **real-data** run on the Farah et al. 2024 developing
human-heart MERFISH AnnData (`data/heart_merfish/overall_merfish.h5ad`,
228 635 cells × 238 genes). The earlier synthetic-fallback exercise of
the same pipeline is preserved at `runs/case1/heart_merfish_synthetic_fallback_v1/`
and the legacy single-repo airway flow remains at `runs/case1/airway/`.

## Top-line result

| Metric | Value | Target (case_study_1.md §13) |
|---|---|---|
| Status | **`success`** (real-data; not synthetic_fallback) | — |
| Wall time | ≈ 160 s | — |
| n_target / n_control cells | **576 / 5 685** | non-zero each |
| Marker count (Welch t, adj_p<0.05, log2fc>0) | **53** | 46 strict; 40–60 acceptable |
| Marker count (Mann-Whitney U, adj_p<0.05, log2fc>0) | **46** ← **exact manuscript match** | 46 strict |
| Expected example marker overlap | **6 / 6** (DES, IGFBP5, NELL2, HAND2, MYH7, MYH6) | ≥ 5/6 strict |
| GeneAgent process label | **"AV Canal/Nodal Fibroblast Developmental Program"** | "Cardiac Development and Remodeling" or equivalent |
| GeneAgent subprocess coverage | **6 / 5+** (TF + nodal · epicardial mesenchyme · ECM · morphogen · myofibroblast · neuronal) | ≥ 4 / 5 |
| Final integrator model | `gpt-5` (`reasoning_effort=medium`) | — |
| Total LLM tokens (GeneAgent + integrator) | 4 611 | — |

**Both Welch t (53) and Mann-Whitney U (46) reproduce within the
acceptable band; Mann-Whitney U lands exactly on the manuscript's
reported 46.** All 6/6 expected example markers — DES, IGFBP5, NELL2,
HAND2, MYH7, MYH6 — appear in the upregulated set.

## What changed vs the synthetic-fallback v1

| Property | v1 (synthetic) | v2 (real Farah MERFISH) |
|---|---|---|
| Dataset | deterministic synthetic (3 000 × 240) | real Farah `overall_merfish.h5ad` (228 635 × 238) |
| n_target / n_control | 55 / 223 | 576 / 5 685 |
| Marker count | 51 (Welch) | **53 (Welch) / 46 (Mann-Whitney)** |
| Status field | `synthetic_fallback` | `success` |
| GeneAgent label | "AV canal/AV node–biased developmental program" | "AV Canal/Nodal Fibroblast Developmental Program" |
| Conclusion | hypothesis-only (synthetic caveat) | evidence-supported |

## Top markers (rank-ordered by Welch t-test p-value)

| Rank | Gene | log2 FC | adj-p |
|---:|---|---:|---:|
| 1 | DES | 0.43 | 2.3e-91 |
| 2 | MYH6 | 0.25 | 9.0e-84 |
| 3 | IGFBP5 | 0.92 | 7.2e-70 |
| 4 | MYH7 | 0.78 | 1.3e-61 |
| 5 | HAND2 | 0.58 | 5.1e-58 |
| 6 | HCN4 | 0.96 | 4.6e-44 |
| 7 | TBX3 | 0.52 | 8.7e-39 |
| 8 | NELL2 | 0.31 | 1.1e-37 |
| 9 | COL9A2 | 1.14 | 4.0e-36 |
| 10 | CD34 | 0.38 | 5.1e-30 |

(see `artifacts/tissueagent_run/avn_avring_marker_genes.csv` for the full
53-row table; `de_results.csv` carries every gene.)

## GeneAgent's six subprocesses

1. **AV canal / conduction-system specification + excitability** (HCN4,
   TBX3, TBX5, NKX2-5, HAND2, IRX4, HEY1, SEMA6D, KCNH2, CACNA1C, RRAD).
2. **Epicardial-derived mesenchyme + AV cushion-like identity**
   (TCF21, WT1, PDGFRA, PRRX1, MSX2, BMP2, INHBA, MECOM, HEY1, BAMBI,
   TBX5, TSHZ2, TPBG).
3. **ECM organisation + peri-nodal matrix remodelling** (POSTN, FBLN2,
   FMOD, COL9A2, MMP11, CTSV, IGFBP5, ADGRL1).
4. **Wnt / BMP / TGF-β + morphogen-pathway modulation** (RSPO3, SFRP1,
   BMP2, INHBA, BAMBI, HHIP, CRABP2, MSX2, MECOM).
5. **Myofibroblast / contractile reactivation + lineage memory**
   (DES, CNN1, TTN, MYH6, MYH7).
6. **Neuronal / axon-guidance + paracrine cues** (NRXN1, SEMA6D,
   NELL2, NEFL, NTS, PENK, SERPINI1, ADM, BRINP3).

GeneAgent's self-verification cited Gene Ontology, Reactome,
MatrisomeDB, TF/developmental literature (TBX3/HCN4 for AV node;
TCF21/WT1 for epicardial lineage; BMP2/MSX2 for AV cushion), and the
ion-channel / guidance literature (CACNA1C/KCNH2; SEMA6D).

## Final integrator output

The `gpt-5` integrator produced an evidence-linked Markdown report
(`artifacts/integration/final_hypothesis_report.md`) that:

- restates the biological question;
- lists the 53 upregulated markers and their developmental categories;
- ties GeneAgent's 6 subprocesses to the upstream evidence;
- concludes that AVN/AV ring aFibro **exhibit a distinct developmental
  program** consistent with an "AV canal / nodal fibroblast" identity
  relative to LA/RA aFibro;
- explicitly enumerates limitations (cell-type purity, MERFISH panel
  constraints, batch handling, developmental-stage heterogeneity, lack
  of orthogonal validation).

## Artifact tree

```
runs/case1/heart_merfish/
  run_manifest.json                            — top-level outcome + paths (status: success)
  compiled_workflow_graph.json                 — L7 external_repo_collaboration nodes/edges
  agent_registry.json                          — AgentCards (auto-built from RepoProfile)
  manifests/
    repo_profile_TissueAgent.json
    repo_profile_GeneAgent.json
    docker_build_report.json                   — Dockerfiles written; daemon was down
    broker.jsonl                               — typed handoff audit log
  docker/
    Dockerfile.TissueAgent                     — generated from RepoProfile + SandboxSpec
    Dockerfile.GeneAgent
    docker-compose.yml
    smoke_TissueAgent.py
    smoke_GeneAgent.py
  artifacts/
    tissueagent_run/
      response.json
      dataset_schema_report.{json,md}
      group_definition_report.json
      group_counts.csv
      de_results.csv                           — 238 genes × Welch t / log2FC / adj-p
      de_sensitivity_summary.csv               — Welch t (53) vs Mann-Whitney (46)
      avn_avring_marker_genes.{csv,json}       — 53 markers ranked
      volcano_afibro_avn_avring_vs_atria.png   — volcano with expected markers labeled
      tissueagent_coding_report.md             — methodology + counts
      de_config.json
    geneagent_input.json                        — broker-validated handoff payload
    geneagent_run/
      response.json
      geneagent_report.md                       — gpt-5 gene-set interpretation
      geneagent_report.json
      geneagent_raw.txt                         — raw LLM JSON (audit)
    integration/
      final_hypothesis_report.md                — gpt-5 integrator output
      final_hypothesis_report.json
      final_summary.md
  traces/
    execution_trace.jsonl
```

## Reproduce

```bash
# 1. Place the Dryad download.
mkdir -p data/heart_merfish
# (Manual download of overall_merfish.h5ad ~370 MB into data/heart_merfish/.)

# 2. Install scanpy / anndata once.
pip install anndata scanpy h5py

# 3. Run.
export OPENAI_API_KEY="..."
agentcoop collaborate \
  --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

The same CLI runs **any** two-agent external collaboration: write a
`request.yaml` matching `case_study_1.md` §3.3, register a local
adapter per agent in `agentcoop/wrappers/<agent>/`, and invoke
`agentcoop collaborate --request <yaml>`. Nothing in the framework
code mentions TissueAgent or GeneAgent.
