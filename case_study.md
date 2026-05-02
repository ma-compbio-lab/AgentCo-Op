# AgentCo-Op Case Studies

This document reformulates the specialized experiments into three case studies:

1. **Upstream/downstream collaboration**: bulk RNA-seq differential expression produces gene markers, then a gene-set agent interprets and refines them.
2. **Parallel collaboration**: single-cell foundation models and perturbation models run in parallel, then a high-level analysis agent ensembles and benchmarks their outputs.
3. **Dynamic topology refinement**: start from an AFlow-discovered or AFlow-like topology, add task skills and tools, and trigger local topology repair at runtime.

The case studies are designed to demonstrate capabilities that are hard to see in standard QA, math, and code benchmarks: typed artifact exchange, sandboxed bioinformatics tools, GPU-model orchestration, specialist disagreement handling, and local graph mutation.

## 1. Literature basis and design principles

### 1.1 Why upstream/downstream biological workflows are useful

Bulk RNA-seq differential expression is a well-established upstream analysis. DESeq2 models count data with negative-binomial generalized linear models and shrinkage estimates, while limma-voom and edgeR are also common alternatives. Downstream gene-set interpretation is a natural next stage because lists of differentially expressed genes are usually interpreted through pathways, Gene Ontology terms, MSigDB signatures, and literature evidence.

GeneAgent is especially relevant because it treats gene-set analysis as an LLM-agent problem and uses biological databases for self-verification. This makes it a good downstream execution node for AgentCo-Op: the upstream node produces typed gene sets, and the downstream node verifies functional descriptions against domain resources.

### 1.2 Why single-cell foundation-model case studies need strong baselines

scGPT, scFoundation, Geneformer, and GEARS show that pretrained or knowledge-informed models can support single-cell annotation, integration, perturbation prediction, or network biology tasks. However, recent perturbation-response benchmark studies show that simple baselines can be competitive or better than deep models on some datasets, especially when systematic variation dominates. Therefore, Case Study 2 must include simple baselines and avoid claiming that foundation models are automatically superior.

### 1.3 Why dynamic topology should be tested on code/math first

Dynamic refinement needs objective runtime signals. Code benchmarks provide syntax errors, runtime errors, unit-test failures, and timeouts. MATH provides answer-format failures, symbolic-check failures, and specialist disagreement. These signals are much more reliable than unconstrained reviewer confidence, so they are ideal for testing AgentCo-Op's gated-subgraph design.

## 2. Case Study 1: External-repo collaboration — TissueAgent × GeneAgent

> **Spec.** Detailed protocol lives in **`case_study_1.md`** (the
> Session-7 update). This section is the high-level overview kept inside
> `case_study.md` so the three case studies stay in one document. The
> earlier "bulk RNA-seq + airway" framing remains in §2.2–§2.5 below as
> the *baseline / fallback* design — useful when the user only wants a
> single-repo bulk RNA-seq + GeneAgent flow rather than the full
> two-repo external-collaboration framework.

### 2.1 Goal (Session-7 update)

Demonstrate that AgentCo-Op can take **two GitHub repository URLs and a
task description** and autonomously:

1. clone each repo and emit a typed `RepoProfile`;
2. generate Dockerfiles + docker-compose + smoke tests via
   `SandboxBuilder`;
3. register an `AgentCard` per repo (capabilities, container, I/O
   schemas) in an `AgentRegistry`;
4. run the upstream agent inside its sandbox, broker the typed
   handoff via `ArtifactBroker` (gene-set / CSV validators), and run
   the downstream agent;
5. integrate both outputs with an LLM-backed integrator into an
   auditable hypothesis report.

Inaugural fixture: TissueAgent × GeneAgent on the developing human
heart MERFISH dataset (Farah et al. 2024). The framework itself is
**generic across repo pairs** — see `runs/case1/heart_merfish/README.md`
for the recipe to reuse it on any other agent pair.

The earlier vertical pattern still holds at the artifact-flow level:

```text
Repo A sandbox (upstream specialist)
  -> typed artifact handoff via ArtifactBroker
  -> Repo B sandbox (downstream specialist)
  -> LLM integrator
  -> evidence-grounded biological interpretation
```

This case study tests whether AgentCo-Op can pass structured
biological artifacts between *external sandboxed* agents and tools,
isolate memories by stage, and integrate statistical outputs with
literature/database-grounded reasoning.

### 2.1.b Baseline / fallback flow (legacy bulk RNA-seq + airway)

### 2.2 Recommended primary dataset

Use the Bioconductor `airway` dataset as the primary reproducible dataset.

Why this dataset:

- It is small, public, and widely used in DESeq2 tutorials.
- It contains RNA-seq counts from four human airway smooth muscle cell lines treated with dexamethasone and untreated controls.
- The design has a clear paired structure: treated vs untreated within cell line.
- The biological context is interpretable: dexamethasone is a synthetic glucocorticoid used in asthma-related airway inflammation contexts.

Optional secondary datasets:

| Dataset/source | Purpose |
|---|---|
| recount3 selected bulk RNA-seq studies | Stress-test dataset selection and metadata parsing. |
| GenoTEX tasks | Evaluate automated gene-expression analysis on curated gene-trait association problems. |
| TCGA/GTEx tumor-vs-normal subsets | Larger disease-marker case study, if compute and preprocessing time permit. |

Start with `airway`; add larger datasets only after the pipeline is stable.

### 2.3 Workflow topology

```text
TaskProfiler
  -> DatasetLoader_R
  -> SampleMetadataInspector
  -> DesignMatrixPlanner
  -> DifferentialExpression_DESeq2
  -> DEMethodCrossCheck_optional
  -> MarkerSelector
  -> GeneSymbolNormalizer
  -> ORA_GSEA_Tool
  -> GeneAgentSandbox
  -> EvidenceVerifier
  -> IntegratorReviewer
  -> FinalBiologyReport
```

Recommended execution-node types:

| Node | Backend | Output artifact |
|---|---|---|
| DatasetLoader_R | R/Bioconductor sandbox | `counts.tsv`, `metadata.tsv` |
| DesignMatrixPlanner | LLM + schema validator | `design.json` |
| DifferentialExpression_DESeq2 | R sandbox | `de_results.tsv` |
| DEMethodCrossCheck_optional | R sandbox: edgeR or limma-voom | `de_crosscheck.tsv` |
| MarkerSelector | Python/R deterministic tool | `up_genes.json`, `down_genes.json` |
| ORA_GSEA_Tool | R/Python enrichment tool | `enrichment_results.tsv` |
| GeneAgentSandbox | GitHub repo or local wrapper | `geneagent_report.json` |
| EvidenceVerifier | LLM + database evidence | `verified_claims.json` |
| IntegratorReviewer | LLM | final report |

### 2.4 Required framework adaptations

AgentCo-Op should add the following adapters before running this case study:

1. **R sandbox adapter**
   - Supports `renv` or fixed Bioconductor Docker images.
   - Runs R scripts with network policy explicitly declared.
   - Captures stdout, stderr, package versions, and output files.

2. **Gene-set artifact schema**

```json
{
  "organism": "Homo sapiens",
  "gene_id_type": "HGNC_SYMBOL",
  "contrast": "dexamethasone_vs_control",
  "direction": "up",
  "selection_rule": {
    "padj_max": 0.05,
    "abs_log2fc_min": 1.0,
    "rank_by": "padj_then_abs_log2fc"
  },
  "genes": [
    {"symbol": "...", "log2fc": 1.23, "padj": 0.001}
  ]
}
```

3. **Gene symbol normalization**
   - Convert Ensembl IDs to HGNC symbols.
   - Record unmapped genes.
   - Preserve original IDs for reproducibility.

4. **Memory isolation**
   - The DE node sees counts and metadata.
   - GeneAgent sees only selected gene sets plus biological context, not raw count matrices.
   - The integrator sees all artifacts and decides whether the downstream interpretation is supported.

5. **Network policy**
   - DESeq2 and local enrichment can run offline after package installation.
   - GeneAgent database calls may require network access; this should be a separate sandbox profile.

### 2.5 Data processing steps

Create an R script `scripts/case1_airway_de.R`:

```r
library(airway)
library(DESeq2)
library(apeglm)
library(org.Hs.eg.db)
library(AnnotationDbi)

data(airway)
se <- airway
se$cell <- factor(se$cell)
se$dex <- relevel(factor(se$dex), ref = "untrt")

dds <- DESeqDataSet(se, design = ~ cell + dex)
dds <- dds[rowSums(counts(dds)) >= 10,]
dds <- DESeq(dds)

res <- results(dds, contrast = c("dex", "trt", "untrt"))
res <- lfcShrink(dds, coef = "dex_trt_vs_untrt", type = "apeglm")
res_df <- as.data.frame(res)
res_df$ensembl_id <- rownames(res_df)
res_df$symbol <- mapIds(org.Hs.eg.db,
                        keys = res_df$ensembl_id,
                        column = "SYMBOL",
                        keytype = "ENSEMBL",
                        multiVals = "first")
write.table(res_df, "artifacts/de_results.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
```

Then select markers:

```bash
agentcoop bio select-markers \
  --de-results artifacts/de_results.tsv \
  --padj-max 0.05 \
  --abs-log2fc-min 1.0 \
  --top-k 100 \
  --out artifacts/gene_sets/
```

Run enrichment:

```bash
agentcoop bio enrich \
  --gene-set artifacts/gene_sets/up_genes.json \
  --background artifacts/all_expressed_genes.json \
  --databases GO_Biological_Process MSigDB_Hallmark Reactome \
  --out artifacts/enrichment/up_enrichment.tsv
```

Run GeneAgent:

```bash
agentcoop run-node geneagent \
  --input artifacts/gene_sets/up_genes.json \
  --context "Human airway smooth muscle cells treated with dexamethasone vs untreated control." \
  --out artifacts/geneagent/up_report.json
```

Repeat for down-regulated genes.

