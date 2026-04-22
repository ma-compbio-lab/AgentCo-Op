# AgentCo-Op experiments.md

This document provides the experimental plan for AgentCo-Op. The experiments have two main tracks:

1. **AFlow-aligned standard benchmarks**: HotpotQA, DROP, HumanEval, MBPP, GSM8K, and MATH, strictly aligned with AFlow’s data splits, metrics, and reporting style.
2. **Specialized agent collaboration**: wrap BioDiscoveryAgent and SpatialAgent / SpatialBench to validate AgentCo-Op’s ability to coordinate existing GitHub repos / specialized agents.

---

## 1. Experimental hypotheses

### H1: Compiled workflows can achieve competitive performance without task-specific topology search

Comparison targets: AFlow, ADAS, CoT, CoT-SC, Self-Refine, MultiPersona, MedPrompt, and single-agent fallback.

The goal is not to prove SOTA on every dataset, but to show that AgentCo-Op:

- does not rely on validation-driven topology search;
- adapts more flexibly to changing task types;
- controls cost and latency more explicitly;
- naturally integrates repos / specialized agents.

### H2: Simplicity-first routing avoids negative returns from multi-agent workflows

On simple GSM8K, simple MBPP, and simple HotpotQA subsets, forced multi-agent execution may be more expensive than single-agent execution without improving accuracy. AgentCo-Op should show that it can automatically choose direct / single-agent topologies.

### H3: Gated local refinement is more effective than blind multi-round reflection

Compare:

- no refinement;
- fixed self-refine;
- gated refinement.

Metrics: score, cost, latency, and rescue rate per gate.

### H4: Sandboxed repo nodes allow a high-level agent to invoke specialized agents

Use SpatialAgent / BioDiscoveryAgent as execution backends to demonstrate:

- automatic or semi-automatic repo wrapping;
- smoke testing;
- high-level planner generation of task parameters;
- repo execution in a sandbox with typed artifact outputs;
- integrator-level result synthesis.

---

## 2. AFlow-aligned benchmark setup

### 2.1 Datasets

AFlow uses six tasks:

| Dataset | Type | AFlow setting | Metric |
|---|---|---|---|
| HotpotQA | multi-hop QA | randomly sample 1,000 examples | F1 |
| DROP | reading comprehension + discrete reasoning | randomly sample 1,000 examples | F1 |
| HumanEval | code generation | full dataset | pass@1 |
| MBPP | code generation | full dataset | pass@1 |
| GSM8K | grade-school math | full dataset | solve rate |
| MATH | competition math | 617 examples: Combinatorics & Probability, Number Theory, Pre-algebra, Pre-calculus; difficulty level 5 | solve rate |

AFlow further uses:

- validation:test = 1:4.
- random seed = 42.
- blank template run 5 times on validation, selecting high-variance questions as the final validation set.
- workflow evaluation run 5 times on validation, reporting mean and standard deviation.
- test results should preferably be run 3 times and averaged to reduce randomness.

### 2.2 Strict reproduction mode

If the goal is to align exactly with the AFlow paper, prioritize the official AFlow code and data processing scripts instead of resampling yourself.

Steps:

```bash
git clone https://github.com/FoundationAgents/AFlow.git external/AFlow
cd external/AFlow
git rev-parse HEAD > ../../configs/aflow_commit.txt
python data/download_data.py
```

Then inspect the generated data directories and split files. If the official repo already contains processed `val/test` files, read them directly.

AgentCo-Op should implement a converter:

```bash
agentcoop data import-aflow \
  --aflow-dir external/AFlow \
  --out data/aflow_aligned
```

Output a unified format:

```jsonl
{"dataset":"gsm8k","split":"validation","task_id":"...","prompt":"...","reference":"...","metadata":{...}}
{"dataset":"gsm8k","split":"test","task_id":"...","prompt":"...","reference":"...","metadata":{...}}
```

In strict mode, the report should clearly state:

- AFlow repo commit.
- data download date.
- split file hash.
- evaluator commit / version.
- whether the high-variance validation subset was reproduced.

### 2.3 Clean-room mode

If the official splits cannot be directly obtained, reproduce the paper description:

```python
import random
random.seed(42)
indices = list(range(len(dataset)))
random.shuffle(indices)
val_size = int(0.2 * len(indices))
val_indices = indices[:val_size]
test_indices = indices[val_size:]
```

Then run the blank template 5 times on validation, compute per-question output variance / correctness variance, and select high-variance questions as the final validation set.

Note: clean-room mode only aligns with the paper’s description; it does not guarantee the exact same examples as the AFlow paper. Therefore, if the main experiment claims exact alignment, use strict reproduction mode.

---

## 3. Data download and processing

### 3.1 HotpotQA

Recommended source: Hugging Face `hotpot_qa` or official HotpotQA.

Processing:

1. Match AFlow’s distractor / fullwiki setting.
2. Sample 1,000 examples with seed 42.
3. The input should include the question and necessary context; if using a retrieval version, specify the corpus.
4. The reference is the answer; supporting facts can help the evidence evaluator, but AFlow’s main metric is answer F1.

Output fields:

```json
{
  "task_id": "hotpotqa_<id>",
  "question": "...",
  "context": [...],
  "answer": "...",
  "supporting_facts": [...]
}
```

### 3.2 DROP

Recommended source: Hugging Face `drop` or the official AllenAI release.

Processing:

1. Sample 1,000 examples.
2. The prompt should include passage + question.
3. References should use the official answer annotations.
4. Use the official DROP F1 / EM normalization evaluator.

Note: DROP includes numeric answers, multi-span answers, dates, and other answer formats. The AgentCo-Op finalizer must perform answer normalization.

### 3.3 HumanEval

Use the OpenAI HumanEval package / `openai_humaneval` data.

Processing:

1. prompt = function signature + docstring.
2. prediction = completion code.
3. pass@1 = generate one sample and run the official unit tests.
4. Run in a sandbox with network disabled.

Safety: HumanEval evaluation executes generated code, so it must be done in an isolated container.

### 3.4 MBPP

Recommended source: Google Research MBPP or Hugging Face `mbpp`.

Processing:

1. Use the split corresponding to AFlow; if official AFlow uses the full dataset, record the MBPP version.
2. The prompt should include the problem statement + examples/tests.
3. The evaluator runs tests and reports pass@1.
4. Pay attention to differences between sanitized and non-sanitized MBPP versions.

### 3.5 GSM8K

Recommended source: Hugging Face `gsm8k` main.

Processing:

1. Use full test / train depending on AFlow code; in strict mode, follow AFlow data.
2. The final answer reference is the number after `####`.
3. Predictions need final-number extraction and comma / decimal normalization.
4. Metric = solve rate.

### 3.6 MATH

Recommended source: official MATH dataset / Hugging Face.

Processing:

1. Select only difficulty level 5.
2. Select only four categories:
   - Counting & Probability / Combinatorics & Probability
   - Number Theory
   - Prealgebra / Pre-algebra
   - Precalculus / Pre-calculus
3. The total sample count should be 617; if not, the data version or category-name mapping is likely wrong.
4. Use a math answer extractor for LaTeX `\boxed{}`, fractions, sets, and intervals.
5. Metric = solve rate.

---

## 4. Metrics

### 4.1 Main metrics

| Dataset | Metric | Description |
|---|---|---|
| HotpotQA | F1 | official answer token F1 |
| DROP | F1 | official DROP F1 |
| HumanEval | pass@1 | one generation, unit tests pass |
| MBPP | pass@1 | one generation, unit tests pass |
| GSM8K | solve rate | exact numeric answer |
| MATH | solve rate | exact / symbolically normalized answer |

### 4.2 Cost metrics

Report for every dataset:

- input tokens.
- output tokens.
- total tokens.
- estimated USD cost.
- wall-clock latency.
- number of LLM calls.
- number of tool calls.
- number of sandbox runs.
- gate activation count.

### 4.3 Structural metrics

AgentCo-Op-specific metrics:

| Metric | Purpose |
|---|---|
| route distribution | verify that simplicity-first routing is active |
| average graph size | measure the complexity of compiled topologies |
| gate trigger frequency | analyze whether dynamic refinement is over-triggered |
| gate rescue rate | fraction of answers changed from wrong to correct after a gate |
| unnecessary refinement rate | fraction of gates that add cost without improving the answer |
| specialist disagreement rate | test whether multiple specialists are actually useful |
| repo setup success rate | measure reliability of specialized repo wrappers |

