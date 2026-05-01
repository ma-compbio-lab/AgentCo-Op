# Case Study 1: AgentCo-Op Reproduces the TissueAgent × GeneAgent External-Agent Collaboration Experiment

**Goal.** This case study tests whether **AgentCo-Op** can take two independently developed GitHub repositories, a public spatial transcriptomics dataset, and a natural-language task description, then autonomously synthesize a runnable collaborative multi-agent workflow. The target experiment is the **“TISSUEAGENT collaborates with externally developed agents”** case study from the TissueAgent manuscript: differential expression analysis on a developing human heart MERFISH dataset, followed by GeneAgent-based gene-set interpretation and final hypothesis synthesis.

**Primary claim to demonstrate.** AgentCo-Op should not simply call a single monolithic agent. It should discover, configure, sandbox, register, and coordinate **TissueAgent** and **GeneAgent** as specialized execution backends. The desired evidence is an auditable run showing that TissueAgent handles the spatial transcriptomics / differential-expression part, GeneAgent handles the gene-set interpretation part, and AgentCo-Op manages the handoff, validation, dynamic repair, and final synthesis.

**Repositories.**

- TissueAgent: `https://github.com/ma-compbio/TissueAgent`
- GeneAgent: `https://github.com/ncbi-nlp/GeneAgent`

**Main dataset.** Developing human heart MERFISH dataset from Farah et al., distributed through Dryad and linked from the TissueAgent README and manuscript.

- Dryad dataset page: `https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp`
- Recommended file for this case study: `overall_merfish.h5ad`.
- Optional metadata / README file: Dryad dataset `README.md`.
- Optional browser entry: `https://cells.ucsc.edu/?ds=hoc`

---

## 1. What exactly should be reproduced?

The TissueAgent manuscript describes an external-agent collaboration experiment on the developing human heart MERFISH dataset. The biological question is:

> Do atrial fibroblasts (`aFibro`) within the **AVN/AV ring cellular community** exhibit a distinct developmental program relative to atrial fibroblasts in the **Left Atria** and **Right Atria** cellular communities?

The reported TissueAgent workflow is:

1. Use TissueAgent’s coding / spatial analysis capability to perform differential expression.
2. Contrast `aFibro` cells in the `AVN/AV ring` cellular community against `aFibro` cells in the `Left Atria` and `Right Atria` cellular communities.
3. Identify AVN/AV ring marker genes at adjusted P-value `< 0.05`.
4. Produce a volcano plot.
5. Forward the marker-gene set to GeneAgent.
6. Use GeneAgent to perform gene-set analysis.
7. Use a hypothesis / integration agent to synthesize the differential-expression evidence and gene-set interpretation into a biological conclusion.

The TissueAgent manuscript reports **46 AVN/AV ring marker genes** at adjusted P-value `< 0.05`, with example positive markers including **DES, IGFBP5, NELL2, HAND2, MYH7, and MYH6**. GeneAgent reports an enrichment theme broadly consistent with **Cardiac Development and Remodeling**, including cardiac morphogenesis, conduction system specification, sarcomere and structural organization, extracellular matrix remodeling, fibrotic / growth-factor signaling, and electrophysiological regulation. The final interpretation is that `aFibro` cells in the AVN/AV ring cellular community occupy a specialized, developmentally programmed, ECM-rich, conduction-associated state rather than generic atrial stroma.

For AgentCo-Op, the success target is not only to reproduce this biological result. The more important systems result is to show that AgentCo-Op can convert two external repositories into callable execution nodes and orchestrate their collaboration through a compiled, auditable workflow.

---

## 2. Why this is the right Case Study 1 for AgentCo-Op

This case study is stronger than a simple bulk RNA-seq upstream/downstream analysis because it directly exercises AgentCo-Op’s core contribution:

1. **External specialized agent collaboration.** TissueAgent is specialized for spatial transcriptomics workflows. GeneAgent is specialized for gene-set function interpretation. AgentCo-Op should use each where it is strongest.
2. **Repository-to-agent synthesis.** The user provides GitHub repositories, not pre-packaged APIs. AgentCo-Op must inspect the repositories, infer dependencies, build sandboxes, define I/O contracts, and create wrappers.
3. **Upstream/downstream dependency.** TissueAgent produces a marker-gene set. GeneAgent consumes that gene set. AgentCo-Op must preserve artifact provenance across the handoff.
4. **Dynamic repair is natural.** The run can fail due to environment issues, dataset label mismatches, old OpenAI SDK usage in GeneAgent, missing columns, ambiguous community labels, or differential-expression method mismatch. AgentCo-Op should handle these through gated repair rather than manual intervention.
5. **Auditable biological interpretation.** The final output should link raw data, group definitions, DE statistics, marker genes, GeneAgent evidence, and the synthesized biological conclusion.

---

## 3. Inputs to AgentCo-Op

### 3.1 Minimal user-facing request

Use this as the main experiment prompt. It gives the biological task clearly but still requires AgentCo-Op to configure and compose the two external repositories.

```text
You are given two external agent repositories and a public spatial transcriptomics dataset.

Repositories:
1. TissueAgent: https://github.com/ma-compbio/TissueAgent
2. GeneAgent: https://github.com/ncbi-nlp/GeneAgent

Dataset:
- Developing human heart MERFISH dataset from Farah et al.
- Use the processed overall MERFISH AnnData file overall_merfish.h5ad from Dryad DOI 10.5061/dryad.w0vt4b8vp.

Task:
Reproduce the TissueAgent manuscript experiment in which TissueAgent collaborates with GeneAgent.
Test whether atrial fibroblasts (aFibro) in the AVN/AV ring cellular community exhibit a distinct developmental program compared with atrial fibroblasts in the Left Atria and Right Atria cellular communities.

Requirements:
- Configure isolated Docker sandboxes for TissueAgent and GeneAgent.
- Register TissueAgent as the spatial transcriptomics / differential-expression execution backend.
- Register GeneAgent as the gene-set analysis backend.
- Run differential expression for aFibro cells: AVN/AV ring versus Left Atria + Right Atria.
- Produce a DE table, marker-gene table, and volcano plot.
- Forward the AVN/AV ring marker gene set to GeneAgent.
- Produce a GeneAgent gene-set report.
- Produce a final integrated hypothesis report linking the DE evidence, GeneAgent gene-set interpretation, and the AVN/AV ring developmental biology claim.
- Save a complete trace, compiled workflow graph, environment manifests, and all artifacts.
```

### 3.2 Optional harder prompt for the autonomy experiment

Use this version only after the main run works. It tests whether AgentCo-Op can infer more from the TissueAgent paper / repo.

```text
Given the TissueAgent and GeneAgent GitHub repositories and the developing human heart MERFISH dataset, reproduce the external-agent collaboration experiment from the TissueAgent manuscript. Configure the required sandboxes, infer the workflow, run the analysis, and return all artifacts and an auditable report.
```

For this harder version, also provide the TissueAgent manuscript PDF or a text excerpt of the “TISSUEAGENT collaborates with externally developed agents” section. Otherwise the task may become a paper-reading / retrieval test rather than a repository-composition test.

### 3.3 Machine-readable request file

AgentCo-Op should support a request file like this:

```yaml
# case_study_1.request.yaml
case_id: tissueagent_geneagent_external_collaboration
mode: autonomous_repo_wrapping

repositories:
  - name: TissueAgent
    url: https://github.com/ma-compbio/TissueAgent
    role_hint: spatial_transcriptomics_workflow_agent
  - name: GeneAgent
    url: https://github.com/ncbi-nlp/GeneAgent
    role_hint: gene_set_analysis_agent

dataset:
  name: developing_human_heart_merfish_farah_2024
  source_page: https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp
  preferred_files:
    - overall_merfish.h5ad
    - README.md
  local_cache_dir: data/farah_human_heart_merfish

task:
  title: AVN/AV ring aFibro developmental-program test
  description: >
    Test whether atrial fibroblasts (aFibro) in the AVN/AV ring cellular community
    exhibit a distinct developmental program compared with atrial fibroblasts in
    the Left Atria and Right Atria cellular communities. Use TissueAgent for
    spatial transcriptomics differential-expression analysis and GeneAgent for
    gene-set interpretation.
  organism: human
  tissue: developing human heart
  target_population: aFibro
  target_community: AVN/AV ring
  control_communities: [Left Atria, Right Atria]

required_outputs:
  - compiled_workflow_graph.json
  - agent_registry.json
  - docker_build_report.json
  - dataset_schema_report.json
  - group_definition_report.json
  - de_results.csv
  - avn_avring_marker_genes.csv
  - volcano_afibro_avn_avring_vs_atria.png
  - geneagent_input_gene_set.json
  - geneagent_report.md
  - geneagent_report.json
  - final_hypothesis_report.md
  - final_reproducibility_notebook.ipynb
  - execution_trace.jsonl
  - run_manifest.json

success_targets:
  expected_marker_count: 46
  expected_example_markers: [DES, IGFBP5, NELL2, HAND2, MYH7, MYH6]
  expected_geneagent_theme: Cardiac Development and Remodeling
  required_collaboration: true
  max_replans: 2
```

---

## 4. Required AgentCo-Op adaptations

This case study requires AgentCo-Op to support more than ordinary tool calling. Add or verify the following framework modules.

### 4.1 Repository profiler

AgentCo-Op needs a `RepoProfiler` that reads each repository and emits a structured profile:

```json
{
  "repo_name": "TissueAgent",
  "repo_url": "https://github.com/ma-compbio/TissueAgent",
  "detected_language": ["Python", "TypeScript"],
  "environment_files": ["environment.yml", "pyproject.toml", "uv.lock", "flake.nix"],
  "run_modes": ["headless_notebook", "fastapi_backend", "web_ui"],
  "candidate_capabilities": [
    "spatial transcriptomics workflow orchestration",
    "differential expression analysis",
    "hypothesis synthesis",
    "artifact-producing coding workflow"
  ],
  "requires_api_key": true,
  "preferred_wrapper_strategy": "headless_python_or_notebook"
}
```

For GeneAgent, the profile should capture that it is a Python 3.11 repository with scripts such as `main_cascade.py`, `main_summary.py`, and `worker.py`, and that the original repository expects OpenAI / Azure OpenAI configuration in code. The wrapper should replace hard-coded credentials with environment variables.

### 4.2 Sandbox builder

AgentCo-Op needs a `SandboxBuilder` that can generate and test Docker images. Each sandbox must have:

- a cloned repository;
- a reproducible environment manifest;
- a smoke test;
- a wrapper entrypoint accepting JSON input and writing JSON / Markdown / file artifacts;
- access to a shared read-only dataset mount;
- access to a shared writable artifact mount;
- no access to unrelated host files;
- optional network access only when needed for package installation, LLM APIs, or GeneAgent database queries.

A minimal directory layout:

```text
runs/case_study_1/
  repos/
    TissueAgent/
    GeneAgent/
  docker/
    Dockerfile.tissueagent
    Dockerfile.geneagent
    docker-compose.yml
  wrappers/
    tissueagent_worker.py
    geneagent_worker.py
    common_io.py
  data/
    farah_human_heart_merfish/
      overall_merfish.h5ad
      README.md
  artifacts/
  traces/
  manifests/
```

### 4.3 Agent-card registry

AgentCo-Op should convert each repo into an `AgentCard`.

```yaml
# agent_specs/tissueagent.yaml
name: TissueAgent
kind: sandboxed_external_agent
repository: https://github.com/ma-compbio/TissueAgent
container: agentcoop-tissueagent:case-study-1
role: Spatial transcriptomics workflow and differential-expression specialist
capabilities:
  - inspect_anndata_spatial_dataset
  - identify_cell_population_and_community_groups
  - run_differential_expression
  - generate_volcano_plot
  - synthesize_spatial_biology_report
input_schema:
  type: object
  required: [dataset_path, task_description, output_dir]
  properties:
    dataset_path: {type: string}
    task_description: {type: string}
    population_label: {type: string}
    target_community: {type: string}
    control_communities: {type: array, items: {type: string}}
    expected_outputs: {type: array, items: {type: string}}
output_schema:
  type: object
  required: [status, artifacts, summary]
  properties:
    status: {enum: [success, failed, partial]}
    artifacts:
      type: object
      properties:
        dataset_schema_report: {type: string}
        group_definition_report: {type: string}
        de_results_csv: {type: string}
        marker_genes_csv: {type: string}
        marker_genes_json: {type: string}
        volcano_png: {type: string}
        coding_report_md: {type: string}
    summary: {type: string}
```

```yaml
# agent_specs/geneagent.yaml
name: GeneAgent
kind: sandboxed_external_agent
repository: https://github.com/ncbi-nlp/GeneAgent
container: agentcoop-geneagent:case-study-1
role: Gene-set interpretation and biological-process annotation specialist
capabilities:
  - analyze_human_gene_set
  - query_biological_databases
  - self_verify_gene_set_annotation
  - produce_interpretable_gene_set_report
input_schema:
  type: object
  required: [gene_set, organism, biological_context, output_dir]
  properties:
    gene_set: {type: array, items: {type: string}}
    organism: {type: string}
    biological_context: {type: string}
    contrast_description: {type: string}
    output_dir: {type: string}
output_schema:
  type: object
  required: [status, artifacts, summary]
  properties:
    status: {enum: [success, failed, partial]}
    artifacts:
      type: object
      properties:
        geneagent_report_md: {type: string}
        geneagent_report_json: {type: string}
        verification_report: {type: string}
    summary: {type: string}
```

### 4.4 Artifact broker

The artifact broker is responsible for preserving typed outputs and handoffs:

```text
TissueAgent output: avn_avring_marker_genes.csv
        ↓
Artifact broker validates gene-symbol column and converts to JSON.
        ↓
GeneAgent input: geneagent_input_gene_set.json
        ↓
GeneAgent output: geneagent_report.json + geneagent_report.md
        ↓
Integrator input: DE table + marker table + GeneAgent report + task description
```

The broker should never pass an unvalidated free-text gene list. It should check gene symbols, remove duplicates, preserve original order, and write both the full marker list and the filtered AVN/AV ring upregulated marker list.

### 4.5 Dynamic repair gates

This case study should include gated repair. Suggested gates:

| Gate | Check | Repair action |
|---|---|---|
| `G0_repo_profile` | Both repos cloned and environment files detected | Retry clone; initialize submodules; switch to shallow clone if needed |
| `G1_sandbox_build` | Docker images build and smoke tests pass | Try alternate environment strategy: conda → uv → pip-only |
| `G2_data_schema` | `overall_merfish.h5ad` loads; expected columns or near-equivalent labels found | Run label-discovery agent; inspect `.obs` unique values; ask no human unless no viable mapping exists |
| `G3_group_definition` | Nonzero target and control `aFibro` cells | Normalize labels; use regex matching; verify `populations` and `communities` columns |
| `G4_de_artifacts` | DE table, marker table, volcano plot exist | Retry with fallback DE method; rerun plotting only if statistics succeeded |
| `G5_marker_recovery` | Marker count close to reported 46 and expected example markers recovered | Try TissueAgent-native DE function or Scanpy `rank_genes_groups`; compare Welch, Wilcoxon, and t-test results |
| `G6_geneagent` | GeneAgent returns report with biological-process interpretation and evidence | Patch API client; lower model; retry with cached database calls; serialize output to Markdown if JSON parse fails |
| `G7_final_report` | Final report explicitly links DE, gene-set interpretation, and biological claim | Re-run integrator with stricter evidence checklist |

