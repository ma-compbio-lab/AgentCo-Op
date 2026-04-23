# AgentCo-Op Standard Benchmarks

This document gives the detailed protocol for the AFlow-aligned standard benchmark track. The goal is not merely to report a single accuracy number, but to show whether AgentCo-Op compiles appropriately simple or complex workflows for each task type.

## 1. Benchmark scope

AFlow evaluates six datasets: HumanEval, MBPP, GSM8K, MATH, HotpotQA, and DROP. It uses F1 for HotpotQA and DROP, pass@1 for HumanEval and MBPP, and solve rate for GSM8K and MATH. AFlow also reports cost-performance tradeoffs, which AgentCo-Op should reproduce.

| Dataset | Task type | AFlow-aligned data setting | Main metric |
|---|---|---|---|
| HotpotQA | multi-hop question answering | randomly selected 1,000 examples | answer token F1 |
| DROP | reading comprehension with discrete reasoning | randomly selected 1,000 examples | DROP F1 |
| HumanEval | Python code generation | full dataset | pass@1 |
| MBPP | Python code generation | full dataset, version pinned | pass@1 |
| GSM8K | grade-school math | full dataset, version pinned | solve rate |
| MATH | competition math | 617 level-5 examples from Combinatorics and Probability, Number Theory, Pre-algebra, and Pre-calculus | solve rate |

## 2. Data split protocol

### 2.1 Strict AFlow reproduction mode

Use this mode for the main table.

1. Clone and pin AFlow.

```bash
git clone https://github.com/FoundationAgents/AFlow.git external/AFlow
cd external/AFlow
git rev-parse HEAD > ../../configs/aflow_commit.txt
```

2. Download or locate the processed data used by AFlow.

```bash
python data/download_data.py
```

3. Convert AFlow data into the AgentCo-Op unified task format.

```bash
agentcoop data import-aflow \
  --aflow-dir external/AFlow \
  --out data/aflow_aligned
```

4. Save hashes for every split file.

```bash
agentcoop data hash data/aflow_aligned > runs/data_hashes_aflow_aligned.json
```

The paper should report the AFlow commit, dataset file hashes, split file hashes, and evaluator commit.

### 2.2 Clean-room mode

Use clean-room mode only when exact AFlow processed data cannot be recovered. The clean-room procedure should match the AFlow description:

1. Use random seed 42.
2. Partition each selected dataset into 20% validation and 80% test.
3. Run a blank template five times on the validation set.
4. Select the high-variance validation subset.
5. Evaluate each candidate workflow five times on validation and report mean and standard deviation.
6. Evaluate final methods on the test set three times when stochasticity remains.

Clean-room mode must be labelled clearly because it may not match the exact AFlow samples.

## 3. Unified task format

All datasets should be converted to JSONL records with the following minimal schema:

```json
{
  "task_id": "dataset_unique_id",
  "dataset": "gsm8k",
  "split": "test",
  "input": {
    "question": "...",
    "context": null,
    "prompt": "..."
  },
  "reference": {
    "answer": "...",
    "tests": null
  },
  "metadata": {
    "source": "...",
    "category": "...",
    "difficulty": "..."
  }
}
```

Code-generation tasks may store tests under `reference.tests`, but official hidden tests must be used only by the evaluator, not by the generation or repair nodes.

## 4. Dataset-specific processing

### 4.1 HotpotQA

Recommended source: AFlow processed data first; otherwise official HotpotQA or Hugging Face `hotpot_qa`.

Processing rules:

- Match AFlow's selected setting and sample count.
- Input should include the question and context used by the benchmark.
- The final answer is evaluated using token-level F1.
- Supporting facts may be used for diagnostic evidence scoring but should not replace the answer F1 metric.

AgentCo-Op task fields:

```json
{
  "question": "...",
  "context": [["title", ["sentence 1", "sentence 2"]]],
  "answer": "...",
  "supporting_facts": [["title", 0]]
}
```