### 2.6 Baselines

| Method | Description |
|---|---|
| DE-only | Report top up/down genes and no functional synthesis. |
| DE + static ORA/GSEA | Standard enrichment only. |
| DE + generic LLM | Give the gene list to a generic LLM with no database verification. |
| DE + GeneAgent | Downstream GeneAgent only. |
| AC-BulkGeneSet | Full AgentCo-Op workflow with DE, enrichment, GeneAgent, verifier, and integrator. |
| AC-BulkGeneSet-NoVerifier | Remove evidence verifier. |
| AC-BulkGeneSet-NoCrossCheck | Remove edgeR/limma cross-check. |

### 2.7 Metrics

#### Pipeline metrics

- DE node success rate.
- Gene mapping success rate.
- Number of selected up/down genes.
- Enrichment result validity.
- GeneAgent runtime success.
- Typed artifact schema validity.
- Manual intervention count.

#### Biological interpretation metrics

- Agreement between GeneAgent labels and static enrichment labels.
- Evidence coverage: percentage of final claims supported by database/literature evidence.
- Hallucination rate: percentage of claims rejected by the verifier or expert review.
- Directionality correctness: whether up/down interpretations respect the sign of differential expression.
- Leave-one-cell-line-out treatment classification using the selected gene set.

#### Collaboration metrics

- Whether the downstream agent uses upstream artifacts correctly.
- Whether the integrator catches invalid gene symbols or unsupported biological claims.
- Gate rescue rate for invalid gene mappings, empty gene sets, failed enrichment, or unsupported GeneAgent claims.

### 2.8 Gates

| Gate | Trigger | Repair action |
|---|---|---|
| `design_ambiguous` | metadata does not clearly define treatment/control | ask DesignMatrixPlanner to propose and validate design |
| `too_few_markers` | fewer than minimum genes pass thresholds | relax threshold or switch to ranked GSEA |
| `gene_mapping_low` | high fraction of unmapped IDs | switch identifier mapping strategy |
| `enrichment_empty` | no significant ORA result | run ranked GSEA and report uncertainty |
| `geneagent_unsupported_claim` | verifier rejects claims | ask GeneAgent to revise using only verified evidence |
| `method_disagreement` | DESeq2 and cross-check method disagree strongly | report robust intersection and method-specific differences |

### 2.9 Expected output

The final report should include:

1. Dataset and design summary.
2. QC summary.
3. DE volcano/table summary.
4. Up/down marker sets.
5. Static enrichment results.
6. GeneAgent functional interpretations.
7. Evidence verification table.
8. Final biological interpretation.
9. Uncertainty and failure notes.
10. Reproducibility block with package versions, seeds, commits, and artifact hashes.

## 3. Case Study 2: Cross-modal external-tool collaboration — Seurat × Signac

> **Spec.** Detailed protocol lives in **`case_study_2.md`** (the
> Session-7.3 update). This section is the high-level overview kept
> inside `case_study.md` so the three case studies stay in one
> document. The earlier "parallel single-cell foundation-model
> collaboration" framing remains in §3.1.b–§3.6 below as the
> *baseline / fallback* design — useful when the user only wants a
> GPU-foundation-model parallel-prediction flow.

### 3.1 Goal (Session-7.3 update)

Demonstrate that AgentCo-Op can take **two external bioinformatics
tool GitHub URLs** (Seurat for scRNA, Signac for scATAC) plus a
multiome dataset + cell-type labels + a reference marker database,
and autonomously:

1. profile each tool repo and emit per-tool `RepoProfile` JSON;
2. render Dockerfile + docker-compose + smoke tests for each tool
   (per `case_study_2.md` §8);
3. **auto-install every declared PyPI package** before invoking the
   adapters (no manual `pip install` from the user);
4. run **two parallel branches** — Seurat for RNA marker discovery,
   Signac for ATAC peak markers + peak-to-mm10-gene mapping;
5. broker the two typed top-N marker JSONs;
6. invoke a `join_agent` (CellMarker 2.0 evaluator) that builds gold
   marker sets, computes per-cell-type intersection / union, and
   reports precision / recall / collaboration-gain;
7. wrap everything with an LLM (`gpt-5`) integrator into one
   evidence-linked Markdown report.

```text
SHARE-seq mm10 multiome + labels + CellMarker 2.0
  ↓                                ↓
Seurat sandbox (scRNA marker)    Signac sandbox (scATAC marker → mm10 nearest gene)
  ↓                                ↓
        ArtifactBroker (gene-set / CSV validators)
                       ↓
          CellMarkerEvaluator (join_agent)
                       ↓
              gpt-5 integrator
                       ↓
        final_report + collaboration_log + topology PNG/DOT
```

The central CS2 hypothesis is that **set intersection of RNA + ATAC
marker genes improves precision** relative to either modality alone,
even when single-modality recall already dominates. The
inaugural run on real Ma 2020 SHARE-seq mouse skin data confirms this
at the macro level (mean `precision_intersection = 0.083` vs
`precision_rna = 0.051` vs `precision_atac = 0.003`) — see
`runs/case2/shareseq_skin/README.md`.

