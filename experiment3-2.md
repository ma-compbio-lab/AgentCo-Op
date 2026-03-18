Below is a benchmark-style Markdown spec you can adapt directly for your paper, README, or experiment plan.

---

# Benchmark Specification: `cell2location` Repository Transfer on a New Spatial Transcriptomics Dataset

## 1. Overview

This benchmark evaluates whether a computational biology agent can **read a paper, inspect and execute the official GitHub repository in Docker/sandbox, adapt the repository to a new dataset, and produce paper-style figures on that new dataset** rather than only replaying the original demo. The benchmark is centered on **cell2location**, a Bayesian method for mapping fine-grained cell types in spatial transcriptomics by combining spatial counts with scRNA-seq-derived reference signatures while modeling technical effects such as platform effects, contamination, and unexplained variation. The official documentation exposes a full workflow—reference-signature estimation, spatial mapping, visualization, and downstream region/compartment analysis—and the project provides container guidance, which makes it a strong fit for a repo-transfer benchmark. ([Nature][1])

The core claim this benchmark is designed to test is **workflow extensibility**: given a paper and its repository, an agent should be able to transfer that workflow to a biologically related but operationally different dataset, resolve environment and schema issues, run the method end-to-end, and generate interpretable outputs. This is a stronger test than “write code from scratch” or “run the default notebook,” because it requires **repository understanding, dependency isolation, data adaptation, dynamic replanning, and figure-level scientific execution**. The official `cell2location` tutorial itself is multi-stage and uses public data from a human lymph node Visium study plus a multi-dataset scRNA reference, which makes transfer to a different tissue a meaningful nontrivial generalization rather than a trivial rerun. ([cell2location][2])

---

## 2. Why `cell2location` is a Strong Repo-Transfer Benchmark

`cell2location` is a particularly good benchmark target because it sits at the intersection of **scientific complexity** and **systems complexity**. Scientifically, the method requires a valid spatial count matrix, a compatible single-cell reference, and a biologically sensible prior on expected cells per location. Operationally, the repository depends on a modern probabilistic single-cell stack (`scvi-tools` and Pyro), recommends a **separate conda environment**, and ships an official Docker image with GPU and mounted-volume usage instructions. Those characteristics make it ideal for demonstrating why an agent that can build repo-specific sandboxes and dynamically restructure a multi-agent workflow should outperform a fixed or monolithic workflow. ([GitHub][3])

The official tutorial also defines a natural “figure contract” for the benchmark. It explicitly walks through: loading Visium and scRNA reference data, estimating cell-type signatures with negative binomial regression, running spatial mapping, visualizing cell abundance, and performing downstream analysis via **Leiden clustering** and **NMF-based tissue zones / cellular compartments**. That means success can be judged not only by “did the script finish,” but by whether the agent produced the right classes of artifacts and figures on the new dataset. ([cell2location][2])

---

## 3. Benchmark Task

### 3.1 Task statement

Given:

1. the `cell2location` paper,
2. the official `BayraktarLab/cell2location` repository,
3. a new target spatial transcriptomics dataset,
4. a compatible new scRNA-seq reference dataset,

the agent must:

* infer the required workflow from the paper and repository,
* build and run the repository in Docker/sandbox,
* adapt the new data to the repository’s expected input schema,
* execute reference-signature estimation and spatial mapping,
* run downstream analyses analogous to the official tutorial,
* and produce figures and a report on the new dataset.

This benchmark does **not** ask the agent to reimplement the method from scratch. It asks the agent to **transfer and operationalize the original repository** on new data.

### 3.2 What the benchmark is supposed to prove

A strong result on this benchmark would support the claim that your workflow can:

* execute arbitrary third-party computational biology repositories inside isolated environments,
* adapt repository expectations to new datasets with different schemas,
* dynamically insert new sub-agents when failures occur,
* and preserve scientific intent from paper to output figure.

---

## 4. Recommended Dataset Configuration

## 4.1 Source method/demo context