---

## 5. Dataset acquisition

### 5.1 Recommended input file

Use only `overall_merfish.h5ad` for the main case study. The full Dryad collection is much larger, but `overall_merfish.h5ad` is sufficient because it contains the 13 PCW MERFISH cells and the annotations needed for the aFibro / cellular-community contrast.

Expected file size is approximately **370 MB**. The Dryad README describes `overall_merfish.h5ad` as a Scanpy AnnData object containing three replicate MERFISH experiments of a 13 PCW heart and observation metadata including:

- `sample_id`
- `batch`
- `n_counts`
- `leiden`
- `zone_cluster`
- `communities`
- `complexity`
- `populations`
- `purity`

### 5.2 Data download command

AgentCo-Op should first resolve files from the Dryad dataset page rather than hard-coding file-stream IDs. However, for a manual run, the Dryad file link exposed by the page can be used.

```bash
mkdir -p data/farah_human_heart_merfish
cd data/farah_human_heart_merfish

# Preferred robust behavior: use AgentCo-Op's downloader to resolve file links from the Dryad page.
agentcoop data download \
  --source-page "https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp" \
  --include "overall_merfish.h5ad" \
  --outdir .

# Manual fallback: open the Dryad page in a browser and download overall_merfish.h5ad.
# Store it at:
# data/farah_human_heart_merfish/overall_merfish.h5ad
```

### 5.3 Data integrity checks

AgentCo-Op should produce `dataset_schema_report.json` with at least:

```json
{
  "file": "overall_merfish.h5ad",
  "n_obs": 0,
  "n_vars": 0,
  "obs_columns": [],
  "var_columns": [],
  "layers": [],
  "obsm_keys": [],
  "detected_population_column": "populations",
  "detected_community_column": "communities",
  "detected_sample_column": "sample_id",
  "unique_population_values": [],
  "unique_community_values": [],
  "warnings": []
}
```

The actual `n_obs` and `n_vars` should be filled after loading. If `n_vars` is near 238, the object is consistent with the MERFISH panel described in the dataset README.

---

## 6. Dataset processing and group definition

### 6.1 Required labels

The primary contrast is:

- Target group: `populations == aFibro` and `communities == AVN/AV ring`
- Control group: `populations == aFibro` and `communities in {Left Atria, Right Atria}`

The exact strings may differ slightly across files or code paths. AgentCo-Op should discover the labels rather than assuming exact spelling. Use case-insensitive normalization and punctuation-insensitive matching.

Suggested normalization:

```python
def norm_label(x: str) -> str:
    return (
        str(x).lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
    )
```

Suggested mappings:

```python
population_aliases = {
    "afibro": ["afibro", "atrialfibroblast", "atrialfibroblasts"]
}
community_aliases = {
    "avn_av_ring": ["avnavring", "avnavr", "avnav", "avnarring"],
    "left_atria": ["leftatria", "la", "leftatrium"],
    "right_atria": ["rightatria", "ra", "rightatrium"]
}
```

Write the final discovered mapping to `group_definition_report.json`:

```json
{
  "population_column": "populations",
  "community_column": "communities",
  "sample_column": "sample_id",
  "target_population_requested": "aFibro",
  "target_population_matched": "aFibro",
  "target_community_requested": "AVN/AV ring",
  "target_community_matched": "AVN/AVring",
  "control_communities_requested": ["Left Atria", "Right Atria"],
  "control_communities_matched": ["Left Atria", "Right Atria"],
  "n_target_cells": 0,
  "n_control_cells": 0,
  "n_target_by_sample": {},
  "n_control_by_sample": {},
  "warnings": []
}
```

### 6.2 Expression matrix selection

Use the following priority:

1. If `adata.layers["counts"]` exists, use it for count-aware normalization.
2. Else if `adata.raw` exists and has matching genes, use `adata.raw.X`.
3. Else use `adata.X` and record that no explicit raw-count layer was found.

For the reproduction target, the simplest and most likely robust path is:

```python
adata_sub = adata[mask_target | mask_control].copy()
adata_sub.obs["de_group"] = np.where(mask_target[adata_sub.obs_names], "AVN_AVring", "Atria")

# If using raw counts:
sc.pp.normalize_total(adata_sub, target_sum=1e4)
sc.pp.log1p(adata_sub)
```

If the object is already log-normalized, avoid double log-transforming. AgentCo-Op should record its choice in `de_config.json`.

### 6.3 Differential-expression method

The TissueAgent manuscript reports the DE result but does not fully specify the exact statistical implementation in the excerpted methods. Therefore, use a two-tier protocol:

**Primary reproduction method.** Use a cell-level differential-expression method designed to reproduce the manuscript-style output:

- two-sided Welch t-test or Scanpy `rank_genes_groups` t-test / Wilcoxon;
- Benjamini-Hochberg adjusted P-value over all MERFISH genes;
- log2 fold change defined as `mean(target + eps) / mean(control + eps)` on normalized expression or an equivalent Scanpy logfoldchange field;
- AVN/AV ring marker genes defined as `adj_p < 0.05` and `log2FC > 0`.

**Recommended default.** Start with Welch t-test on log-normalized expression, because it is transparent and easy to audit. If the marker count or example markers do not match the TissueAgent target, trigger the marker-recovery gate and compare alternative methods:

1. Welch t-test on log-normalized expression.
2. Mann-Whitney / Wilcoxon rank-sum test.
3. Scanpy `tl.rank_genes_groups(..., method="t-test")`.
4. Scanpy `tl.rank_genes_groups(..., method="wilcoxon")`.

Choose the method that best matches the TissueAgent manuscript target, but report all alternatives in `de_sensitivity_summary.csv`.

**Biological caution.** Cell-level DE can overstate significance because cells from the same section are not independent biological replicates. For the manuscript-reproduction experiment, the cell-level result is acceptable because the goal is to match TissueAgent’s reported workflow. For a biology-facing paper, also include a pseudobulk sensitivity analysis by `sample_id` if enough target/control cells exist in each replicate.

### 6.4 Volcano plot requirements

Save `volcano_afibro_avn_avring_vs_atria.png`.

Required visual elements:

- x-axis: `log2 fold change (AVN/AV ring vs Atria)`
- y-axis: `-log10(adjusted p-value)` or `-log10(p-value)` if matching TissueAgent’s plot style; label clearly
- red: AVN/AV ring upregulated significant genes
- blue: atria upregulated significant genes
- gray: non-significant genes
- vertical dashed line at `log2FC = 0`
- horizontal dashed line at adjusted P-value `0.05`
- title: `aFibro: AVN/AV ring vs Atria`
- annotate or list expected top markers in the report: `DES`, `IGFBP5`, `NELL2`, `HAND2`, `MYH7`, `MYH6`

---

## 7. Compiled workflow topology

AgentCo-Op should compile an upstream/downstream workflow with a validation loop:

```text
User task + repos + dataset
        ↓
RepoProfiler
        ↓
SandboxBuilder
        ↓
AgentRegistryBuilder
        ↓
Planner / Compiler
        ↓
DatasetInspector  [TissueAgent sandbox]
        ↓
DEAnalysis        [TissueAgent sandbox]
        ↓
Gate: DE artifacts + marker recovery
        ↓
GeneSetAnalysis   [GeneAgent sandbox]
        ↓
Gate: gene-set report quality
        ↓
HypothesisSynthesizer [TissueAgent Hypothesis Agent or AgentCo-Op Integrator]
        ↓
Reporter
```