### 3.1.b Baseline / fallback flow (legacy parallel foundation-model collaboration)

Demonstrate a parallel specialist collaboration pattern:

```text
Perturb-seq / scRNA-seq dataset
  -> dataset profiler
  -> parallel model runners: GEARS, scGPT, scFoundation, Geneformer-style embeddings, simple baselines
  -> metric evaluator
  -> ensemble selector
  -> high-level biological and benchmark analysis
```

The main claim is not that every foundation model wins. The claim is that AgentCo-Op can orchestrate heterogeneous model repos, collect comparable outputs, evaluate them fairly, and use validation evidence to select or ensemble specialists.

### 3.2 Recommended datasets

| Tier | Dataset | Why use it |
|---|---|---|
| Debug | GEARS built-in `norman` loader | Fast smoke test for GEARS-compatible pipelines. |
| Main 1 | Norman et al. 2019 Perturb-seq | Contains single and combinatorial perturbations; useful for 0-seen/1-seen/2-seen split analysis. |
| Main 2 | Replogle et al. 2022 K562 Essential | Large CRISPRi perturbation dataset; important for single-gene unseen perturbation evaluation. |
| Main 3 | Adamson et al. 2016, preferably corrected version | Common benchmark, but label issues require care. |
| Extended | scPerturb or scPerturBench datasets | Broader benchmark after the first pipeline is stable. |

Start with Norman and Replogle K562 Essential. Add Adamson only with a clear note about corrected labels.

### 3.3 Models and baselines

| Model/backend | Role | Notes |
|---|---|---|
| Perturbed mean baseline | simple baseline | Must be included because simple baselines are competitive on many perturbation datasets. |
| Matching/additive mean baseline | simple combinatorial baseline | Important for Norman two-gene perturbations. |
| CRISPR-informed mean baseline | biologically informed simple baseline | Useful for CRISPRi/CRISPRa tasks. |
| Linear model / ridge regression | simple learned baseline | Tests whether deep models are necessary. |
| GEARS | specialized perturbation model | Official repo supports Norman, Adamson, Dixit, and newer Replogle loaders. |
| scGPT | single-cell foundation model | Use official perturbation tutorial or pinned implementation. |
| scFoundation | foundation-model embeddings/downstream perturbation code | Use official GEARS-related downstream folder or embedding-based adapter. |
| Geneformer | embedding/target-discovery specialist | Use as an optional embedding model or fine-tuned classifier; do not force it into direct perturbation prediction if the adapter is not mature. |
| Optional scPerturBench methods | broader benchmark | biolord, CPA, scGen, scFoundation, scGPT, and other methods can be added later. |

### 3.4 Required framework adaptations

1. **GPU sandbox executor**
   - Supports CUDA image selection, GPU memory limits, and timeout.
   - Captures environment, package versions, checkpoints, stdout/stderr, and GPU hours.

2. **Model adapter manifest**

```yaml
name: GEARS
kind: perturbation_model
repo: https://github.com/snap-stanford/GEARS
commit: <sha>
image: agentcoop/gears:<sha>
entrypoint: python run_gears.py
input_schema: perturbation_dataset_v1
output_schema: perturbation_prediction_v1
resources:
  gpu: true
  min_vram_gb: 16
  max_wall_time_hours: 12
```

3. **Unified AnnData schema**

```json
{
  "adata_path": "data/norman.h5ad",
  "control_label": "ctrl",
  "perturbation_key": "condition",
  "cell_type_key": null,
  "batch_key": null,
  "gene_id_type": "symbol",
  "split_key": "agentcoop_split"
}
```

4. **Unified prediction schema**

```json
{
  "model": "GEARS",
  "dataset": "norman",
  "split": "test",
  "perturbation": "FOSB+CEBPB",
  "prediction_type": "mean_expression_delta",
  "genes": ["FOSB", "CEBPB", "..."],
  "predicted_delta": [0.1, -0.2, 0.0],
  "uncertainty": null,
  "metadata": {"seed": 1, "checkpoint": "..."}
}
```

5. **Result compatibility layer**
   - Models may predict full expression, expression delta, embeddings, or ranked genes.
   - The evaluator must convert outputs to comparable forms and document any conversion.

### 3.5 Split design

Use perturbation-level splits, not cell-level random splits, to test generalization to unseen perturbations.

For Norman two-gene perturbations, report:

- **2-seen**: both single perturbations were seen during training, but the combination was unseen.
- **1-seen**: one gene in the pair was seen as a single perturbation.
- **0-seen**: neither gene in the pair was seen as a single perturbation.

For Replogle K562 Essential and Adamson, report unseen single-gene perturbation performance.

Recommended seeds: `1, 2, 3`. If compute allows, use `1..10`.

### 3.6 Metrics

Primary perturbation metrics:

- Pearson Delta over all genes.
- Pearson Delta over the top 20 differentially expressed genes.
- RMSE or MSE over expression deltas.
- Cosine similarity of predicted vs observed perturbation effect.
- Precision@K or recall@K for recovering top differentially expressed genes.
- Rank metric for perturbation triage when available.