The official `cell2location` tutorial uses a **public human lymph node Visium dataset** and maps a **34-cell-type reference atlas** assembled from integrated scRNA-seq datasets from human secondary lymphoid organs. The tutorial also recommends estimating reference signatures with **NB regression**, especially when combining multiple technologies or batches. This is an excellent source workflow, but running the same lymph node example is not a strong transfer test. ([cell2location][2])

## 4.2 Recommended target task: Human breast cancer transfer

I recommend the following **standard benchmark track**:

### Target spatial dataset

Use the public **10x Genomics Visium human breast cancer fresh frozen** dataset. The page reports an invasive ductal carcinoma sample with **4,898 spots detected under tissue**, **median 9,720 UMIs per spot**, and **median 3,654 genes per spot**. This is a good target because it is public, widely recognized, visually interpretable, and outside the original lymph node tutorial. The same page also notes that sequence data are not publicly available for this sample, which is another reason the benchmark should start from processed matrices / Space Ranger outputs or packaged `AnnData`, not from FASTQ. ([10x Genomics][4])

### Target scRNA reference: Standard mode

Use the **Wu et al. 2021 breast cancer atlas** as the standard reference. That study analyzed **26 primary pre-treatment breast tumors** and retained **130,246 single cells** after QC, providing a strong disease-matched reference for tumor, immune, and stromal compartments. The associated public record also provides **Visium spatial transcriptomics data for 6 primary breast cancers**, which makes the dataset family well established and spatially relevant even if your benchmark uses an independent 10x target slide. ([PMC][5])

### Target scRNA reference: Hard mode

Use the **Chen et al. 2026 integrated human breast cancer atlas** as a harder reference. The paper describes **more than 600,000 cells across 138 patients**, making it a much larger and more heterogeneous reference. This version is useful if you want a stress test for scalability, ontology harmonization, and memory management. 

## 4.3 Why this dataset pair is well chosen

The breast cancer track is attractive because it is **biologically coherent** and **operationally realistic**. The method is tissue-agnostic as long as the scRNA reference and spatial data are compatible; the transfer from a lymph node tutorial to breast cancer is substantial enough to require new reasoning, but not so extreme that failure would mostly reflect modality mismatch. The 10x breast cancer slide offers a clean, public Visium target, and the Wu/Chen references provide disease-matched single-cell atlases at two difficulty levels. ([10x Genomics][4])

---

## 5. Why the Benchmark Should Start from Processed Data

This benchmark is about **repository transfer**, not upstream raw-sequencing preprocessing. Starting from FASTQ would confound the evaluation with alignment, Space Ranger/Cell Ranger setup, storage overhead, and unrelated pipeline brittleness. Starting from processed spatial counts plus histology and from a prepared reference `AnnData` keeps the focus on the core repo-transfer challenge that `cell2location` actually solves. That choice is also consistent with the repository’s own workflow, which consumes **untransformed, unnormalized spatial mRNA counts** and scRNA-derived reference information rather than raw sequencing reads. ([cell2location][2])

---

## 6. Benchmark Inputs

A benchmark instance should provide the following inputs:

```text
benchmark_instance/
├── paper/
│   └── cell2location_paper.pdf
├── repo/
│   └── repo_url.txt
├── data/
│   ├── spatial/
│   │   ├── filtered_feature_bc_matrix.h5
│   │   ├── spatial/
│   │   └── metadata.json
│   ├── reference/
│   │   ├── reference_raw.h5ad
│   │   └── reference_metadata.md
│   └── gene_maps/
│       └── gene_symbol_aliases.tsv
├── task/
│   ├── task.md
│   └── expected_outputs.json
└── environment/
    └── sandbox_rules.md
```

### Required semantic content of the inputs

* **Paper**: the original `cell2location` paper.
* **Repository**: the official GitHub repo URL.
* **Spatial dataset**: processed spatial counts, coordinates, histology assets, and metadata.
* **Reference dataset**: raw-count single-cell `AnnData` with cell-type annotations and batch metadata.
* **Gene mapping aids**: optional symbol/alias map to make schema repair tractable.
* **Task file**: explicit success criteria and output contract.

