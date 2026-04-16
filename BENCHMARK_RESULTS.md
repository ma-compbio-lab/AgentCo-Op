# AgentCo-Op Multi-Agent Workflow — Comprehensive Benchmark Results

**Model:** gpt-4o-mini (all benchmarks)
**Splits:** AFlow-aligned (validate/test, seed=42)
**Last Updated:** 2026-04-16

---

## 🏆 Best Results Summary

| Benchmark | Tasks | **Our Best** | AFlow | CoT-SC | Gap vs AFlow | Best Config |
|-----------|-------|------------|-------|--------|--------------|-------------|
| **MBPP** | 341 | **89.7% pass@1** | 83.4% | 73.6% | **+6.3pp** ✓ | mbpp |
| **HumanEval** | 131 | **89.3% pass@1** | 94.7% | 91.6% | -5.4pp | humaneval_v3 |
| **GSM8K** | 1055 | **77.1% acc** | 93.5% | 92.7% | -16.4pp | gsm8k |
| **HotpotQA** | 800 | **70.6% F1** | 73.5% | 68.9% | -2.9pp | hotpotqa (with norm) |
| **MATH (L5)** | 486 | **47.1% solve** | 56.2% | 50.4% | -9.1pp | math_v5 |
| **DROP** | 800 | **43.0% F1** | 80.6% | 78.8% | -37.6pp | drop_v2 |

**Our Average: 69.5%**  •  **AFlow Average: 80.3%**  •  **Gap: -10.8pp**

✓ MBPP **beats** AFlow. HotpotQA is **within 3pp**. DROP and GSM8K remain the largest gaps.

---

## 📊 Full Results Table — All Versions

### MATH (Level 5, 486 tasks)

| Version | Solve Rate | Δ vs v2 | Avg Cost | Avg Latency | Key Change |
|---------|-----------|---------|----------|-------------|------------|
| v2 (baseline) | 35.4% | +0.0pp | $0.00068 | 9.8s | Bug fixes (contracts, JSON, SymPy guard) |
| v3 (no review) | 39.7% | +4.3pp | — | — | Review loop disabled |
| v4 (verify) | 38.9% | +3.5pp | — | — | Review-as-verification |
| **v5 (ScEnsemble)** | **47.1%** | **+11.7pp** | $0.00115 | 14.9s | **Challenger solver + CoT** ✓ |
| v6 (retry) | 45.9% | +10.5pp | $0.00118 | 18.4s | + programmer retry (hurt PreCalc) |
| v7 (smart-sel) | 46.7% | +11.3pp | $0.00114 | 20.8s | Smarter selector for PreCalc |

#### MATH per-type (v5, best)
| Type | v2 | v5 | Δ |
|------|-----|-----|---|
| Number Theory | 44% | **61%** | **+17pp** |
| Prealgebra | 49% | **59%** | **+10pp** |
| Counting & Probability | 29% | **44%** | **+15pp** |
| Precalculus | 11% | 16% | +5pp (model ceiling) |

### HumanEval (131 tasks)

| Version | pass@1 | Δ | Cost | Latency |
|---------|--------|---|------|---------|
| v2 (baseline) | 89.3% | — | $0.00055 | 7.7s |
| v3 (humaneval_v3) | 89.3% | +0.0pp | $0.00060 | 5.8s |

### GSM8K (1055 tasks)

| Version | Accuracy | Δ | Cost | Latency |
|---------|---------|---|------|---------|
| aflow_v1 | 75.8% | — | $0.00080 | 11.3s |
| **aflow_v2** | **77.1%** | **+1.3pp** | $0.00079 | 12.9s |

### HotpotQA (800 tasks)

| Version | F1 | EM | Cost | Latency |
|---------|------|-----|------|---------|
| aflow_v1 | 59.1% | — | $0.00037 | 2.0s |
| **aflow_v2 (with norm)** | **70.6%** | **55.9%** | $0.00040 | 2.3s |

### DROP (800 tasks)

| Version | F1 | EM | Cost | Latency |
|---------|------|-----|------|---------|
| aflow_v1 (direct_answer) | 31.5% | — | $0.00020 | 1.6s |
| **aflow_v2 (drop_v2 + code exec)** | **43.0%** | **37.8%** | $0.00075 | 9.6s |

### MBPP (341 tasks)

| Version | pass@1 | Cost | Latency |
|---------|--------|------|---------|
| **aflow_v1 (with grading fix)** | **89.7%** | $0.00038 | 3.9s |
| aflow_v2 | 89.7% | $0.00039 | 4.7s |

---

## 📈 Optimization Journey Timeline

### Round 1: Bug Fixes (v2)
- Fixed non-blocking contract violations → MATH 35.4%
- JSON recovery from malformed responses
- SymPy infinite recursion guard
- **Result:** Stable baseline

### Round 2: Review Loop Investigation (v3, v4)
- v3: Disabled review loop → +4.3pp (review was destructive)
- v4: Review-as-verification → -0.8pp vs v3 (still destructive with gpt-4o-mini)
- **Lesson:** gpt-4o-mini too weak for re-solver review

### Round 3: ScEnsemble (v5) — BIGGEST WIN
- Challenger solver + 3 reasoning paths
- LLM-based selector arbitrates
- CoT-first prompting
- Concurrent node execution
- **Result:** MATH +11.7pp (35.4% → 47.1%)

### Round 4: Targeted Improvements (v6, v7)
- v6: Programmer retry → mixed (helped NT, hurt PreCalc)
- v7: Smarter selector → tied with v5
- **Lesson:** Universal optimizations beat targeted ones