Operational metrics:

- train time;
- inference time;
- GPU hours;
- memory peak;
- failed run count;
- adapter setup time;
- cost per completed dataset/model pair.

Collaboration metrics:

- ensemble selection accuracy on validation vs test;
- specialist disagreement rate;
- cases where the high-level agent correctly prefers a simple baseline;
- cases where a deep model beats simple baselines in specific split types;
- report quality score for the final analysis.

### 3.7 Ensemble and high-level analysis

AgentCo-Op should implement three ensemble strategies:

1. **Validation winner**
   - Choose the single best model for each dataset and split type based on validation metric.
   - This is the simplest and most interpretable ensemble.

2. **Metric-aware rank aggregation**
   - Convert each model output to a ranked list of genes by absolute predicted delta.
   - Aggregate ranks using Borda count or reciprocal-rank fusion.
   - Useful when models have incompatible expression-scale outputs.

3. **Weighted prediction average**
   - Only use when predictions are on the same gene universe and scale.
   - Weights are proportional to validation performance.
   - Report calibration and scale-normalization details.

High-level analysis agent prompt contract:

```text
You are the benchmark integrator. You receive model metrics, run logs, and selected plots.
Do not claim a model is better unless the metric table supports it.
Always compare against simple baselines.
Identify dataset-specific failure modes, systematic variation risks, and split-specific behavior.
Return a structured report with: summary, per-dataset findings, per-model findings,
ensemble decision, failure modes, and recommended next experiments.
```

### 3.8 Workflow topology

```text
DatasetProfiler
  -> SplitPlanner
  -> ParallelModelRunner[
       MeanBaseline,
       CRISPRInformedBaseline,
       GEARS,
       scGPT,
       scFoundation,
       Geneformer_optional
     ]
  -> PredictionNormalizer
  -> MetricEvaluator
  -> EnsembleSelector
  -> BiologicalPatternAnalyzer
  -> BenchmarkReportReviewer
  -> FinalReport
```

Gates:

| Gate | Trigger | Repair action |
|---|---|---|
| `model_env_fail` | import or CUDA failure | rebuild image or switch CPU/debug mode if possible |
| `gene_universe_mismatch` | model output genes differ from evaluator genes | intersect genes and report coverage |
| `prediction_schema_invalid` | missing perturbation or gene fields | run adapter-level formatter |
| `metric_outlier` | suspiciously high/low metric | inspect leakage, split, and normalization |
| `simple_baseline_beats_all` | simple baseline wins validation | ensemble selector should prefer simple baseline and explain why |
| `model_disagreement_high` | large prediction disagreement | produce split-specific analysis instead of one global conclusion |

### 3.9 Step-by-step protocol

1. Pin repos and build images.

```bash
agentcoop repo add GEARS https://github.com/snap-stanford/GEARS --commit <sha>
agentcoop repo add scGPT https://github.com/bowang-lab/scGPT --commit <sha>
agentcoop repo add scFoundation https://github.com/biomap-research/scFoundation --commit <sha>
agentcoop repo build --all --profile gpu
```

2. Download datasets.

```bash
agentcoop data perturb download --dataset norman --source gears --out data/perturb/norman
agentcoop data perturb download --dataset replogle_k562_essential --source gears_or_figshare --out data/perturb/replogle_k562
```

3. Create perturbation-level splits.

```bash
agentcoop data perturb split \
  --adata data/perturb/norman/norman.h5ad \
  --mode simulation \
  --seeds 1 2 3 \
  --out data/perturb/norman/splits
```

4. Run model adapters in parallel.

```bash
agentcoop run-case2 \
  --dataset data/perturb/norman/norman.h5ad \
  --splits data/perturb/norman/splits \
  --models mean crispr_mean gears scgpt scfoundation \
  --out runs/case2/norman
```

5. Evaluate.

```bash
agentcoop evaluate-perturb \
  --predictions runs/case2/norman/predictions \
  --adata data/perturb/norman/norman.h5ad \
  --metrics pearson_delta pearson_delta_top20 rmse cosine precision_at_k \
  --out runs/case2/norman/metrics.json
```

6. Ensemble and analyze.

```bash
agentcoop ensemble-perturb \
  --metrics runs/case2/norman/metrics.json \
  --strategy validation_winner rank_fusion weighted_average \
  --out runs/case2/norman/ensemble

agentcoop report-case2 \
  --metrics runs/case2/norman/metrics.json \
  --ensemble runs/case2/norman/ensemble \
  --out runs/case2/norman/final_report.md
```

### 3.10 Required figures

- Per-dataset model performance bar chart.
- Performance by split type: 0-seen, 1-seen, 2-seen.
- Simple baselines vs foundation/deep models.
- Cost/GPU-hour vs performance scatter plot.
- Specialist disagreement heatmap.
- Ensemble selection diagram.
- Failure taxonomy table.

### 3.11 Risks and controls