### 4.2 DROP

Recommended source: AFlow processed data first; otherwise official AllenAI DROP or Hugging Face `drop`.

Processing rules:

- Prompt = passage + question.
- Reference answers may include numbers, dates, spans, and multi-span annotations.
- Use the official DROP normalization and F1 evaluator.
- The finalizer must normalize numbers, commas, units, dates, and spans.

### 4.3 HumanEval

Recommended source: OpenAI HumanEval package or AFlow processed data.

Processing rules:

- Prompt = function signature and docstring.
- The system generates exactly one final code completion for pass@1.
- Internal repair is allowed only before final submission and must not use official hidden tests.
- Syntax checks, import checks, model-generated tests, and public examples are allowed.
- Evaluation must run in a locked sandbox with network disabled.

### 4.4 MBPP

Recommended source: Google Research MBPP, Hugging Face `mbpp`, or AFlow processed data.

Processing rules:

- Pin whether the sanitized or full MBPP version is used.
- Prompt should include problem statement and public examples/tests when the benchmark provides them.
- The final answer is a single Python program or function implementation.
- The official test set is used only by the evaluator.

### 4.5 GSM8K

Recommended source: OpenAI grade-school-math or AFlow processed data.

Processing rules:

- Extract the gold final answer after `####`.
- Normalize commas, decimal points, negative signs, and simple fractions.
- Metric = exact final-answer solve rate after normalization.
- Keep the full reasoning trace for diagnostics, but score only the final answer.

### 4.6 MATH

Recommended source: official MATH dataset or AFlow processed data.

Processing rules:

- Use only difficulty level 5.
- Use four categories: Combinatorics and Probability, Number Theory, Pre-algebra, and Pre-calculus.
- Confirm that the selected subset contains 617 examples.
- Extract LaTeX `\boxed{...}` answers, fractions, intervals, sets, and equations carefully.
- Use a symbolic or canonical answer normalizer when possible.

## 5. AgentCo-Op benchmark workflows

### 5.1 HotpotQA workflow

Recommended compiled graph:

```text
TaskProfiler
  -> QueryPlanner
  -> ContextSelector
  -> EvidenceSelector
  -> Answerer
  -> EvidenceReviewer
  -> Finalizer
```

Gates:

- `evidence_missing`: retrieve or select additional context.
- `answer_unsupported`: rewrite answer using only selected evidence.
- `answer_format_invalid`: run finalizer only.
- `high_disagreement`: spawn a second answerer and compare evidence.

Simplicity rule: if the question is answerable from one short context span, skip parallel answerers.

### 5.2 DROP workflow

```text
TaskProfiler
  -> PassageReader
  -> AnswerTypeClassifier
  -> SpanOrNumberExtractor
  -> NumericReasoner
  -> DROPFormatter
  -> FormatReviewer
```

Gates:

- `numeric_inconsistency`: recompute arithmetic.
- `answer_type_mismatch`: call formatter only.
- `multi_span_conflict`: ask reviewer to select minimal supporting spans.

### 5.3 HumanEval and MBPP workflow

```text
TaskProfiler
  -> Programmer
  -> SandboxSyntaxCheck
  -> GeneratedTestRunner
  -> RepairPlanner
  -> FinalCodeFormatter
```

Gates:

- `syntax_error`: one targeted repair.
- `runtime_error`: inspect traceback and repair.
- `public_or_generated_test_failure`: repair up to the budget.
- `timeout`: simplify algorithm or add constraints.
- `format_invalid`: formatter only.

Important rule: do not use official hidden tests during generation or repair. Otherwise the benchmark becomes invalid.

### 5.4 GSM8K workflow

```text
TaskProfiler
  -> WordProblemSolver
  -> ArithmeticVerifier
  -> FinalAnswerExtractor
```

Gates:

- `arithmetic_fail`: one repair with explicit calculation.
- `answer_extraction_fail`: formatter only.
- `unusually_complex_problem`: optional second solver.

Simplicity rule: most GSM8K examples should not use large parallel specialist graphs.

### 5.5 MATH workflow

```text
TaskProfiler
  -> MathDomainRouter
  -> DomainSpecialist
  -> IndependentVerifier
  -> FinalAnswerExtractor
```

Optional specialists:

- combinatorics specialist;
- number theory specialist;
- algebra/pre-algebra specialist;
- pre-calculus specialist;
- symbolic checker using Python/SymPy when the answer form permits.

Gates:

- `solver_disagreement`: compare assumptions and recompute.
- `symbolic_check_fail`: run targeted repair.
- `boxed_answer_missing`: formatter only.
- `domain_mismatch`: reroute to a different specialist.

## 6. Baselines

### 6.1 Standard baselines

| Baseline | Description |
|---|---|
| IO | Direct input-output prompting. |
| CoT | Single chain-of-thought style solution with final answer. |
| CoT-SC | Self-consistency with five answers. |
| MultiPersona | Multiple role-conditioned agents followed by aggregation. |
| Self-Refine | Iterative self-feedback and revision, capped at three rounds. |
| MedPrompt | Multiple answers plus voting, following the AFlow baseline family. |
| ADAS | Official or reproduced Meta Agent Search result when available. |
| AFlow | Official AFlow optimized workflow or reproduced AFlow run. |

### 6.2 AgentCo-Op variants

| Variant | Purpose |
|---|---|
| AC-Direct | Single-agent fallback. |
| AC-Compiled | Base graph from task profile and skills, no gated repair. |
| AC-Gated | Main method: compiled graph with local gated refinement. |
| AC-ForcedMulti | Negative control for unnecessary multi-agent complexity. |
| AC-NoMetaSkills | Removes topology-selection skill cards. |
| AC-NoToolSkills | Removes tools/sandboxes where possible. |
| AC-NoReviewer | Removes reviewer and verifier nodes. |
| AC-AFlowImported | Starts from AFlow topology but executes through AgentCo-Op runtime. |
| AC-AFlowImported-Gated | AFlow topology plus AgentCo-Op skills, tools, and gates. |

## 7. Model and budget settings

Run at least two settings if budget permits:

1. **AFlow-aligned executor setting**: match the executor model, temperature, and prompt format used by the AFlow run as closely as possible.
2. **Current practical setting**: use current available models and report date, API version, model ID, and pricing assumptions.

Example per-task budget:

```yaml
budget_by_dataset:
  hotpotqa:
    max_llm_calls: 6
    max_tool_calls: 5
    max_cost_usd: 0.25
  drop:
    max_llm_calls: 5
    max_cost_usd: 0.25
  humaneval:
    max_llm_calls: 6
    max_sandbox_runs: 4
    max_cost_usd: 0.50
  mbpp:
    max_llm_calls: 6
    max_sandbox_runs: 4
    max_cost_usd: 0.50
  gsm8k:
    max_llm_calls: 4
    max_cost_usd: 0.15
  math:
    max_llm_calls: 8
    max_tool_calls: 4
    max_cost_usd: 0.60
```

If the budget is exceeded, return the best current output and set `budget_exceeded=true` in the trace.

## 8. Evaluation commands

Suggested command structure:

```bash
agentcoop run-benchmark \
  --dataset data/aflow_aligned/gsm8k_test.jsonl \
  --method ac-gated \
  --model-config configs/models/aflow_aligned.yaml \
  --skill-library configs/skills \
  --out runs/gsm8k/ac-gated/seed_42

agentcoop evaluate \
  --predictions runs/gsm8k/ac-gated/seed_42/predictions.jsonl \
  --dataset gsm8k \
  --out runs/gsm8k/ac-gated/seed_42/metrics.json
```

## 9. Result tables

### 9.1 Main AFlow-style table