---

## 5. Baselines

### 5.1 Standard baselines

Align with AFlow tables:

1. IO: direct input-output.
2. CoT: chain-of-thought-style prompting; output the final answer.
3. CoT-SC: self-consistency with 5 answers.
4. MultiPersona: multiple roles answer, followed by aggregation.
5. Self-Refine: up to 3 refinement rounds.
6. MedPrompt: 3 answers + 5 votes.
7. ADAS: run or cite official workflow / reported results if available.
8. AFlow: official optimized workflow or reported results.

### 5.2 AgentCo-Op variants

| Variant | Description | Purpose |
|---|---|---|
| AC-Direct | Force L0/L1 simple topology | Test single-agent fallback |
| AC-Compiled | Compile base graph without gated refinement | Test meta-skill topology |
| AC-Gated | Compile base graph + gated local refinement | Main method |
| AC-NoMeta | Remove meta-skill evidence / use only coarse rules | Test value of meta-skills |
| AC-NoSkillMemory | Do not use historical skill memory | Test impact of long-term memory |
| AC-ForcedMulti | Force all tasks into multi-agent workflows | Show that multi-agent is not always beneficial |
| AC-NoSandbox | Do not use sandbox execution nodes for code/repo tasks | Test contribution of execution backends |
| AC-NoReviewer | Remove reviewer and use direct integration only | Test reviewer/gate value |

---

## 6. Standard benchmark workflows

### 6.1 HotpotQA workflow

Recommended base graph:

```text
QuestionProfiler
  → QueryPlanner
  → Retriever / ContextSelector
  → EvidenceSelector
  → Answerer
  → EvidenceReviewer
  → Finalizer
```

Gates:

- evidence_missing → add retrieval query.
- answer unsupported → rewrite with evidence only.
- low confidence → second answerer with same evidence.

Ablations:

- no retrieval planner.
- no evidence reviewer.
- parallel answerer vs single answerer.

### 6.2 DROP workflow

```text
PassageReader
  → SpanExtractor
  → OperationClassifier
  → NumericReasoner
  → AnswerFormatter
  → DROPFormatReviewer
```

Gates:

- numeric inconsistency → recompute.
- answer type mismatch → formatter.
- low confidence → second numeric reasoner.

### 6.3 HumanEval / MBPP workflow

```text
ProblemParser
  → Programmer
  → SandboxUnitTest
  → RepairPlanner on failure
  → FinalCodeFormatter
```

Gates:

- syntax error → repair.
- failing public tests → repair.
- timeout → inspect complexity / fallback to simpler implementation.
- environment error → environment repair, not code repair.

Metrics: pass@1. The main result permits only one final code submission; internal repair does not count as multiple submissions, but its cost must be reported.

### 6.4 GSM8K workflow

```text
MathProfiler
  → WordProblemSolver
  → ArithmeticVerifier
  → FinalAnswerExtractor
```

Do not default to multiple specialists for simple problems. Gates:

- arithmetic verifier fail → one repair.
- extraction fail → formatter.

### 6.5 MATH workflow

```text
MathRouter
  → DomainSpecialist
  → IndependentVerifier
  → FinalAnswerExtractor
```

For level 5 problems, optionally enable:

- two parallel specialists.
- specialist disagreement gate.
- symbolic checker if feasible.

---

## 7. Running protocol

### 7.1 Model settings

To align with AFlow, run at least two settings:

1. **AFlow executor-aligned**: Use the same executor models and temperatures as the AFlow paper as much as possible. AFlow reports using executors such as DeepSeek-V2.5, GPT-4o-mini-0718, Claude-3.5-Sonnet-0620, and GPT-4o-0513, with different temperature settings for different models. In strict mode, follow the AFlow configuration.
2. **Current practical setting**: Use currently available models and report date, version, and price. Because models/APIs may change, this setting is for modern usability and should not be directly compared to AFlow with absolute numerical claims.

### 7.2 Randomness

- Record the seed for all sampling.
- Even models with temperature=0 may not be fully deterministic; run 3 times and report mean ± std.
- Explicitly record sample counts for self-consistency / parallel sampling.

### 7.3 Budget