| Risk | Control |
|---|---|
| Cell-level leakage | Use perturbation-level splits. |
| Dataset label errors | Prefer corrected Adamson; report data provenance. |
| Systematic variation inflates metrics | Include perturbed mean and matching mean baselines. |
| Different gene universes | Report gene coverage and use intersection metrics. |
| GPU instability | Use per-model images and smoke tests. |
| Foundation model overclaiming | Always report simple baselines and split-specific behavior. |

## 4. Case Study 3: Dynamic topology refinement from AFlow-style workflows

### 4.1 Goal

Show that AgentCo-Op can start from an existing AFlow-discovered or AFlow-like topology and improve its practical behavior by adding skills, tools, and gated local repair.

The intended contribution is:

```text
AFlow topology
  -> AgentCo-Op graph importer
  -> skill/tool augmentation
  -> runtime gate triggers
  -> local topology repair
  -> refined execution
```

This case study directly tests the claim that AgentCo-Op is not just a static compiler; it can dynamically refine topology during execution.

### 4.2 Recommended benchmark targets

Use code and math first because they provide objective runtime signals.

| Target | Why use it | Main metric |
|---|---|---|
| HumanEval | syntax/runtime/generated-test signals; small and cheap | pass@1 |
| MBPP | public tests/examples enable repair signals | pass@1 |
| MATH level 5 | domain routing and verifier disagreement are meaningful | solve rate |
| Optional SWE-bench Lite or Verified Mini | real repo repair and sandbox value | resolved rate |

Do not make SWE-bench the first dynamic case unless the sandbox and budget are already mature. SWE-bench is valuable but expensive and operationally brittle.

### 4.3 Starting topologies

Use two starting points:

1. **AFlow official or reproduced topology**
   - Import workflow code from the AFlow repo or reconstruct the topology from the paper appendix.
   - Keep the original AFlow topology unchanged for the `AFlow-original` baseline.

2. **AFlow-like topology**
   - If exact AFlow workflow code is unavailable for a dataset, create a faithful AFlow-like workflow using the operators described in AFlow: Generate, Format, Review, Revise, Ensemble, Test, and Programmer.
   - Label this clearly as reconstructed, not official.

### 4.4 Framework adaptations

1. **AFlow graph importer**

```bash
agentcoop import-aflow-workflow \
  --workflow-file external/AFlow/workspace/<dataset>/<workflow>.py \
  --dataset HumanEval \
  --out configs/imported_graphs/humaneval_aflow.json
```

The importer should convert AFlow operators into AgentCo-Op node manifests:

```json
{
  "node_id": "programmer_1",
  "source": "aflow",
  "operator": "Programmer",
  "input_keys": ["problem"],
  "output_keys": ["code"],
  "skills": [],
  "tools": [],
  "gates": []
}
```

2. **Skill/tool augmentation API**

```bash
agentcoop augment-graph \
  --graph configs/imported_graphs/humaneval_aflow.json \
  --skills configs/skills/code_debugging.yaml configs/skills/python_testing.yaml \
  --tools sandbox_python static_analyzer generated_tests \
  --out configs/imported_graphs/humaneval_aflow_augmented.json
```

3. **Local topology mutation API**

At runtime, the graph may add or replace a bounded subgraph, for example:

```json
{
  "trigger": "generated_test_failure",
  "mutation": "insert_subgraph_after",
  "after_node": "programmer_1",
  "subgraph": ["failure_analyzer", "repair_programmer", "sandbox_retest"]
}
```

4. **Gate budget controller**

Every gate has a maximum number of activations, maximum added cost, and maximum wall time. This prevents dynamic repair from becoming unbounded search.

### 4.5 Code benchmark dynamic workflow

Base graph:

```text
AFlowImportedProgrammer
  -> AFlowImportedFormatter
  -> FinalCode
```

AgentCo-Op augmented graph:

```text
AFlowImportedProgrammer
  -> CodeSkillRouter
  -> SandboxSyntaxCheck
  -> GeneratedTestRunner
  -> FailureAnalyzer [gate]
  -> RepairProgrammer [gated]
  -> SandboxRetest [gated]
  -> FinalCodeFormatter
```

Allowed runtime signals:

- syntax error;
- import error;
- runtime exception on public/generated tests;
- timeout;
- output format invalid;
- generated test disagreement.

Disallowed signal:

- official hidden test feedback during generation or repair.

Recommended generated-test policy:

1. Ask the model to generate 3 to 8 lightweight tests from the prompt.
2. Run tests only in the sandbox.
3. Treat test failures as heuristic debugging evidence, not as official score.
4. Cap repair rounds at 2 or 3.

### 4.6 MATH dynamic workflow

Base graph:

```text
AFlowImportedMathSolver
  -> AFlowImportedEnsembleOrFormatter
  -> FinalAnswer
```

AgentCo-Op augmented graph:

```text
AFlowImportedMathSolver
  -> MathDomainSkillRouter
  -> IndependentVerifier
  -> SymbolicOrNumericChecker [when applicable]
  -> DisagreementGate
  -> TargetedRepairSolver [gated]
  -> FinalAnswerExtractor
```