The workflow graph should be exported as `compiled_workflow_graph.json`:

```json
{
  "case_id": "tissueagent_geneagent_external_collaboration",
  "topology_type": "upstream_downstream_external_agent_collaboration",
  "nodes": [
    {
      "id": "repo_profiler",
      "kind": "agentcoop_internal",
      "role": "inspect external repositories and infer execution strategy"
    },
    {
      "id": "sandbox_builder",
      "kind": "agentcoop_internal",
      "role": "build and smoke-test Docker sandboxes"
    },
    {
      "id": "tissueagent_dataset_de",
      "kind": "sandboxed_external_agent",
      "agent": "TissueAgent",
      "role": "inspect MERFISH dataset and run differential expression"
    },
    {
      "id": "de_quality_gate",
      "kind": "evaluator_gate",
      "role": "validate DE outputs and marker recovery"
    },
    {
      "id": "geneagent_gsa",
      "kind": "sandboxed_external_agent",
      "agent": "GeneAgent",
      "role": "interpret AVN/AV ring marker-gene set"
    },
    {
      "id": "geneagent_quality_gate",
      "kind": "evaluator_gate",
      "role": "validate GeneAgent output"
    },
    {
      "id": "hypothesis_integrator",
      "kind": "llm_backed_agent_node",
      "role": "synthesize DE, gene-set analysis, and biological conclusion"
    },
    {
      "id": "reporter",
      "kind": "agentcoop_internal",
      "role": "package artifacts, trace, and final report"
    }
  ],
  "edges": [
    ["repo_profiler", "sandbox_builder"],
    ["sandbox_builder", "tissueagent_dataset_de"],
    ["tissueagent_dataset_de", "de_quality_gate"],
    ["de_quality_gate", "geneagent_gsa"],
    ["geneagent_gsa", "geneagent_quality_gate"],
    ["geneagent_quality_gate", "hypothesis_integrator"],
    ["hypothesis_integrator", "reporter"]
  ],
  "gated_repairs": {
    "de_quality_gate": ["label_mapping_repair", "de_method_repair", "plot_repair"],
    "geneagent_quality_gate": ["api_patch_repair", "json_parse_repair", "evidence_completion_repair"]
  }
}
```

---

## 8. Docker sandbox design

### 8.1 Docker Compose skeleton

```yaml
# docker-compose.yml
services:
  tissueagent:
    build:
      context: ..
      dockerfile: docker/Dockerfile.tissueagent
    image: agentcoop-tissueagent:case-study-1
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_BASE_URL: ${OPENAI_BASE_URL:-}
      AGENTCOOP_RUN_ID: ${AGENTCOOP_RUN_ID:-case_study_1}
    volumes:
      - ../data:/workspace/data:ro
      - ../artifacts:/workspace/artifacts:rw
      - ../traces:/workspace/traces:rw
      - ../wrappers:/workspace/wrappers:ro
    command: ["python", "/workspace/wrappers/tissueagent_worker.py", "--serve"]

  geneagent:
    build:
      context: ..
      dockerfile: docker/Dockerfile.geneagent
    image: agentcoop-geneagent:case-study-1
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_BASE_URL: ${OPENAI_BASE_URL:-}
      OPENAI_API_VERSION: ${OPENAI_API_VERSION:-}
      AGENTCOOP_RUN_ID: ${AGENTCOOP_RUN_ID:-case_study_1}
    volumes:
      - ../data:/workspace/data:ro
      - ../artifacts:/workspace/artifacts:rw
      - ../traces:/workspace/traces:rw
      - ../wrappers:/workspace/wrappers:ro
    command: ["python", "/workspace/wrappers/geneagent_worker.py", "--serve"]
```

### 8.2 TissueAgent Dockerfile skeleton

TissueAgent’s README supports conda, uv, Nix, and headless notebook usage. For this case study, use headless mode; no frontend is needed.

```dockerfile
# docker/Dockerfile.tissueagent
FROM mambaorg/micromamba:1.5.8

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN git clone --recurse-submodules https://github.com/ma-compbio/TissueAgent.git
WORKDIR /workspace/TissueAgent

# Prefer the repository's environment file.
RUN micromamba create -y -n tissueagent -f environment.yml && micromamba clean -a -y
SHELL ["micromamba", "run", "-n", "tissueagent", "/bin/bash", "-lc"]

RUN pip install -e .

# Extra lightweight packages for wrappers and statistical fallbacks.
RUN pip install --no-cache-dir \
    anndata scanpy scipy statsmodels pandas numpy matplotlib seaborn pydantic fastapi uvicorn papermill nbformat

WORKDIR /workspace
CMD ["python", "/workspace/wrappers/tissueagent_worker.py", "--serve"]
```

If conda environment solving fails, AgentCo-Op should dynamically replan to the `uv` strategy described by the TissueAgent README:

```bash
uv sync
source .venv/bin/activate
pip install -e .
```

### 8.3 GeneAgent Dockerfile skeleton

GeneAgent’s README lists Python 3.11 and dependencies including `openai==0.28.0`, `torch==1.13.0`, `numpy`, `pandas`, `requests`, `requests-oauthlib`, `seaborn`, and `tiktoken`. Build a separate environment to avoid dependency conflicts with TissueAgent.

```dockerfile
# docker/Dockerfile.geneagent
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN git clone https://github.com/ncbi-nlp/GeneAgent.git
WORKDIR /workspace/GeneAgent

RUN pip install --no-cache-dir \
    openai==0.28.0 \
    numpy==1.26.3 \
    pandas==2.1.4 \
    requests==2.31.0 \
    requests-oauthlib==1.3.1 \
    seaborn==0.13.2 \
    tiktoken==0.7.0 \
    pydantic fastapi uvicorn

# Torch is included in GeneAgent's requirements but may be unnecessary for the wrapper.
# If GeneAgent code imports torch, install a CPU wheel. AgentCo-Op should repair this if needed.
RUN pip install --no-cache-dir torch==1.13.0 || true

WORKDIR /workspace
CMD ["python", "/workspace/wrappers/geneagent_worker.py", "--serve"]
```

### 8.4 Smoke tests

TissueAgent smoke test:

```bash
docker run --rm agentcoop-tissueagent:case-study-1 \
  python - <<'PY'
import anndata, scanpy, pandas, numpy, scipy, statsmodels
print('tissueagent-smoke-test-ok')
PY
```

GeneAgent smoke test:

```bash
docker run --rm agentcoop-geneagent:case-study-1 \
  python - <<'PY'
import openai, pandas, numpy, requests, tiktoken
print('geneagent-smoke-test-ok')
PY
```

Both results should be stored in `docker_build_report.json`.

---

## 9. Wrapper interface

Each sandboxed repository should expose the same simple invocation contract. AgentCo-Op can implement it as a CLI, JSON-RPC service, FastAPI endpoint, or direct `docker exec` call. The key is that the interface is standardized.

### 9.1 Common request format

```json
{
  "task_id": "case_study_1_de",
  "capability": "run_differential_expression",
  "input": {},
  "output_dir": "/workspace/artifacts/tissueagent_de"
}
```

### 9.2 Common response format

```json
{
  "status": "success",
  "summary": "Differential expression completed for aFibro AVN/AV ring vs atria.",
  "artifacts": {
    "de_results_csv": "/workspace/artifacts/tissueagent_de/de_results.csv",
    "marker_genes_csv": "/workspace/artifacts/tissueagent_de/avn_avring_marker_genes.csv",
    "volcano_png": "/workspace/artifacts/tissueagent_de/volcano_afibro_avn_avring_vs_atria.png"
  },
  "warnings": []
}
```