Set per-problem budgets:

```yaml
budget_by_dataset:
  gsm8k:
    max_llm_calls: 4
    max_cost_usd: 0.20
  math:
    max_llm_calls: 8
    max_cost_usd: 0.50
  humaneval:
    max_repairs: 3
    max_sandbox_runs: 4
  hotpotqa:
    max_retrieval_calls: 5
  specialized:
    max_wall_time_s: 7200
```

When budget is exceeded, output best effort and mark `budget_exceeded=true` in the result table.

---

## 8. Result analysis

### 8.1 Main table

Replicate the AFlow style:

| Method | HotpotQA F1 | DROP F1 | HumanEval pass@1 | MBPP pass@1 | GSM8K Solve | MATH Solve | Avg |
|---|---:|---:|---:|---:|---:|---:|---:|
| IO | | | | | | | |
| CoT | | | | | | | |
| CoT-SC | | | | | | | |
| Self-Refine | | | | | | | |
| ADAS | | | | | | | |
| AFlow | | | | | | | |
| AgentCo-Op Compiled | | | | | | | |
| AgentCo-Op Gated | | | | | | | |

### 8.2 Cost-performance Pareto

For each method, plot:

- x = cost per problem.
- y = metric.
- point size = latency.
- separate by dataset or average.

### 8.3 Route distribution

Example:

| Dataset | L0 Direct | L1 Single+Tool | L3 Specialist | L4 Parallel | L6 RepairLoop | L7 Repo |
|---|---:|---:|---:|---:|---:|---:|
| GSM8K | 20% | 70% | 10% | 0% | 0% | 0% |
| MATH | 0% | 10% | 50% | 40% | 0% | 0% |
| HumanEval | 0% | 0% | 0% | 0% | 100% | 0% |

This table is core evidence for AgentCo-Op: it shows that the system actually selects different topologies for different tasks.

### 8.4 Gate analysis

| Gate | Trigger count | Avg cost added | Rescue count | Rescue rate | Harm count |
|---|---:|---:|---:|---:|---:|
| test_failure | | | | | |
| schema_invalid | | | | | |
| low_confidence | | | | | |
| evidence_missing | | | | | |

Rescue definition: the base graph without the gate predicts incorrectly, and the final answer after the gate is correct.

### 8.5 Failure taxonomy

Manually annotate 100 failure cases:

- wrong topology.
- missing skill.
- retrieval miss.
- arithmetic error.
- code environment error.
- repair loop overfits public tests.
- reviewer false positive / false negative.
- answer extraction error.
- budget exceeded.

---

## 9. Specialized agent collaboration experiments

### 9.1 Why the design must be careful

BioDiscoveryAgent and SpatialAgent are both specialized biology agents, but they are not two baselines for the same benchmark:

- BioDiscoveryAgent targets closed-loop genetic perturbation experiment design.
- SpatialAgent targets spatial biology / spatial transcriptomics data analysis.
- SpatialBench is a verifiable spatial analysis benchmark; BioDiscoveryAgent’s official experiments use perturbation design datasets.

Therefore, split the specialized experiments into three layers:

1. **Repo wrapping reliability**: whether the two repos can be wrapped as sandbox execution nodes.
2. **Single-specialist task performance**: invoke the corresponding specialized agent on its native benchmark.
3. **Cross-specialist collaboration pilot**: use spatial analysis results as context for perturbation design in an exploratory evaluation; unless strong ground truth is constructed, do not claim it as a main benchmark result.

---

## 10. Experiment S1: Repo wrapping reliability

### 10.1 Goal

Validate AgentCo-Op’s ability to “given two GitHub repos, wrap and run them in a sandbox.”

### 10.2 Repos

- BioDiscoveryAgent: https://github.com/snap-stanford/BioDiscoveryAgent
- SpatialAgent: https://github.com/Genentech/SpatialAgent

### 10.3 Protocol

For each repo:

1. pin commit SHA.
2. clone.
3. inspect dependencies.
4. generate Dockerfile / manifest.
5. build image.
6. run import smoke test.
7. run a minimal official example or dry run.
8. write `result.json`.

### 10.4 Metrics