---

## 7. Expected Repository-Transfer Workflow

The official `cell2location` workflow is naturally decomposed into: loading Visium and single-cell data, estimating cell-type signatures with NB regression, running spatial mapping, visualizing abundance, and performing downstream analysis including Leiden clustering and NMF-based tissue zones. The benchmark should preserve that structure but require transfer to the new breast cancer data. ([cell2location][2])

### 7.1 Stage 0 — Read paper and inspect repo

The agent should first infer:

* the method’s required inputs,
* the expected workflow stages,
* the outputs and figures shown in the docs/tutorial,
* the dependency model,
* and any version or environment assumptions.

This stage should produce a short machine-readable plan, such as `plan.json`, before execution begins.

### 7.2 Stage 1 — Build the repository environment in Docker/sandbox

The agent should prefer the official repository/container path over ad hoc local installs. The repo recommends a separate conda environment, and the docs provide a Docker image plus mounted-directory guidance. This is the point where your workflow’s sandbox capability becomes central. The benchmark should allow the agent to clone the repo, build or pull the container, mount a shared workspace, and create a patched execution wrapper if needed. ([GitHub][3])

### 7.3 Stage 2 — Adapt the new datasets to the repository schema

The reference-signature model expects a single-cell `AnnData` object registered via `setup_anndata`, where raw counts can be taken from a layer and metadata include at least a **batch key** and a **labels key**. In the tutorial example, the repo uses `batch_key='Sample'`, `labels_key='Subset'`, and optional categorical covariates such as `Method`. The spatial model likewise expects a registered spatial `AnnData` object plus a `cell_state_df` table of reference signatures. ([cell2location][6])

This stage is where real transfer difficulty lives. The agent may need to:

* harmonize gene identifiers,
* map reference cell-type columns to the expected label key,
* reconstruct or relocate raw counts into an expected layer,
* convert Space Ranger outputs into `AnnData`,
* and validate gene overlap between reference and spatial data.

### 7.4 Stage 3 — Estimate reference cell-type signatures

The docs recommend NB regression to estimate reference signatures when batch and technology effects are present. On breast cancer data, this is likely the correct path. The agent should set up the reference model, train it, export the reference signature matrix, and save both the trained reference object and the derived `cell_state_df`. ([cell2location][2])

### 7.5 Stage 4 — Run spatial mapping with `cell2location`

The spatial model requires **untransformed, unnormalized spatial counts** and an estimate of **expected average cell abundance per location** (`N_cells_per_location`). The docs state that this prior can be estimated by counting nuclei in roughly 10–20 histology locations, or approximated from capture-region size if paired histology inspection is not available. This requirement is valuable for benchmarking because it may force the agent to insert a new reasoning branch: histology inspection, heuristic prior estimation, or user-configured fallback. ([cell2location][2])

### 7.6 Stage 5 — Generate figures analogous to the official workflow

The benchmark should require figures analogous to the official tutorial, not just a completed model object. At minimum, the agent should produce:

* several **cell-abundance spatial maps** for selected cell types,
* a **Leiden tissue-region clustering** figure,
* an **NMF tissue-zone / cellular-compartment** figure,
* and a training / QC summary.

These figure classes are directly aligned with the official docs and downstream notebooks. ([cell2location][2])

### 7.7 Stage 6 — Write a transfer report

The benchmark should require a report that states:

* what was changed relative to the original repo/tutorial,
* what assumptions were inferred from the paper/docs,
* what failed and how it was repaired,
* what data transformations were applied,
* and which figures were generated and why they are biologically plausible.

---

## 8. Why This Benchmark Strongly Exercises Dynamic Multi-Agent Topology