| Method | HotpotQA F1 | DROP F1 | HumanEval pass@1 | MBPP pass@1 | GSM8K solve | MATH solve | Average |
|---|---:|---:|---:|---:|---:|---:|---:|
| IO | | | | | | | |
| CoT | | | | | | | |
| CoT-SC | | | | | | | |
| Self-Refine | | | | | | | |
| MultiPersona | | | | | | | |
| ADAS | | | | | | | |
| AFlow | | | | | | | |
| AC-Compiled | | | | | | | |
| AC-Gated | | | | | | | |

### 9.2 Cost-performance table

| Method | Dataset | Score | Input tokens | Output tokens | LLM calls | Tool calls | Sandbox runs | Cost USD | Latency s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|

### 9.3 Route distribution table

| Dataset | Direct | Single+Tool | Specialist | Parallel | RepairLoop | Repo/Sandbox |
|---|---:|---:|---:|---:|---:|---:|
| HotpotQA | | | | | | |
| DROP | | | | | | |
| HumanEval | | | | | | |
| MBPP | | | | | | |
| GSM8K | | | | | | |
| MATH | | | | | | |

### 9.4 Gate rescue table

| Gate | Trigger count | Rescue count | Rescue rate | Harm count | Average cost added | Average latency added |
|---|---:|---:|---:|---:|---:|---:|
| syntax_error | | | | | | |
| generated_test_failure | | | | | | |
| evidence_missing | | | | | | |
| answer_format_invalid | | | | | | |
| symbolic_check_fail | | | | | | |

A rescue occurs when the no-gate base graph would be wrong and the gated graph becomes correct. A harm occurs when the no-gate base graph would be correct and the gated graph becomes wrong.

## 10. Failure taxonomy

Manually inspect at least 100 failures across datasets and label them as:

- wrong topology;
- missing agent-skill;
- missing meta-skill;
- retrieval miss;
- arithmetic error;
- code syntax/runtime error;
- repair overfit to public/generated tests;
- reviewer false positive;
- reviewer false negative;
- answer extraction error;
- budget exceeded;
- environment/sandbox failure.

## 11. Reproducibility checklist

Each run directory should contain:

```text
runs/{benchmark}/{method}/{timestamp}/
  config.yaml
  git_state.txt
  data_hashes.json
  model_versions.json
  workflow_blueprint.json
  skill_cards/
  prompts/
  predictions.jsonl
  metrics.json
  traces/
  sandbox_logs/
  artifacts/
```

Record all model IDs exactly as returned by the provider. If an API model is silently upgraded or deprecated, the run should be marked as non-comparable to older runs.

## 12. Key pitfalls

1. **AFlow split mismatch**: seed 42 is not enough if the dataset version differs.
2. **MATH subset mismatch**: category names vary across releases; always confirm the final count of 617.
3. **MBPP version mismatch**: sanitized and full MBPP produce different pass@1.
4. **DROP normalization**: simple exact match is invalid for DROP.
5. **HumanEval/MBPP leakage**: do not use official hidden tests inside the generation loop.
6. **Over-refinement**: additional agents can reduce performance while increasing cost.
7. **Reviewer hallucination**: reviewer outputs must be grounded in tests, evidence, or symbolic checks whenever possible.

## 13. References

- AFlow paper: https://arxiv.org/abs/2410.10762
- AFlow code: https://github.com/FoundationAgents/AFlow
- ADAS paper: https://arxiv.org/abs/2408.08435
- HotpotQA: https://hotpotqa.github.io/
- DROP: https://allennlp.org/drop
- HumanEval: https://github.com/openai/human-eval
- MBPP: https://github.com/google-research/google-research/tree/master/mbpp
- GSM8K: https://github.com/openai/grade-school-math
- MATH: https://github.com/hendrycks/math
- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- OpenAI Agents SDK orchestration: https://developers.openai.com/api/docs/guides/agents/orchestration