### 9.3 TissueAgent wrapper behavior

The TissueAgent wrapper should first try to use TissueAgent-native code or notebook invocation if a clear API exists. If not, it should run a generated headless analysis script inside the TissueAgent environment. This still counts as a TissueAgent execution backend if:

- the repository’s environment is used;
- TissueAgent’s available utilities / agent code are inspected and used where feasible;
- the execution is registered as the TissueAgent spatial analysis node;
- all actions are logged in the AgentCo-Op trace.

Pseudo-entrypoint:

```python
# wrappers/tissueagent_worker.py

def invoke_run_differential_expression(req):
    dataset_path = req["input"]["dataset_path"]
    output_dir = req["output_dir"]
    params = req["input"]

    # 1. Load AnnData.
    # 2. Discover columns and labels.
    # 3. Build target/control masks.
    # 4. Run DE.
    # 5. Save schema report, group report, DE table, markers, volcano, coding report.
    # 6. Return structured artifact paths.
```

### 9.4 GeneAgent wrapper behavior

GeneAgent’s original scripts are not designed as a clean microservice. AgentCo-Op should create a thin wrapper that:

1. Accepts a JSON gene set and context.
2. Writes the gene set into the format expected by GeneAgent.
3. Injects `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and optional Azure settings from environment variables rather than editing source files manually.
4. Runs the GeneAgent cascade / summary path.
5. Parses or captures the output.
6. Writes both Markdown and JSON.
7. Records any self-verification / database evidence produced by GeneAgent.

Pseudo-entrypoint:

```python
# wrappers/geneagent_worker.py

def invoke_gene_set_analysis(req):
    gene_set = req["input"]["gene_set"]
    context = req["input"].get("biological_context", "")
    output_dir = req["output_dir"]

    # 1. Validate gene symbols.
    # 2. Write gene set to GeneAgent-compatible input.
    # 3. Patch runtime config from env variables if needed.
    # 4. Run GeneAgent or a faithful wrapper around its prompts / verification workflow.
    # 5. Save geneagent_report.md and geneagent_report.json.
```

If exact script-level integration is brittle, the acceptable repair is to implement a wrapper that reuses GeneAgent’s prompt files, database API modules, and verification logic while replacing only the fragile API-key handling. This should be documented as `api_patch_repair` in the trace.

---

## 10. Detailed execution protocol

### Step 0: Prepare runtime secrets

```bash
export OPENAI_API_KEY="..."
# Optional if using a compatible endpoint:
export OPENAI_BASE_URL="..."
export OPENAI_API_VERSION="..."
```

### Step 1: Start AgentCo-Op run

```bash
agentcoop run \
  --request case_study_1.request.yaml \
  --workdir runs/case_study_1 \
  --sandbox docker \
  --allow-network true \
  --max-replans 2
```

### Step 2: Repository profiling

Expected artifacts:

```text
runs/case_study_1/manifests/repo_profile_TissueAgent.json
runs/case_study_1/manifests/repo_profile_GeneAgent.json
runs/case_study_1/manifests/repo_profile_summary.md
```

Minimum checks:

- TissueAgent clone succeeds with submodules.
- TissueAgent `environment.yml`, `pyproject.toml`, `uv.lock`, and source tree are detected.
- GeneAgent clone succeeds.
- GeneAgent scripts and dependency requirements are detected.
- Both repos are marked as external sandboxed agents.

### Step 3: Docker build and smoke tests

Expected artifacts:

```text
runs/case_study_1/manifests/docker_build_report.json
runs/case_study_1/docker/Dockerfile.tissueagent
runs/case_study_1/docker/Dockerfile.geneagent
runs/case_study_1/docker/docker-compose.yml
```

Minimum checks:

- TissueAgent image built.
- GeneAgent image built.
- Smoke tests pass.
- AgentCo-Op records any build repairs.

### Step 4: Dataset download and validation

Expected artifacts:

```text
runs/case_study_1/data/farah_human_heart_merfish/overall_merfish.h5ad
runs/case_study_1/artifacts/tissueagent_de/dataset_schema_report.json
runs/case_study_1/artifacts/tissueagent_de/dataset_schema_report.md
```

Minimum checks:

- AnnData loads successfully.
- `populations` and `communities` are detected or mapped to equivalent columns.
- `aFibro`, `AVN/AV ring`, `Left Atria`, and `Right Atria` or close label variants are detected.

### Step 5: Group definition

Expected artifacts:

```text
runs/case_study_1/artifacts/tissueagent_de/group_definition_report.json
runs/case_study_1/artifacts/tissueagent_de/group_counts.csv
```

The report must include target and control cell counts. If either group has zero cells, AgentCo-Op should trigger `label_mapping_repair` and inspect all unique values in the relevant columns.

### Step 6: Differential expression and volcano plot

Expected artifacts:

```text
runs/case_study_1/artifacts/tissueagent_de/de_config.json
runs/case_study_1/artifacts/tissueagent_de/de_results.csv
runs/case_study_1/artifacts/tissueagent_de/avn_avring_marker_genes.csv
runs/case_study_1/artifacts/tissueagent_de/avn_avring_marker_genes.json
runs/case_study_1/artifacts/tissueagent_de/volcano_afibro_avn_avring_vs_atria.png
runs/case_study_1/artifacts/tissueagent_de/tissueagent_coding_report.md
runs/case_study_1/artifacts/tissueagent_de/de_sensitivity_summary.csv
```

`de_results.csv` should contain at least:

```text
gene,mean_target,mean_control,log2fc,statistic,p_value,adj_p_value,is_significant,direction
```

`avn_avring_marker_genes.csv` should contain at least:

```text
gene,log2fc,adj_p_value,p_value,rank
```

The coding report should explicitly state:

- which columns were used;
- exact target and control definitions;
- number of target and control cells;
- expression preprocessing;
- statistical test;
- multiple-testing correction;
- number of AVN/AV ring markers;
- whether the expected example markers were recovered;
- where the volcano plot is saved.

### Step 7: DE quality gate

The gate should classify the result:

```json
{
  "route": "PASS",
  "marker_count": 46,
  "expected_marker_overlap": ["DES", "IGFBP5", "NELL2", "HAND2", "MYH7", "MYH6"],
  "artifacts_exist": true,
  "repair_attempts": []
}
```

If not exact, the gate can still pass with caveats if the result is biologically consistent. Suggested thresholds:

- strict reproduction: marker count exactly 46 and at least 5/6 expected example markers recovered;
- acceptable reproduction: marker count within 40–60 and at least 4/6 expected example markers recovered;
- fail / repair: missing marker table, missing volcano plot, zero group counts, marker count wildly different, or no expected markers recovered.

### Step 8: GeneAgent handoff

The artifact broker converts the marker table into GeneAgent input.

Expected artifact:

```text
runs/case_study_1/artifacts/geneagent/geneagent_input_gene_set.json
```

Example:

```json
{
  "organism": "human",
  "gene_set_name": "aFibro_AVN_AVring_upregulated_markers",
  "gene_set": ["DES", "IGFBP5", "NELL2", "HAND2", "MYH7", "MYH6"],
  "source": {
    "dataset": "Farah et al. developing human heart MERFISH overall_merfish.h5ad",
    "contrast": "aFibro in AVN/AV ring cellular community vs aFibro in Left Atria and Right Atria cellular communities",
    "selection_rule": "adj_p_value < 0.05 and log2fc > 0"
  },
  "biological_context": "Developing human heart, atrioventricular canal / atrioventricular node region, atrial fibroblasts, spatial cellular communities",
  "requested_output": "Interpret the marker genes as biological processes or pathways, self-verify with domain databases where possible, and return a concise report."
}
```

### Step 9: GeneAgent gene-set analysis

Expected artifacts:

```text
runs/case_study_1/artifacts/geneagent/geneagent_report.md
runs/case_study_1/artifacts/geneagent/geneagent_report.json
runs/case_study_1/artifacts/geneagent/geneagent_verification_report.md
runs/case_study_1/artifacts/geneagent/geneagent_stdout.log
runs/case_study_1/artifacts/geneagent/geneagent_stderr.log
```

`geneagent_report.json` should contain:

```json
{
  "status": "success",
  "process_label": "Cardiac Development and Remodeling",
  "subprocesses": [
    {
      "name": "Cardiac morphogenesis and differentiation",
      "supporting_genes": [],
      "evidence_summary": ""
    },
    {
      "name": "Conduction system specification or electrophysiological regulation",
      "supporting_genes": [],
      "evidence_summary": ""
    },
    {
      "name": "Sarcomere and structural organization",
      "supporting_genes": [],
      "evidence_summary": ""
    },
    {
      "name": "Extracellular matrix remodeling and fibrotic signaling",
      "supporting_genes": [],
      "evidence_summary": ""
    },
    {
      "name": "Growth-factor signaling",
      "supporting_genes": [],
      "evidence_summary": ""
    }
  ],
  "self_verification": {
    "databases_or_sources_used": [],
    "verification_summary": ""
  },
  "caveats": []
}
```

### Step 10: GeneAgent quality gate

The gate should pass if:

- GeneAgent produced a report;
- the report interprets the gene set as human gene symbols;
- the report is specific to cardiac / developmental / ECM / conduction biology;
- the report includes at least several supporting genes;
- the report does not make unsupported diagnostic or clinical claims;
- the report is saved as both Markdown and JSON, or JSON can be reconstructed from Markdown.

Suggested automatic category-coverage check:

```json
{
  "route": "PASS",
  "expected_theme_found": true,
  "category_coverage": {
    "cardiac_morphogenesis": true,
    "conduction_or_electrophysiology": true,
    "sarcomere_or_structural": true,
    "ecm_or_fibrotic": true,
    "growth_factor_signaling": true
  },
  "warnings": []
}
```

### Step 11: Final hypothesis synthesis

The integrator can be a TissueAgent Hypothesis Agent if exposed cleanly, or an AgentCo-Op high-level integrator. The important requirement is that it consumes both upstream artifacts and produces a structured, evidence-linked conclusion.

Expected artifacts:

```text
runs/case_study_1/artifacts/integration/final_hypothesis_report.md
runs/case_study_1/artifacts/integration/final_hypothesis_report.json
runs/case_study_1/artifacts/integration/final_summary.md
```

The final conclusion should be phrased as evidence support, not overclaiming:

```text
The differential-expression and GeneAgent gene-set analyses support the interpretation that aFibro cells within the AVN/AV ring cellular community exhibit a specialized developmental, ECM-rich, and conduction-associated program relative to atrial fibroblasts in the Left/Right Atria communities. This is consistent with the TissueAgent manuscript’s interpretation that the AVN/AV ring community may represent a developmental structure associated with atrioventricular node formation rather than generic atrial stroma.
```

---

## 11. Prompt templates

### 11.1 AgentCo-Op compiler prompt

```text
You are the AgentCo-Op workflow compiler.