A fixed single-agent or fixed-DAG workflow is likely to fail on this task for mundane but important reasons: version conflicts, missing metadata keys, gene-identifier mismatches, raw-count placement errors, or failure to set a defensible `N_cells_per_location` prior. The official repo’s own installation guidance, separate-environment recommendation, and Docker support all suggest that environment handling is part of the real operational burden. ([GitHub][3])

This benchmark is therefore especially good for showing **dynamic topology adaptation**. A reasonable topology might start with:

* a **Paper Reader** agent,
* a **Repo/Environment** agent,
* a **Data Adapter** agent,
* a **Runner** agent,
* and a **Figure/Evaluator** agent.

But the topology should be allowed to change when new problems are discovered. For example:

* if the container build fails, insert an **Environment Repair** agent;
* if `batch_key` or `labels_key` is missing, insert a **Schema Mapper** agent;
* if `N_cells_per_location` is absent, insert a **Histology Prior Estimator** agent;
* if output figures do not match the paper/tutorial intent, insert a **Figure Spec Checker** agent;
* if training fails or produces degenerate outputs, insert a **Recovery / Debugging** agent.

That dynamic branching behavior is precisely what makes this benchmark valuable for your system.

---

## 9. Detailed Task Definition

## 9.1 Input contract

The agent receives:

* the `cell2location` paper PDF,
* the official repository URL,
* a target spatial dataset from human breast cancer,
* a target scRNA reference dataset from breast cancer,
* permission to run Docker/sandboxed code and call tools,
* a writable shared workspace.

## 9.2 Allowed actions

The agent may:

* clone the GitHub repository,
* build or pull containers,
* patch configuration files or write wrapper scripts,
* convert data formats,
* generate notebook or script-based figures,
* create small helper utilities.

The agent may **not**:

* replace `cell2location` with a different method,
* manually fabricate outputs without executing the workflow,
* bypass the repo by submitting a purely narrative answer.

## 9.3 Success criteria

A run counts as successful only if all of the following are true:

1. The repository is actually executed in Docker/sandbox.
2. The target breast cancer data are adapted into a compatible input representation.
3. Reference signature estimation and spatial mapping both run to completion.
4. The run produces paper-style figures on the new data.
5. The agent outputs a reproducible transfer report and run manifest.

---

## 10. Recommended Output Contract

```text
outputs/
├── environment/
│   ├── container_build.log
│   ├── environment_manifest.json
│   └── repo_commit.txt
├── adapted_data/
│   ├── reference_adapted.h5ad
│   ├── spatial_adapted.h5ad
│   └── gene_overlap_report.tsv
├── models/
│   ├── reference_signature_model/
│   ├── spatial_mapping_model/
│   └── cell_state_df.csv
├── figures/
│   ├── abundance_map_epithelial.png
│   ├── abundance_map_immune.png
│   ├── abundance_map_stromal.png
│   ├── region_clusters.png
│   ├── nmf_compartments.png
│   └── training_qc.png
├── reports/
│   ├── transfer_report.md
│   ├── run_manifest.json
│   └── failure_recovery_log.json
└── summary/
    └── final_summary.json
```

---

## 11. Detailed Agent Prompt

## 11.1 System prompt

```text
You are a computational biology repository-transfer agent.

Your task is to read a scientific paper, inspect the official GitHub repository for the method, and transfer that repository to a new dataset.

You may use Docker/sandboxed execution, patch scripts, write adapters, and create helper utilities. Prefer running the official repository over reimplementing the method from scratch.

You must:
1. infer the required workflow from the paper and repository,
2. build or run the repository in an isolated environment,
3. adapt the new datasets to the repository’s expected schema,
4. execute the method end-to-end,
5. generate figures analogous to those in the official tutorial or paper,
6. write a reproducible transfer report.

Important constraints:
- Do not silently replace the method with another package.
- Preserve raw-count semantics where required.
- Keep track of every major decision, patch, input file, and output file.
- If execution fails, diagnose the failure and modify the workflow topology by inserting new repair/debug steps.
- Version all important artifacts; do not overwrite previous runs without recording the change.
```