Runtime signals:

- missing boxed answer;
- answer-extraction failure;
- arithmetic inconsistency;
- symbolic equivalence failure;
- disagreement between independent solver and verifier;
- domain mismatch.

Skill cards to attach:

| Skill | Use |
|---|---|
| combinatorics_counting | counting, probability, cases, inclusion-exclusion |
| number_theory | modular arithmetic, divisibility, primes, Diophantine equations |
| algebraic_simplification | equations, inequalities, transformations |
| pre_calculus | functions, trigonometry, limits, sequences |
| answer_normalization | boxed answer extraction and equivalence checks |

### 4.7 Optional SWE-bench dynamic workflow

Use SWE-bench Lite, SWE-bench Verified, or Verified Mini only after the code-generation pipeline is stable.

Workflow:

```text
IssueProfiler
  -> RepoContextRetriever
  -> FileLocalizer
  -> PatchGenerator
  -> SandboxTestRunner
  -> FailureAnalyzer [gate]
  -> PatchRepair [gated]
  -> FinalPatchFormatter
```

Metrics:

- resolved rate;
- patch applies rate;
- test pass rate;
- localization accuracy;
- sandbox setup success;
- average wall time;
- gate rescue/harm rate.

Controls:

- Use official SWE-bench harness.
- Do not inspect gold patches.
- Pin Docker images and dataset version.
- Start with a 50-task mini subset before running 300 or 500 tasks.

### 4.8 Methods to compare

| Method | Description |
|---|---|
| AFlow-original | Original AFlow workflow and runtime. |
| AFlow-imported | AFlow topology executed in AgentCo-Op without skill/tool augmentation. |
| AC-AFlow+Skills | Imported topology with skill cards and tools, no dynamic gates. |
| AC-AFlow+Skills+Gates | Main dynamic method. |
| AC-Compiled | AgentCo-Op compiled from scratch without importing AFlow. |
| AC-ForcedRepair | Always run repair regardless of gate signal. |
| AC-NoTools | Dynamic graph without sandbox/symbolic tools. |

### 4.9 Metrics

Task metrics:

- HumanEval pass@1.
- MBPP pass@1.
- MATH solve rate.
- Optional SWE-bench resolved rate.

Dynamic metrics:

- gate trigger rate;
- gate rescue rate;
- gate harm rate;
- average number of local graph mutations;
- average added LLM calls;
- average added sandbox calls;
- average added cost;
- average added latency;
- fraction of tasks where dynamic repair was skipped.

Topology metrics:

- imported graph size;
- augmented graph size;
- final executed graph size;
- number of inserted subgraphs;
- number of replaced nodes;
- percentage of tasks ending in direct/simple path.

### 4.10 Step-by-step protocol

1. Reproduce or import AFlow baseline.

```bash
agentcoop import-aflow-workflow \
  --workflow-file external/AFlow/workspace/HumanEval/best_workflow.py \
  --dataset HumanEval \
  --out configs/imported_graphs/humaneval_aflow.json
```

2. Run imported baseline.

```bash
agentcoop run-benchmark \
  --dataset data/aflow_aligned/humaneval_test.jsonl \
  --graph configs/imported_graphs/humaneval_aflow.json \
  --method aflow-imported \
  --out runs/case3/humaneval/aflow_imported
```

3. Attach skills and tools.

```bash
agentcoop augment-graph \
  --graph configs/imported_graphs/humaneval_aflow.json \
  --skills configs/skills/code_debugging.yaml configs/skills/python_testing.yaml \
  --tools sandbox_python generated_tests static_analyzer \
  --out configs/imported_graphs/humaneval_aflow_skills.json
```

4. Enable gates.

```bash
agentcoop run-benchmark \
  --dataset data/aflow_aligned/humaneval_test.jsonl \
  --graph configs/imported_graphs/humaneval_aflow_skills.json \
  --gates configs/gates/code_runtime_gates.yaml \
  --method ac-aflow-skills-gates \
  --out runs/case3/humaneval/ac_aflow_skills_gates
```

5. Evaluate and compare.

```bash
agentcoop evaluate \
  --predictions runs/case3/humaneval/ac_aflow_skills_gates/predictions.jsonl \
  --dataset humaneval \
  --out runs/case3/humaneval/ac_aflow_skills_gates/metrics.json

agentcoop analyze-gates \
  --runs runs/case3/humaneval \
  --out runs/case3/humaneval/gate_analysis.md
```

Repeat for MBPP and MATH.

### 4.11 Required figures

- AFlow topology vs AgentCo-Op augmented topology.
- Gate trigger/rescue/harm bar chart.
- Cost-performance Pareto before and after gates.
- Dynamic route Sankey diagram: base path, repair path, formatter-only path.
- Error category table before and after dynamic repair.

### 4.12 Expected outcome and interpretation

Expected improvements are most plausible on tasks where tool feedback is informative:

- HumanEval/MBPP: syntax, generated tests, public examples, and sandbox failures should help targeted repair.
- MATH: symbolic checks and specialist disagreement should help some problems, but may also add cost and false negatives.
- SWE-bench: dynamic repair can help, but environment failures and long wall time may dominate.

The paper should report both rescues and harms. A strong result is not only higher accuracy; it is showing that AgentCo-Op activates dynamic repair selectively and avoids unnecessary multi-agent execution on easy tasks.

## 5. Shared implementation requirements for all case studies

### 5.1 Typed artifact exchange

Every node should produce machine-readable artifacts. Free-form text is allowed only as an auxiliary explanation.

Required artifact types:

- `de_results_v1`;
- `gene_set_v1`;
- `enrichment_results_v1`;
- `geneagent_report_v1`;
- `perturbation_dataset_v1`;
- `perturbation_prediction_v1`;
- `benchmark_metrics_v1`;
- `workflow_blueprint_v1`;
- `gate_event_v1`.

### 5.2 Sandbox profiles

| Profile | Use | Network | GPU | Example |
|---|---|---:|---:|---|
| `r_bioc_offline` | DESeq2, edgeR, limma | no after setup | no | Case 1 DE |
| `bio_db_online` | GeneAgent database calls | yes, restricted | no | Case 1 GeneAgent |
| `gpu_sc_model` | scGPT, GEARS, scFoundation | no after data/checkpoints | yes | Case 2 |
| `python_eval_locked` | HumanEval/MBPP tests | no | no | Case 3 code |
| `repo_repair` | SWE-bench repo tests | no or restricted | optional | Case 3 SWE-bench |

### 5.3 Run directory structure

```text
runs/{case_study}/{dataset}/{method}/{timestamp}/
  config.yaml
  git_state.txt
  external_repos.json
  data_hashes.json
  environment.json
  workflow_blueprint_initial.json
  workflow_blueprint_final.json
  gate_events.jsonl
  artifacts/
  predictions/
  metrics.json
  report.md
```

### 5.4 Minimum case-study deliverables

| Case study | Minimum deliverable |
|---|---|
| Case 1 | `airway` DESeq2 run, up/down marker artifacts, enrichment, GeneAgent report, evidence verifier, final report. |
| Case 2 | Norman and Replogle K562 runs for simple baselines, GEARS, and at least one foundation-model adapter; ensemble report. |
| Case 3 | HumanEval, MBPP, and MATH dynamic experiments comparing AFlow-imported, AC-AFlow+Skills, and AC-AFlow+Skills+Gates. |

## 6. References

### Multi-agent workflow design

- AFlow: https://arxiv.org/abs/2410.10762
- AFlow code: https://github.com/FoundationAgents/AFlow
- ADAS: https://arxiv.org/abs/2408.08435
- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, Multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI Agents SDK orchestration: https://developers.openai.com/api/docs/guides/agents/orchestration

### Case Study 1

- DESeq2: https://www.nature.com/articles/s13059-014-0550-8
- DESeq2 vignette: https://bioconductor.org/packages/release/bioc/html/DESeq2.html
- `airway` RNA-seq workflow: https://www.bioconductor.org/packages/release/data/experiment/html/airway.html
- limma/voom workflow: https://pmc.ncbi.nlm.nih.gov/articles/PMC4937821/
- GSEA: https://www.gsea-msigdb.org/
- GeneAgent: https://www.nature.com/articles/s41592-025-02748-6
- GeneAgent code: https://github.com/ncbi-nlp/GeneAgent
- GenoTEX: https://liu-hy.github.io/GenoTEX/
- recount3: https://rna.recount.bio/

### Case Study 2

- scGPT: https://github.com/bowang-lab/scGPT
- scGPT model card: https://virtualcellmodels.cziscience.com/model/scgpt
- GEARS: https://github.com/snap-stanford/GEARS
- GEARS paper: https://www.nature.com/articles/s41587-023-01905-6
- Geneformer: https://www.nature.com/articles/s41586-023-06139-9
- scFoundation: https://github.com/biomap-research/scFoundation
- scPerturb: https://projects.sanderlab.org/scperturb/
- scPerturBench: https://bm2-lab.github.io/scPerturBench-reproducibility/
- Replogle processed Perturb-seq data: https://plus.figshare.com/articles/dataset/_Mapping_information-rich_genotype-phenotype_landscapes_with_genome-scale_Perturb-seq_Replogle_et_al_2022_processed_Perturb-seq_datasets/20029387
- Norman 2019 Perturb-seq via pertpy: https://pertpy.readthedocs.io/en/latest/api/data/pertpy.data.norman_2019_raw.html
- Systema perturbation benchmark: https://www.nature.com/articles/s41587-025-02777-8

### Case Study 3

- SWE-bench: https://www.swebench.com/
- SWE-bench Lite: https://www.swebench.com/lite.html
- SWE-bench Verified: https://www.swebench.com/verified.html
- HumanEval: https://github.com/openai/human-eval
- MBPP: https://github.com/google-research/google-research/tree/master/mbpp
- MATH: https://github.com/hendrycks/math