Given a task, external repositories, and datasets, compile the simplest workflow that can satisfy the task while preserving specialization and artifact provenance.

Rules:
1. Use a single-agent or code-only workflow only if the task is simple and does not require external specialization.
2. For this task, use an upstream/downstream collaboration topology because one specialist must produce a marker-gene set and another specialist must interpret that gene set.
3. Treat each GitHub repository as a candidate execution backend. Profile it, build a sandbox, and expose a standardized JSON invocation interface.
4. Do not manually stitch free-text outputs. Use typed artifacts: DE table, marker-gene CSV/JSON, GeneAgent input JSON, GeneAgent report JSON/Markdown.
5. Add gates after environment build, dataset schema validation, differential expression, GeneAgent interpretation, and final synthesis.
6. If a gate fails, perform targeted repair only. Do not restart the whole workflow unless necessary.
7. Return the compiled workflow graph, agent registry, execution plan, and expected artifacts before execution.
```

### 11.2 TissueAgent differential-expression node prompt

```text
You are the TissueAgent spatial transcriptomics execution node inside AgentCo-Op.

Task:
Load the developing human heart MERFISH AnnData file and test whether aFibro cells in the AVN/AV ring cellular community differ from aFibro cells in the Left Atria and Right Atria cellular communities.

Inputs:
- dataset_path: {dataset_path}
- output_dir: {output_dir}
- population label: aFibro
- target community: AVN/AV ring
- control communities: Left Atria, Right Atria

Required actions:
1. Load the AnnData object.
2. Inspect .obs columns and identify the population, community, and sample columns.
3. Discover exact label strings for aFibro, AVN/AV ring, Left Atria, and Right Atria.
4. Create target and control masks.
5. Report cell counts overall and by sample_id.
6. Select an expression matrix and record whether it is raw, normalized, or log-normalized.
7. Run differential expression for target vs control.
8. Adjust P-values with Benjamini-Hochberg FDR.
9. Define AVN/AV ring marker genes as adjusted P-value < 0.05 and log2FC > 0.
10. Save a full DE table, marker-gene table, marker-gene JSON, and volcano plot.
11. Produce a concise coding report with all paths and assumptions.

Expected reproduction target:
- The TissueAgent manuscript reports 46 AVN/AV ring markers at adjusted P < 0.05.
- Example positive markers shown in the manuscript include DES, IGFBP5, NELL2, HAND2, MYH7, and MYH6.

Output must be valid JSON with status, summary, artifact paths, marker_count, expected_marker_overlap, and warnings.
```

### 11.3 DE evaluator gate prompt

```text
You are the DE quality gate for AgentCo-Op.

Evaluate the TissueAgent DE node output against the task and manuscript reproduction target.

Pass criteria:
- dataset schema report exists;
- group definition report exists;
- target and control groups have nonzero cells;
- DE table exists;
- AVN/AV ring marker table exists;
- volcano plot exists;
- marker_count is exactly 46 for strict pass, or close to 46 with a clear caveat;
- expected example markers DES, IGFBP5, NELL2, HAND2, MYH7, MYH6 are substantially recovered.

If the result fails, return ROUTE: REPAIR and a targeted repair instruction.
If it passes, return ROUTE: PASS and specify the marker-gene artifact to send to GeneAgent.
```

### 11.4 GeneAgent node prompt

```text
You are the GeneAgent gene-set analysis execution node inside AgentCo-Op.

Analyze the following human marker-gene set:
{gene_set}

Biological context:
The genes are AVN/AV ring-upregulated markers from atrial fibroblasts in a developing human heart MERFISH dataset. They were identified by contrasting aFibro cells in the AVN/AV ring cellular community against aFibro cells in the Left Atria and Right Atria cellular communities.

Required output:
1. A concise biological process label for the gene set.
2. Subprocesses or pathways supported by the genes.
3. Supporting genes for each subprocess.
4. Evidence from biological databases or GeneAgent self-verification, where available.
5. Caveats and limitations.
6. A final interpretation focused on whether the marker set supports a developmental, ECM-rich, conduction-associated atrial fibroblast state.

Avoid clinical claims. This is a research dataset and the output is for biological interpretation only.

Return both Markdown and JSON.
```

### 11.5 Final integrator prompt

```text
You are the AgentCo-Op final integrator.