## 11.2 Task prompt

```text
You are given:
- the cell2location paper,
- the official BayraktarLab/cell2location repository,
- a new human breast cancer spatial transcriptomics dataset,
- a compatible human breast cancer scRNA-seq reference dataset,
- Docker/sandbox execution capability.

Your objective is to transfer the official cell2location workflow from its original tutorial/demo context to this new breast cancer dataset.

Required steps:
1. Read the paper and infer the workflow stages and expected outputs.
2. Inspect the repository and identify the recommended execution path.
3. Build or run the repository in Docker/sandbox.
4. Convert the new datasets into the format expected by the repo.
5. Estimate reference cell-type signatures from the scRNA-seq reference.
6. Run spatial mapping on the new breast cancer spatial dataset.
7. Produce at least:
   - multiple cell abundance maps,
   - one tissue-region clustering figure,
   - one NMF/compartment figure,
   - one training/QC figure.
8. Write a transfer report explaining:
   - what you changed,
   - what assumptions you inferred,
   - what failed,
   - how you fixed it,
   - and why the outputs are scientifically plausible.

Method-specific requirements:
- Use raw, untransformed, unnormalized spatial counts.
- Estimate or justify N_cells_per_location.
- Respect the repository’s schema expectations for raw counts, batch annotations, and cell-type labels.
- Save all adapted data, model outputs, plots, and logs.

Success is defined by a real end-to-end run on the new dataset, not by a theoretical description.
```

## 11.3 Optional stricter evaluator prompt

```text
Evaluate the run on:
1. repository execution authenticity,
2. correctness of data adaptation,
3. completion of reference-signature estimation,
4. completion of spatial mapping,
5. quality and completeness of generated figures,
6. reproducibility of the transfer report,
7. evidence of adaptive recovery when failures occurred.
```

---

## 12. Evaluation Plan

## 12.1 Primary metrics

### A. Repository execution success

Did the agent actually run the official repo in Docker/sandbox, and did it produce executable artifacts rather than only narrative output?

### B. Data adaptation correctness

Did the agent correctly construct compatible spatial and reference `AnnData` objects, preserve raw counts, harmonize genes, and provide required metadata fields?

### C. Scientific workflow completion

Did the run complete the two core model stages:

* reference signature estimation,
* spatial mapping?

These are the two key computational stages exposed in the official workflow. ([cell2location][2])

### D. Figure fidelity

Did the outputs include the same kinds of figures emphasized by the official docs:

* cell-abundance maps,
* Leiden-based region clustering,
* NMF tissue zones / compartments? ([cell2location][2])

### E. Adaptive recovery quality

When errors occurred, did the agent successfully add repair branches rather than failing permanently?

### F. Reproducibility

Are there enough logs, manifests, and patched scripts to rerun the transfer?

## 12.2 Example weighted score

```text
Final Score =
0.20 * Repo Execution
+ 0.20 * Data Adaptation
+ 0.20 * Workflow Completion
+ 0.15 * Figure Fidelity
+ 0.15 * Adaptive Recovery
+ 0.10 * Reproducibility
```

---

## 13. Baselines and Ablations

To show the value of your workflow, compare against:

### Baseline 1 — Fixed-topology agent

Same model, but no dynamic insertion of repair/debug/data-adaptation agents.

### Baseline 2 — No sandbox / monolithic environment

Same model, but force all execution into one environment instead of repo-specific containers.

### Baseline 3 — Paper-only reasoning

Allow the agent to read the paper but not execute the repo.

### Baseline 4 — Repo-only execution

Allow the agent to run the repo but not inspect the paper, so it cannot infer hidden requirements like figure intent or the role of `N_cells_per_location`.

### Baseline 5 — Human-written transfer script

A manually prepared narrow script for a single dataset pair.

These ablations help isolate where your gains come from: repo execution, paper understanding, dynamic topology, or sandboxing.

---

## 14. Hidden Failure Cases to Include

