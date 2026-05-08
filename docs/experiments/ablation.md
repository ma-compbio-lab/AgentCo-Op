# AgentCo-Op Ablation Plan

This document defines a simple ablation protocol for AgentCo-Op on the six standard benchmarks: HotpotQA, DROP, HumanEval, MBPP, GSM8K, and MATH. The goal is to isolate whether performance gains come from two core components:

1. **Skills + tools**: task-conditioned compilation using meta-skills, agent-skills, tool cards, and sandbox/tool nodes.
2. **Gate repair**: dynamic gated refinement after execution, including reviewer-triggered repair, rerouting, re-execution, and re-evaluation.

All ablations should use the same dataset splits, evaluator scripts, model settings, and budget accounting as `benchmarks.md`.

---

## 1. Experimental factors

Use a 2 × 2 factorial design.

| Variant | Skills + tools | Gate repair | Purpose |
|---|---:|---:|---|
| **AC-Full** | On | On | Main AgentCo-Op system. |
| **AC-NoGate** | On | Off | Tests whether compiled workflows alone are sufficient. |
| **AC-NoSkillsTools** | Off | On | Tests whether generic multi-agent repair can compensate for missing skill/tool grounding. |
| **AC-Minimal** | Off | Off | Minimal baseline: generic compiled workflow, no skill/tool grounding, no dynamic repair. |

### 1.1 Skills + tools switch

When `skills_tools=on`, AgentCo-Op may use:

- meta-skill cards for topology selection;
- agent-skill cards for domain-specific specialists;
- tool cards for calculators, symbolic checkers, code sandboxes, retrieval tools, and other approved execution nodes;
- task-conditioned workflow templates selected from the skill library.

When `skills_tools=off`:

- disable retrieval from the meta-skill and agent-skill libraries;
- disable tool and sandbox nodes during generation and repair;
- use only a generic router, generic solver/programmer, generic reviewer, and finalizer;
- keep the final benchmark evaluator unchanged.

Important: benchmark evaluators are external to the method and must remain unchanged. For example, HumanEval and MBPP hidden tests are still used by the evaluator, but never exposed to any generation or repair node.

### 1.2 Gate repair switch

When `gate_repair=on`, AgentCo-Op may run local refinement after the base graph output. Examples:

- evidence-missing repair for HotpotQA;
- arithmetic or formatting repair for DROP and GSM8K;
- syntax/runtime/generated-test repair for HumanEval and MBPP;
- symbolic-check or domain-rerouting repair for MATH.

When `gate_repair=off`:

- execute the compiled base graph once;
- do not trigger repair, rerouting, re-execution, or topology modification;
- allow only deterministic final answer formatting if it does not call a new model or tool;
- log what gates would have fired, but do not act on them.

---

## 2. Benchmarks and metrics

Run all four variants on all six benchmarks.

| Benchmark | Metric | Notes |
|---|---|---|
| HotpotQA | Answer F1 | Use the AFlow-aligned 1,000-example setting or exact AFlow processed split. |
| DROP | DROP F1 | Use official DROP normalization and F1. |
| HumanEval | pass@1 | One final completion per problem; no hidden-test leakage. |
| MBPP | pass@1 | Pin sanitized/full version; no hidden-test leakage. |
| GSM8K | Solve rate | Score only the normalized final answer. |
| MATH | Solve rate | Use AFlow-aligned level-5 subset: Combinatorics and Probability, Number Theory, Pre-algebra, and Pre-calculus. |

Recommended run count:

- minimum: one seed for each dataset × variant;
- preferred: three seeds, e.g. `42, 43, 44`;
- total preferred runs: `6 datasets × 4 variants × 3 seeds = 72 runs`.

---

## 3. Configuration files

Create one config per ablation variant.

```yaml
# configs/ablations/ac_full.yaml
method: ac_full
skills_tools: true
gate_repair: true
```

```yaml
# configs/ablations/ac_nogate.yaml
method: ac_nogate
skills_tools: true
gate_repair: false
```

```yaml
# configs/ablations/ac_noskillstools.yaml
method: ac_noskillstools
skills_tools: false
gate_repair: true
```

```yaml
# configs/ablations/ac_minimal.yaml
method: ac_minimal
skills_tools: false
gate_repair: false
```

Keep all other settings identical across variants:

```yaml
model_config: configs/models/aflow_aligned.yaml
data_root: data/aflow_aligned
max_budget_by_dataset: configs/budgets/standard.yaml
same_prompt_budget: true
same_random_seed: true
log_gate_decisions: true
log_tool_calls: true
log_sandbox_runs: true
```

---

## 4. Execution commands

Example single run:

```bash
agentcoop run-benchmark \
  --dataset data/aflow_aligned/gsm8k_test.jsonl \
  --method-config configs/ablations/ac_full.yaml \
  --model-config configs/models/aflow_aligned.yaml \
  --out runs/ablations/gsm8k/ac_full/seed_42 \
  --seed 42

agentcoop evaluate \
  --predictions runs/ablations/gsm8k/ac_full/seed_42/predictions.jsonl \
  --dataset gsm8k \
  --out runs/ablations/gsm8k/ac_full/seed_42/metrics.json
```

Recommended batch script:

```bash
DATASETS=(hotpotqa drop humaneval mbpp gsm8k math)
METHODS=(ac_full ac_nogate ac_noskillstools ac_minimal)
SEEDS=(42 43 44)

for dataset in "${DATASETS[@]}"; do
  for method in "${METHODS[@]}"; do
    for seed in "${SEEDS[@]}"; do
      agentcoop run-benchmark \
        --dataset data/aflow_aligned/${dataset}_test.jsonl \
        --method-config configs/ablations/${method}.yaml \
        --model-config configs/models/aflow_aligned.yaml \
        --out runs/ablations/${dataset}/${method}/seed_${seed} \
        --seed ${seed}

      agentcoop evaluate \
        --predictions runs/ablations/${dataset}/${method}/seed_${seed}/predictions.jsonl \
        --dataset ${dataset} \
        --out runs/ablations/${dataset}/${method}/seed_${seed}/metrics.json
    done
  done
 done
```

---

## 5. Required logs and artifacts

Each run directory should contain:

```text
runs/ablations/{dataset}/{method}/seed_{seed}/
  config.yaml
  predictions.jsonl
  metrics.json
  workflow_blueprint.json
  gate_log.jsonl
  tool_log.jsonl
  sandbox_log.jsonl
  token_costs.json
  traces/
  artifacts/
```

For `gate_repair=off`, still write `gate_log.jsonl` with `would_trigger=true/false` so that later analysis can estimate how often repair would have been used.

For `skills_tools=off`, still write `tool_log.jsonl`, but it should contain zero executed tool calls. Any accidental tool call should mark the run as invalid.

---

## 6. Analysis

### 6.1 Main result table

| Dataset | AC-Full | AC-NoGate | AC-NoSkillsTools | AC-Minimal |
|---|---:|---:|---:|---:|
| HotpotQA F1 | | | | |
| DROP F1 | | | | |
| HumanEval pass@1 | | | | |
| MBPP pass@1 | | | | |
| GSM8K solve | | | | |
| MATH solve | | | | |
| Average normalized score | | | | |

Use dataset-native metrics in the first six rows. For the average, normalize all metrics to `[0, 1]`.

### 6.2 Component effect table

Report the marginal effect of each component.

| Dataset | Skills/tools effect with gates | Skills/tools effect without gates | Gate effect with skills/tools | Gate effect without skills/tools | Interaction |
|---|---:|---:|---:|---:|---:|
| HotpotQA | | | | | |
| DROP | | | | | |
| HumanEval | | | | | |
| MBPP | | | | | |
| GSM8K | | | | | |
| MATH | | | | | |

Definitions:

```text
Skills/tools effect with gates    = AC-Full - AC-NoSkillsTools
Skills/tools effect without gates = AC-NoGate - AC-Minimal
Gate effect with skills/tools     = AC-Full - AC-NoGate
Gate effect without skills/tools  = AC-NoSkillsTools - AC-Minimal
Interaction                       = AC-Full - AC-NoGate - AC-NoSkillsTools + AC-Minimal
```

### 6.3 Cost and latency table

| Dataset | Method | Score | LLM calls | Tool calls | Sandbox runs | Cost USD | Latency s |
|---|---|---:|---:|---:|---:|---:|---:|
| HotpotQA | AC-Full | | | | | | |
| HotpotQA | AC-NoGate | | | | | | |
| HotpotQA | AC-NoSkillsTools | | | | | | |
| HotpotQA | AC-Minimal | | | | | | |

Repeat for all six datasets.

### 6.4 Gate rescue analysis

For each dataset, compare `AC-Full` and `AC-NoGate` on the same examples.

| Dataset | Gate trigger count | Rescue count | Harm count | Rescue rate | Harm rate | Avg cost added |
|---|---:|---:|---:|---:|---:|---:|
| HotpotQA | | | | | | |
| DROP | | | | | | |
| HumanEval | | | | | | |
| MBPP | | | | | | |
| GSM8K | | | | | | |
| MATH | | | | | | |

Definitions:

- **Rescue**: `AC-NoGate` is wrong, but `AC-Full` is correct.
- **Harm**: `AC-NoGate` is correct, but `AC-Full` is wrong.
- **Rescue rate**: `rescue_count / gate_trigger_count`.
- **Harm rate**: `harm_count / gate_trigger_count`.

---

## 7. Expected interpretation

The ablation should support three claims if AgentCo-Op works as intended:

1. **Skills + tools improve task-conditioned compilation.** `AC-Full` should outperform `AC-NoSkillsTools`, especially on HumanEval, MBPP, MATH, and DROP where tools, sandboxes, symbolic checks, or domain-specific reasoning are useful.
2. **Gate repair improves robustness but should not always fire.** `AC-Full` should outperform `AC-NoGate`, but gate trigger rates should stay task-dependent rather than uniformly high.
3. **The system should avoid unnecessary complexity.** On simpler examples, `AC-NoGate` may be close to `AC-Full`; this is acceptable and supports the simplicity-first design principle.

Negative results are also informative:

- If `AC-NoSkillsTools` is close to `AC-Full`, the skill/tool library is not yet adding enough value.
- If `AC-NoGate` is close to `AC-Full`, the base compiler may already be strong or the gate triggers are too conservative.
- If `AC-Full` is worse than `AC-NoGate`, gate repair may be over-refining or causing reviewer-induced errors.
- If `AC-Minimal` is competitive on simple benchmarks, report this honestly and use it as evidence that AgentCo-Op should compile simpler workflows when possible.

---

## 8. Reporting checklist

The final ablation section should include:

- one main 6-benchmark performance table;
- one component effect table;
- one cost/latency table;
- one gate rescue/harm table;
- a short per-dataset interpretation;
- at least 20 manually inspected examples where gate repair changed the answer;
- a statement that all variants used identical data, evaluator, model, and budget settings except for the two ablated components.