Inputs:
- Original task description.
- TissueAgent dataset schema report.
- TissueAgent group definition report.
- TissueAgent DE results and marker-gene table.
- TissueAgent volcano plot path.
- GeneAgent gene-set report.

Write a final auditable hypothesis report that:
1. Restates the biological question.
2. Describes the target and control groups.
3. Summarizes the DE result, including marker count and representative markers.
4. Summarizes GeneAgent's gene-set interpretation.
5. Explains how the two evidence streams support or weaken the hypothesis.
6. States the final conclusion with appropriate caution.
7. Lists all artifact paths.
8. Lists limitations, including the cell-level DE caveat and any mismatch from the manuscript target.
```

---

## 12. Expected AgentCo-Op output package

AgentCo-Op should return a directory like:

```text
runs/case_study_1/
  run_manifest.json
  compiled_workflow_graph.json
  agent_registry.json

  manifests/
    repo_profile_TissueAgent.json
    repo_profile_GeneAgent.json
    docker_build_report.json
    dependency_lock_summary.md

  data/
    farah_human_heart_merfish/
      overall_merfish.h5ad
      README.md

  artifacts/
    tissueagent_de/
      dataset_schema_report.json
      dataset_schema_report.md
      group_definition_report.json
      group_counts.csv
      de_config.json
      de_results.csv
      avn_avring_marker_genes.csv
      avn_avring_marker_genes.json
      de_sensitivity_summary.csv
      volcano_afibro_avn_avring_vs_atria.png
      tissueagent_coding_report.md

    geneagent/
      geneagent_input_gene_set.json
      geneagent_report.md
      geneagent_report.json
      geneagent_verification_report.md
      geneagent_stdout.log
      geneagent_stderr.log

    integration/
      final_hypothesis_report.md
      final_hypothesis_report.json
      final_summary.md
      final_reproducibility_notebook.ipynb

  traces/
    execution_trace.jsonl
    planner_trace.md
    repair_trace.md
    tool_calls.jsonl
```

### 12.1 `run_manifest.json`

Minimum content:

```json
{
  "case_id": "tissueagent_geneagent_external_collaboration",
  "started_at": "",
  "completed_at": "",
  "status": "success",
  "repos": {
    "TissueAgent": {
      "url": "https://github.com/ma-compbio/TissueAgent",
      "commit": "",
      "container_image": "agentcoop-tissueagent:case-study-1"
    },
    "GeneAgent": {
      "url": "https://github.com/ncbi-nlp/GeneAgent",
      "commit": "",
      "container_image": "agentcoop-geneagent:case-study-1"
    }
  },
  "dataset": {
    "name": "developing_human_heart_merfish_farah_2024",
    "file": "data/farah_human_heart_merfish/overall_merfish.h5ad",
    "source": "https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp"
  },
  "main_results": {
    "marker_count": 46,
    "example_marker_overlap": ["DES", "IGFBP5", "NELL2", "HAND2", "MYH7", "MYH6"],
    "geneagent_theme": "Cardiac Development and Remodeling",
    "final_conclusion": "..."
  },
  "repairs": []
}
```

### 12.2 `final_summary.md`

The final summary should be short enough to read quickly:

```markdown
# AgentCo-Op Case Study 1 Summary

AgentCo-Op configured two external repositories, TissueAgent and GeneAgent, as sandboxed execution nodes. TissueAgent loaded the Farah et al. developing human heart MERFISH dataset and contrasted aFibro cells in the AVN/AV ring cellular community against aFibro cells in Left/Right Atria communities. The DE node identified N AVN/AV ring-upregulated marker genes at adjusted P < 0.05 and produced a volcano plot. The marker set was passed through the artifact broker to GeneAgent, which interpreted the gene set as [theme]. The final integrator concluded that the evidence [supports/partially supports] a specialized developmental, ECM-rich, conduction-associated aFibro state in the AVN/AV ring community.

Key artifacts:
- DE results: artifacts/tissueagent_de/de_results.csv
- Marker genes: artifacts/tissueagent_de/avn_avring_marker_genes.csv
- Volcano plot: artifacts/tissueagent_de/volcano_afibro_avn_avring_vs_atria.png
- GeneAgent report: artifacts/geneagent/geneagent_report.md
- Final hypothesis report: artifacts/integration/final_hypothesis_report.md
- Execution trace: traces/execution_trace.jsonl
```

---

## 13. Evaluation metrics

### 13.1 Systems metrics

| Metric | Definition | Target |
|---|---|---|
| Repo wrapping success | Both GitHub repos are profiled, cloned, containerized, and smoke-tested | 2/2 |
| External collaboration success | Both TissueAgent and GeneAgent are invoked as separate registered execution nodes | true |
| Typed handoff success | Marker table is converted into a validated GeneAgent input JSON | true |
| Dynamic repair evidence | If any gate fails, AgentCo-Op performs targeted repair and records it | recorded |
| Artifact completeness | Required output files exist | ≥ 95% |
| Trace completeness | Plan, tool calls, container logs, artifacts, and final report are linked | complete |

### 13.2 Biological reproduction metrics

| Metric | Definition | Target |
|---|---|---|
| Target/control cell definition | Correctly identifies `aFibro` in AVN/AV ring and atria communities | exact or justified alias |
| Marker count | Number of AVN/AV ring markers at adjusted P `< 0.05` and positive log2FC | 46 strict; 40–60 acceptable with caveat |
| Example-marker overlap | Overlap with `DES`, `IGFBP5`, `NELL2`, `HAND2`, `MYH7`, `MYH6` | ≥ 5/6 strict; ≥ 4/6 acceptable |
| GeneAgent theme match | Report includes Cardiac Development / Remodeling or equivalent | yes |
| Subprocess coverage | Morphogenesis, conduction/electrophysiology, sarcomere/structure, ECM/fibrotic, growth-factor signaling | ≥ 4/5 |
| Final interpretation | Supports specialized developmental, ECM-rich, conduction-associated AVN/AV ring aFibro state | yes, with caveats |

### 13.3 Human / expert review rubric

Score 0–2 for each item:

1. **Workflow correctness.** Does the final workflow match the TissueAgent × GeneAgent experiment?
2. **Repository collaboration.** Are TissueAgent and GeneAgent actually used as separate execution backends?
3. **Data handling.** Are the group definitions correct and auditable?
4. **Statistical clarity.** Are DE preprocessing, test, FDR correction, and marker criteria clear?
5. **Gene-set interpretation.** Is the GeneAgent report biologically coherent and grounded?
6. **Final synthesis.** Does the final report accurately connect DE evidence to the developmental AVN/AV ring claim?
7. **Reproducibility.** Could another researcher rerun the experiment from the artifacts?

Maximum score: 14.

---

## 14. Ablation experiments

Run these to show that AgentCo-Op’s contribution is not merely “running code.”

### 14.1 No external GeneAgent

Run the same DE workflow but replace GeneAgent with a generic LLM summarizer.

Expected finding: DE artifacts still exist, but gene-set interpretation should be less grounded and less self-verified.

### 14.2 No TissueAgent sandbox

Run DE with a generic coding agent in a plain Python environment, then pass markers to GeneAgent.

Expected finding: biological result may still be possible, but the run no longer demonstrates repository-to-agent synthesis for the spatial workflow backend.

### 14.3 No dynamic repair

Disable repair gates and run once.

Expected finding: environment / label / API brittleness should reduce success rate, especially in GeneAgent setup and exact marker recovery.

### 14.4 Single monolithic agent

Ask a single general-purpose agent to perform the entire task in one environment.

Expected finding: simpler but less modular, weaker provenance, less evidence of external specialized-agent collaboration.

### 14.5 Prebuilt containers versus autonomous containers

Compare:

- **Autonomous mode:** AgentCo-Op builds Dockerfiles from repo inspection.
- **Oracle mode:** provide prebuilt images.

Expected finding: autonomous mode is the main contribution; oracle mode is a debugging control.

---

## 15. Common pitfalls and required repairs

### 15.1 TissueAgent has no stable CLI for this exact case

The README emphasizes UI and notebook usage. If there is no direct CLI for the collaboration case, AgentCo-Op should generate a headless wrapper script or notebook inside the TissueAgent environment. Record this as a wrapper synthesis step, not a failure.

### 15.2 GeneAgent uses older OpenAI SDK and manual key configuration

GeneAgent’s README lists `openai==0.28.0` and instructs users to replace API settings in source files. AgentCo-Op should not require manual source editing. It should patch at runtime using environment variables or generate a small config shim.

### 15.3 Label strings may not match exactly

`AVN/AV ring` may appear as `AVN/AVring`, `AVN_AVring`, or a similar string. Use label discovery and save the exact mapping.

### 15.4 Differential-expression method may not exactly reproduce 46 markers

The manuscript target is 46 markers, but the exact DE method is not fully specified in the section. Use a method-recovery gate and report sensitivity across methods. Do not silently choose a method only because it gives 46; the report must state the method and why it was selected.

### 15.5 Cell-level DE significance can be inflated

Because cells from the same tissue section are not independent biological replicates, include a pseudobulk sensitivity analysis if possible. The main manuscript reproduction can use cell-level DE, but the final report should include this caveat.

### 15.6 GeneAgent output may be text-only

If GeneAgent returns free-form text, AgentCo-Op should serialize it to Markdown and then use a parser / structured-output repair step to create `geneagent_report.json`. The original Markdown should be preserved.

### 15.7 Network-dependent database queries

GeneAgent may call external biological databases. Cache requests when possible and record database access logs. If network access is blocked, mark the result as partial and run a local fallback only as an explicitly labeled fallback.

---

## 16. Minimal implementation checklist

A successful first implementation should complete these items:

```text
[ ] Create case_study_1.request.yaml.
[ ] Implement RepoProfiler for TissueAgent and GeneAgent.
[ ] Implement Dockerfile synthesis or provide initial Dockerfile templates.
[ ] Build TissueAgent sandbox and run smoke test.
[ ] Build GeneAgent sandbox and run smoke test.
[ ] Download overall_merfish.h5ad.
[ ] Implement TissueAgent wrapper for dataset inspection and DE.
[ ] Implement label-discovery and group-definition reporting.
[ ] Produce DE table, marker table, and volcano plot.
[ ] Implement DE quality gate.
[ ] Implement artifact broker to create GeneAgent input JSON.
[ ] Implement GeneAgent wrapper.
[ ] Produce GeneAgent Markdown and JSON reports.
[ ] Implement GeneAgent quality gate.
[ ] Implement final hypothesis integrator.
[ ] Export final report, notebook, compiled graph, agent registry, and trace.
[ ] Run ablations.
```

---

## 17. Recommended first-run command sequence

```bash
# 1. Create workspace.
mkdir -p runs/case_study_1
cd runs/case_study_1