| Metric | Definition |
|---|---|
| build_success | Docker image built |
| import_success | package import works |
| smoke_success | minimal command exits 0 |
| adapter_success | produces valid result.json |
| setup_time | build + smoke wall time |
| manual_steps | number of human interventions |
| reproducibility | same command twice produces same file structure / comparable outputs |
| security_compliance | network/mount/secret policy satisfied |

### 10.5 Baseline

- Manual README installation by researcher.
- AgentCo-Op automatic/semi-automatic wrapper.

This is not a performance benchmark; it supports the system contribution.

---

## 11. Experiment S2: SpatialAgent / SpatialBench

### 11.1 Goal

Test on verifiable spatial biology tasks:

- SpatialAgent as a specialized repo backend.
- AgentCo-Op high-level planner + SpatialAgent + reviewer.
- Whether dynamic repair helps with artifact / grader failures.

### 11.2 Data

The SpatialBench repo description includes 146 verifiable problems, but the full benchmark may have access restrictions; the public part includes canonical examples. Run two tiers:

- **S2-small**: public canonical examples, used for system validation and paper demo.
- **S2-full**: if full SpatialBench access is obtained, run all 146 tasks.

### 11.3 Methods

| Method | Description |
|---|---|
| Direct LLM | Answer from the task only, without calling SpatialAgent |
| Mini SWE / generic coding agent | Use a general coding agent to analyze data |
| SpatialAgent alone | Run SpatialAgent directly according to the official method |
| AC-Spatial | AgentCo-Op planner → SpatialAgent sandbox → artifact reviewer |
| AC-Spatial-Gated | AC-Spatial + failure gates: missing artifact, grader fail, runtime error |
| AC-ForcedMulti | Force an extra bio reviewer / parallel agent to test over-complexity |

### 11.4 Workflow

```text
TaskProfiler
  → SpatialTaskPlanner
  → SpatialAgentSandbox
  → ArtifactCollector
  → SpatialResultReviewer
  → FinalAnswerFormatter
  → SpatialBenchGrader
```

Gates:

- missing required file → rerun with explicit artifact instruction.
- code runtime error → environment repair or rerun.
- grader format fail → formatter only.
- low confidence but grader unavailable → domain reviewer.

### 11.5 Metrics

- pass rate by deterministic grader.
- pass rate by category: QC, normalization, dimension reduction, clustering, cell typing, DE, spatial analysis.
- cost per task.
- wall time.
- number of code/tool calls.
- artifact validity.
- gate rescue rate.

### 11.6 Analysis

The point is not merely to show that SpatialAgent is strong, but to demonstrate that:

1. AgentCo-Op can call SpatialAgent as a node.
2. The planner can translate a high-level task into repo input.
3. The reviewer/gate can handle missing artifacts, format mismatch, and runtime errors.
4. For tasks that do not need SpatialAgent, the compiler does not force a call to it.

---

## 12. Experiment S3: BioDiscoveryAgent official tasks

### 12.1 Goal

Test on BioDiscoveryAgent’s native closed-loop perturbation design tasks:

- BioDiscoveryAgent as a sandbox backend.
- Whether AgentCo-Op can select literature review / critique / pathway tools based on task and budget.
- Whether reviewer / gate improves failed runs and format issues.

### 12.2 Data

Datasets listed in the BioDiscoveryAgent README include:

- IFNG
- IL2
- Carnevale22_Adenosine
- Scharenberg22
- Sanchez21_down

If the paper or a newer repo version adds a sixth dataset, record the version and report it separately from the original experiments.

### 12.3 Methods

| Method | Description |
|---|---|
| Random / BO baseline | Existing baseline from the paper or repo |
| BioDiscoveryAgent base | Official command, without AgentCo-Op planner |
| BioDiscoveryAgent all-tools | lit review + critique + reactome all enabled |
| AC-BioBudgeted | AgentCo-Op selects tools based on budget and dataset profile |
| AC-BioGated | AC-BioBudgeted + failed-run repair / critique gate |
| AC-BioNoCritique | Remove critique to test reviewer value |

### 12.4 Workflow

```text
TaskProfiler
  → PerturbationDesignPlanner
  → BioDiscoveryAgentSandbox
  → GeneListValidator
  → BioEvidenceReviewer
  → MetricsCollector
```

Gates:

- invalid gene symbols → gene validator / mapping repair.
- run crash → environment repair.
- no improvement after round N → enable critique or literature tool.
- budget near limit → disable expensive literature calls.

### 12.5 Metrics

- hit rate per round.
- final hit rate.
- AUC over acquisition rounds.
- number of genes queried.
- cost per round.
- wall time.
- tool usage count.
- run success rate.

### 12.6 Reporting

Report the curve for each dataset, not only the average:

```text
round 1, 2, 3, 4, 5 hit-rate curve
```

Also report:

- model version.
- prompt/tool flags.
- number of repetitions.
- random seeds.
- exact commit.

---

## 13. Experiment S4: Cross-specialist collaboration pilot

### 13.1 Goal

Demonstrate the core idea of “specialized agent collaboration”: SpatialAgent extracts spatial gene programs / cell-type niches from spatial data, and BioDiscoveryAgent uses that context to design perturbation candidates.

### 13.2 Recommended task format

Each task should include:

```json
{
  "spatial_dataset": "...",
  "biological_goal": "Identify perturbations likely to reduce inflammatory macrophage niche activity.",
  "available_prior": "cell type labels / region annotations / marker genes",
  "heldout_screen": "optional perturbation screen ground truth",
  "expected_output": "ranked gene perturbations with evidence"
}
```

Workflow:

```text
SpatialTaskPlanner
  → SpatialAgentSandbox
  → SpatialProgramExtractor
  → PerturbationTaskRewriter
  → BioDiscoveryAgentSandbox
  → CrossModalIntegrator
  → EvidenceAndCausalityReviewer
```

### 13.3 Ground truth options

Ranked from strongest to weakest:

1. **Matched perturbation screen**: CRISPR or Perturb-seq ground truth exists for the same cell type / phenotype.
2. **Retrospective known targets**: literature and databases confirm that gene perturbation affects the target phenotype.
3. **Gene-set enrichment**: predicted genes are enriched in a known pathway / marker program.
4. **Expert review**: domain experts blindly evaluate ranked genes.
5. **No ground truth demo**: demonstrate execution only, not used as a main result.

The main paper experiments should use only options 1 or 2; options 3/4/5 belong in the appendix or demo section.

### 13.4 Candidate datasets

Further investigation and filtering are needed. Consider:

- Spatial transcriptomics datasets with annotated disease niches.
- Paired scRNA/spatial datasets with known pathway perturbations.
- Public CRISPR / Perturb-seq screens in matching cell types.
- BioDiscoveryAgent official perturbation datasets, if corresponding spatial context can be constructed.

### 13.5 Metrics

If screen ground truth exists:

- Precision@K.
- Recall@K.
- NDCG.
- enrichment of positive hits.
- hit rate over acquisition rounds.

If only known targets exist:

- overlap with curated target set.
- pathway enrichment p-value.
- literature evidence coverage.
- expert score.

### 13.6 Baselines

| Baseline | Description |
|---|---|
| Spatial-only | SpatialAgent outputs marker genes, used directly as ranked perturbations |
| Bio-only | BioDiscoveryAgent without spatial context |
| Union / intersection heuristic | Manually combine spatial markers and bio candidates |
| AC-Collab | AgentCo-Op compiled collaboration |
| AC-Collab-NoIntegrator | Remove cross-modal integrator |
| AC-Collab-NoReviewer | Remove evidence reviewer |

### 13.7 Expected risks

- Spatial marker genes are not necessarily causal perturbation targets.
- BioDiscoveryAgent datasets may not include spatial context.
- Literature priors may bias predictions toward well-known genes.
- Without matched ground truth, results are only a qualitative demo.

---

## 14. Statistical analysis

### 14.1 Confidence intervals

For each dataset:

- Bootstrap 95% CI over tasks.
- For stochastic runs, first average over runs for the same task, then bootstrap tasks.

### 14.2 Paired tests

Use paired bootstrap or McNemar-style comparison for AgentCo-Op vs baseline:

- For solve/pass/fail metrics, use paired accuracy difference.
- For F1, use paired F1 difference.

### 14.3 Multiple comparisons

Do not overemphasize small improvements. Restrict the main comparisons to:

1. AC-Gated vs AC-Compiled.
2. AC-Gated vs best non-search baseline.
3. AC-Gated vs AFlow / ADAS reported or reproduced.
4. AC-Compiled vs AC-ForcedMulti.

---

## 15. Reproducibility checklist

Save the following for every experimental run:

- git commit of AgentCo-Op.
- external repo commits.
- dataset file hashes.
- model provider and model version.
- prompt templates.
- skill card versions.
- workflow blueprint JSON.
- raw model outputs.
- tool stdout/stderr.
- sandbox image digest.
- cost and token logs.
- final predictions CSV.
- evaluator version.

Directory:

```text
runs/{experiment_name}/{timestamp}/
  config.yaml
  git_state.txt
  data_hashes.json
  predictions.csv
  metrics.json
  traces/
  blueprints/
  artifacts/
  analysis.ipynb
```

---

## 16. Common pitfalls

### 16.1 AFlow data-alignment pitfalls

- “seed 42 + 1:4 split” can still produce different samples if the dataset version differs.
- The high-variance validation subset requires reproducing blank-template runs; the exact model outputs affect variance.
- MATH category names may differ across versions, so the total count must be checked against 617.
- MBPP sanitized / full split differences affect pass@1.

### 16.2 Evaluation pitfalls

- Incorrect final-number extraction for GSM8K can create false failures.
- MATH LaTeX normalization is difficult; use the official or a mature evaluator.
- HumanEval/MBPP evaluation executes generated code and has security risks.
- DROP has complex multi-answer formats and cannot be evaluated with simple exact match.

### 16.3 Multi-agent pitfalls

- Sharing too much context between agents can cause cross-contamination of errors.
- A reviewer can become a “more confident hallucination.”
- Parallel specialists may amplify errors on evidence-sensitive QA.
- Repair loops may overfit public tests.
- Cost growth may exceed performance gains.

### 16.4 Specialized-agent pitfalls

- BioDiscoveryAgent / SpatialAgent may have heavy dependencies and fragile installation environments.
- API model names and prices may change.
- The full SpatialBench set may not be publicly downloadable.
- BioDiscoveryAgent and SpatialAgent lack a natural shared ground truth.
- Do not present a cross-repo demo as a strict benchmark.

---

## 17. Recommended paper figures and tables

1. **Architecture figure**: TaskProfile + SkillCompiler + BaseGraph + GatedSubgraph + Backends.
2. **Topology ladder figure**: L0 through L7, showing simplicity-first selection.
3. **AFlow-aligned main table**.
4. **Cost-performance Pareto**.
5. **Route distribution heatmap**.
6. **Gate rescue / harm analysis**.
7. **Sandbox repo wrapping pipeline**.
8. **SpatialAgent / BioDiscoveryAgent collaboration workflow**.
9. **Failure taxonomy bar chart**.

---

## 18. Minimal deliverable experimental package

The first paper / project version should include at least:

1. Debug + full runs on the six AFlow-aligned benchmarks.
2. Three core ablations: AC-Compiled, AC-Gated, and AC-ForcedMulti.
3. Route distribution and gate rescue analysis.
4. Proof of sandbox execution on HumanEval / MBPP.
5. At least one successful repo wrapper run for SpatialAgent or BioDiscoveryAgent.
6. Smoke tests passed for both repos.
7. Cross-specialist collaboration pilot as a qualitative demo.

---

## 19. References

- AFlow paper: https://arxiv.org/abs/2410.10762
- AFlow code: https://github.com/FoundationAgents/AFlow
- ADAS paper: https://arxiv.org/abs/2408.08435
- ADAS code: https://github.com/ShengranHu/ADAS
- HotpotQA: https://hotpotqa.github.io/
- DROP: https://allennlp.org/drop
- HumanEval: https://github.com/openai/human-eval
- MBPP: https://github.com/google-research/google-research/tree/master/mbpp
- GSM8K: https://github.com/openai/grade-school-math
- MATH dataset: https://github.com/hendrycks/math
- BioDiscoveryAgent paper: https://arxiv.org/abs/2405.17631
- BioDiscoveryAgent code: https://github.com/snap-stanford/BioDiscoveryAgent
- SpatialAgent code: https://github.com/Genentech/SpatialAgent
- SpatialBench code: https://github.com/Genentech/SpatialBench