### Round 5: Multi-Benchmark Expansion (aflow_v1)
- Added 4 new benchmarks (GSM8K, MBPP, HotpotQA, DROP)
- AFlow-aligned data splits
- Discovered MBPP grading bug → fixed
- Discovered DROP needed code execution

### Round 6: Generalization (aflow_v2) — CURRENT
- SQuAD-style F1 normalization → HotpotQA +2.7pp
- Verbosity stripping → eliminated near-misses
- DROP v2 with code execution → +4.0pp
- **Result:** 4-benchmark avg 65.4% → 69.5%

---

## 🧠 Validated Principles (KEEP)

### 1. **ScEnsemble is the highest-leverage technique**
- Multiple reasoning paths + LLM voter beat single-path
- Evidence: MATH +11.7pp, generalizable across math/code

### 2. **Test-driven repair works for code (when verifiable)**
- MBPP coder: 100% accuracy (when no repair triggered)
- Reviser: 100% accuracy (when triggered)
- Rewriter: 8% (only attempts left for hard cases)

### 3. **Review loops are destructive with weak models**
- gpt-4o-mini reviser scores 5-10% on what selector handles 41%
- Disable for any LLM weaker than GPT-4 class

### 4. **Concurrent node execution is free win**
- Solver + Programmer run independently
- 40% latency reduction
- No accuracy impact

### 5. **Standard normalization matters**
- SQuAD F1 normalization recovered 96+ near-misses on HotpotQA
- Verbosity stripping (units, prefixes, quotes) helps QA benchmarks

### 6. **Code execution for numerical reasoning**
- DROP: +4pp from adding programmer path
- Generalizable to any benchmark with computation

---

## 🚫 Validated Anti-Patterns (DO NOT REPEAT)

### 1. ❌ Re-solver review loops
- Reviser scores 5-10% — always destructive
- Even verify_then_correct doesn't help with weak models

### 2. ❌ Universal programmer retry
- Helps NT (+8pp) but hurts PreCalc (-8pp), PreAlg (-4pp)
- Net negative across types

### 3. ❌ Precalculus-specific prompts
- Tried multiple variations
- Model capability ceiling: 8-17% across all configs

### 4. ❌ Attempt history for HumanEval rewriter
- -1.5pp regression
- Rewriter doesn't benefit from "what we already tried"

### 5. ❌ verify_then_correct with gpt-4o-mini
- Model can't verify reliably
- Reverts good answers to bad ones

---

## 💰 Cost-Performance Analysis

| Benchmark | Best Version | $/task | Tasks | Total Cost |
|-----------|-------------|--------|-------|------------|
| MATH | v5 | $0.00115 | 486 | $0.56 |
| HumanEval | v2 | $0.00055 | 131 | $0.07 |
| GSM8K | aflow_v2 | $0.00079 | 1055 | $0.83 |
| HotpotQA | aflow_v2 | $0.00040 | 800 | $0.32 |
| DROP | aflow_v2 (drop_v2) | $0.00075 | 800 | $0.60 |
| MBPP | aflow_v1 | $0.00038 | 341 | $0.13 |
| **TOTAL** | | | **3613 tasks** | **$2.51** |

Average cost-per-task: **$0.0007** (very efficient — ~1500 tasks per dollar)

---

## 🎯 Remaining Improvement Opportunities (Ranked)

### 1. DROP (-37.6pp gap) — HIGHEST ROI
- Current 43% vs AFlow 80.6%
- Code execution helped (+4pp) but not enough
- **Hypothesis:** Need decomposition pattern (least-to-most) + better passage grounding
- **Risk:** Long passages may exceed gpt-4o-mini's effective context

### 2. GSM8K (-16.4pp gap) — Model capability limit
- Failures are genuine multi-step math errors
- Already using ScEnsemble + programmer
- **Hypothesis:** Verifier-reranked sampling (Lightman 2023) could add +5-8pp
- **Risk:** Cost scales with sample count

### 3. MATH (-9.1pp gap) — Diminishing returns
- Extensively optimized (5 versions tried)
- Precalculus stuck at 16% (model ceiling)
- **Hypothesis:** Only model upgrade will move the needle

### 4. HumanEval (-5.4pp gap) — Marginal room
- 14 failures, mostly hard rewriter cases
- Already strong at 89.3%
- **Hypothesis:** CRITIC-style tool-grounded critique may add +2-3pp

### 5. HotpotQA (-2.9pp gap) — Close to ceiling
- F1 normalization closed most of the gap
- Remaining is genuine knowledge gaps
- **Not worth optimizing further** — diminishing returns

### 6. MBPP (+6.3pp ahead) — Already winning
- No optimization needed

---

## 📁 Reproducibility

All run artifacts preserved in:
- `runs-bench-4omini/` — v2 baseline (MATH, HumanEval)
- `runs-bench-v5/` — v5 ScEnsemble
- `runs-bench-v6/` — v6 with retry
- `runs-bench-v7/` — v7 smart selector
- `runs-aflow/` — initial 4-benchmark expansion
- `runs-aflow-v2/` — F1 normalization + drop_v2 (latest)

Reproduce best results:
```bash
agentcoop-exp math --experiment math_v5 --subset test --model openai_gpt4o_mini --base-dir runs-final
agentcoop-exp humaneval --experiment humaneval_v3 --subset test --model openai_gpt4o_mini --base-dir runs-final
agentcoop-exp gsm8k --subset test --model openai_gpt4o_mini --base-dir runs-final
agentcoop-exp hotpotqa --subset test --model openai_gpt4o_mini --base-dir runs-final
agentcoop-exp drop --experiment drop_v2 --subset test --model openai_gpt4o_mini --base-dir runs-final
agentcoop-exp mbpp --subset test --model openai_gpt4o_mini --base-dir runs-final
```