# 2. Place or generate the request file.
cp ../../case_study_1.request.yaml .

# 3. Export credentials.
export OPENAI_API_KEY="..."

# 4. Run AgentCo-Op.
agentcoop run \
  --request case_study_1.request.yaml \
  --workdir . \
  --sandbox docker \
  --allow-network true \
  --max-replans 2

# 5. Inspect final artifacts.
cat artifacts/integration/final_summary.md
open artifacts/tissueagent_de/volcano_afibro_avn_avring_vs_atria.png
cat artifacts/geneagent/geneagent_report.md
cat artifacts/integration/final_hypothesis_report.md
```

---

## 18. What to report in the paper

In the AgentCo-Op paper or project report, present this case study as:

> **Case Study 1: Repository-level collaboration between spatial and gene-set agents.** Given only the TissueAgent and GeneAgent GitHub repositories, the developing human heart MERFISH dataset, and a task description, AgentCo-Op compiled an upstream/downstream workflow, constructed isolated Docker sandboxes, registered both repositories as specialized execution nodes, ran TissueAgent for spatial differential expression, handed the marker set to GeneAgent for self-verified gene-set interpretation, and synthesized the results into an auditable hypothesis report.

Include one workflow figure:

```text
Input repos + dataset + task
        ↓
AgentCo-Op compiler
        ↓
TissueAgent sandbox: dataset inspection + DE + volcano
        ↓ marker-gene artifact
GeneAgent sandbox: gene-set analysis + self-verification
        ↓
AgentCo-Op/TissueAgent integrator: hypothesis report
```

Include one results table:

| Output | Result |
|---|---|
| Repos configured | TissueAgent + GeneAgent |
| Docker sandboxes built | yes / no |
| Target group | aFibro in AVN/AV ring CC |
| Control group | aFibro in Left Atria + Right Atria CCs |
| Marker count | N, target 46 |
| Example marker overlap | list |
| GeneAgent theme | e.g., Cardiac Development and Remodeling |
| Final conclusion | supported / partially supported |
| Repairs used | none or list |

Include one biological figure:

- volcano plot of `aFibro: AVN/AV ring vs Atria`.

Include one systems figure:

- compiled workflow graph with TissueAgent and GeneAgent as sandboxed execution nodes and gates.

---

## 19. Source notes

This design is based on the following source facts:

1. The TissueAgent manuscript describes a role-based multi-agent framework with Planner, Recruiter, Manager, Evaluator, and Reporter roles, and a single evolving plan that records rationales, step status, and artifact links.
2. The TissueAgent manuscript’s external-agent collaboration case integrates GeneAgent as a gene-set analysis specialist. The described workflow contrasts `aFibro` cells in the AVN/AV ring cellular community against `aFibro` cells in Left Atria and Right Atria communities, identifies 46 markers at adjusted P `< 0.05`, forwards the marker set to GeneAgent, and synthesizes the result into a hypothesis report.
3. The TissueAgent manuscript’s Figure 3 shows the intended workflow: differential gene expression analysis → gene-set analysis → hypothesis analysis, with TissueAgent routing DE to a Coding Agent, gene-set analysis to GeneAgent, and final synthesis to a Hypothesis Agent.
4. The Dryad dataset page for Farah et al. lists `overall_merfish.h5ad` and describes it as an AnnData object for the three 13 PCW MERFISH replicate experiments, with relevant observation metadata including `communities` and `populations`.
5. The TissueAgent GitHub README describes a role-based framework, built-in external specialized-agent collaboration, setup options through conda / uv / Nix, headless notebook usage, and public data availability including the developing human heart MERFISH dataset.
6. The GeneAgent GitHub README describes GeneAgent as a gene-set analysis agent using domain databases and self-verification, with Python 3.11 and older OpenAI SDK requirements. This motivates a separate sandbox and a wrapper that replaces manual key editing with environment-variable configuration.

---

## 20. References

- TissueAgent repository: `https://github.com/ma-compbio/TissueAgent`
- GeneAgent repository: `https://github.com/ncbi-nlp/GeneAgent`
- GeneAgent NCBI project page: `https://www.ncbi.nlm.nih.gov/CBBresearch/Lu/Demo/GeneAgent/`
- Farah et al., “Spatially organized cellular communities form the developing human heart,” Nature 2024: `https://www.nature.com/articles/s41586-024-07171-z`
- Farah et al. Dryad dataset: `https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp`
- UCSC Cell Browser entry: `https://cells.ucsc.edu/?ds=hoc`
- TissueAgent manuscript uploaded in this project: `TissueAgent_manuscript.pdf`