To make the benchmark nontrivial, inject one or more hidden perturbations per run:

* missing or renamed metadata keys for the reference (`Sample`, `Subset`, etc. no longer match tutorial names),
* gene symbol / alias mismatches,
* counts stored in a nonstandard layer,
* missing `N_cells_per_location`,
* GPU unavailable despite container support,
* a minor dependency mismatch that requires changing the execution plan,
* histology assets present but not preprocessed.

These perturbations are realistic and strongly differentiate a robust adaptive workflow from a static execution chain.

---

## 15. Expected Results

A strong agent should be able to:

1. inspect the paper and repo,
2. provision the correct sandbox/container path,
3. harmonize the breast cancer reference and spatial inputs,
4. estimate reference signatures,
5. run spatial mapping,
6. generate several abundance maps plus region and NMF plots,
7. and produce a report documenting modifications and recovery steps.

The strongest runs will also demonstrate **controlled topology expansion**: for example, dynamically inserting a schema-mapping agent or histology-prior estimation agent only when needed, rather than always hard-coding those steps.

A weak agent will typically fail in one of four ways:

* it cannot get the repo running in an isolated environment,
* it cannot adapt the data into the expected input schema,
* it runs a partial workflow without producing the correct figure classes,
* or it gives up when encountering missing metadata / dependency issues.

---

## 16. Why This Benchmark Is Good Evidence of Extensibility

This task is valuable because it is not a narrow benchmark built around a custom internal pipeline. It uses a **real published paper**, a **real public GitHub repository**, a **real public target dataset**, and a **real transfer objective**. Success therefore demonstrates more than single-step tool use. It shows that the agent can **operationalize external scientific software**, **reconfigure its workflow when reality deviates from the tutorial**, and **preserve the method’s scientific semantics when moving to a new dataset**. The fact that `cell2location` has an official tutorial structure, explicit input requirements, downstream analyses, and official container guidance makes it especially well suited to this role. ([cell2location][2])

---

## 17. Recommended Final Benchmark Variant Names

You can present the benchmark as three nested variants:

### `C2L-Transfer-Std`

* Spatial target: 10x Visium human breast cancer
* Reference: Wu et al. 2021 breast cancer scRNA atlas
* Goal: standard repo-transfer benchmark

### `C2L-Transfer-Hard`

* Spatial target: same
* Reference: Chen et al. 2026 integrated breast cancer atlas
* Goal: scale and ontology-harmonization stress test

### `C2L-Transfer-Perturbed`

* Same as standard, but with hidden schema/dependency perturbations
* Goal: dynamic-topology and recovery benchmark

---

If you want, I can next turn this into a **paper-ready experimental section** with:

1. a formal problem definition,
2. a benchmark table,
3. an evaluation rubric,
4. and a polished “Methods / Experimental Setup” subsection.

[1]: https://www.nature.com/articles/s41587-021-01139-4?utm_source=chatgpt.com "Cell2location maps fine-grained cell types in spatial ..."
[2]: https://cell2location.readthedocs.io/en/latest/notebooks/cell2location_tutorial.html "Mapping human lymph node cell types to 10X Visium with Cell2location — cell2location  documentation"
[3]: https://github.com/BayraktarLab/cell2location "GitHub - BayraktarLab/cell2location: Comprehensive mapping of tissue cell architecture via integrated single cell and spatial transcriptomics (cell2location model) · GitHub"
[4]: https://www.10xgenomics.com/datasets/human-breast-cancer-visium-fresh-frozen-whole-transcriptome-1-standard "Human Breast Cancer: Visium Fresh Frozen, Whole Transcriptome | 10x Genomics"
[5]: https://pmc.ncbi.nlm.nih.gov/articles/PMC9044823/ "
            A single-cell and spatially resolved atlas of human breast cancers - PMC
        "
[6]: https://cell2location.readthedocs.io/en/latest/cell2location.reference_models.html "Reference signatures (NB regression) — cell2location  documentation"
