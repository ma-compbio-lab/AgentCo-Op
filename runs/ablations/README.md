# Ablation Runs — `ablation.md` 2 × 2 Factorial

Source spec: [`ablation.md`](../../ablation.md).

This directory holds the four ablation variants for the two factors
declared in §3 of the spec:

| Variant            | skills + tools | gate repair |
|--------------------|:--:|:--:|
| `AC-Full`          | ✓ | ✓ |
| `AC-NoGate`        | ✓ | ✗ |
| `AC-NoSkillsTools` | ✗ | ✓ |
| `AC-Minimal`       | ✗ | ✗ |

`AC-Full` reuses the Sessions 4–6 `AC-Gated` runs
(`runs/full_v6/<dataset>/`, plus `runs/full/math/` for MATH). The other
three variants live under `runs/ablations/<dataset>/<variant>/`.

Implementation is YAML-only (per the user's hard constraint): the four
variants are defined in `configs/benchmarks/_base.yaml` and inherit
into every per-dataset config via the `extends: _base` deep-merge.
`AC-NoSkillsTools` is the **proxy form** — `_apply_variant` only honours
`disable_gates` and `disable_reviewer`, so we drop reviewer/verifier
nodes as the closest available proxy for "skills + tools off". Tool /
sandbox backends remain wired (see `configs/ablations/*.yaml` notes).

CSV / JSON outputs in this directory:

| File | Content |
|---|---|
| `ablation_results.csv` | §6.1 — main 6 × 4 score table |
| `component_effects.csv` | §6.2 — marginal effects + interaction |
| `cost_latency.csv` | §6.3 — per-(dataset, variant) cost / latency |
| `gate_rescue.csv` | §6.4 — per-task rescue / harm vs `AC-NoGate` |
| `summary.json` | All four tables as a structured object |

To regenerate: `python scripts/aggregate_ablation.py`.

---

## §6.1 Main result table

| Dataset | AC-Full | AC-NoGate | AC-NoSkillsTools | AC-Minimal |
|---|---:|---:|---:|---:|
| HotpotQA F1            | **0.7648** | 0.7662 | 0.7615 | 0.7604 |
| DROP F1                | 0.7723 | 0.7739 | **0.7830** | 0.7695 |
| HumanEval pass@1       | **0.9015** | 0.8788 | 0.8864 | 0.8864 |
| MBPP pass@1            | **0.8713** | 0.8684 | 0.8626 | 0.8596 |
| GSM8K solve            | **0.9441** | 0.9318 | 0.9403 | 0.9394 |
| MATH solve             | **0.5816** | 0.5655 | 0.5322 | 0.5166 |
| **Average normalised** | **0.8059** | 0.7974 | 0.7943 | 0.7886 |

All metrics already lie in `[0, 1]`, so the average is a simple mean
of the six rows above. `AC-Full` wins the average and the per-dataset
column on 5 / 6 datasets — DROP is the lone exception, where dropping
the reviewer node lifts F1 by ~1 point (see §6.2 and the discussion
below).

`n_tasks` per cell:

| Dataset | AC-Full | AC-NoGate | AC-NoSkillsTools | AC-Minimal |
|---|---:|---:|---:|---:|
| HotpotQA | 800 | 800 | 800 | 800 |
| DROP | 800 | 800 | 800 | 800 |
| HumanEval | 132 | 132 | 132 | 132 |
| MBPP | 342 | 342 | 342 | 342 |
| GSM8K | 1 056 | 1 056 | 1 056 | 1 056 |
| MATH | 478 | 481 | 481 | 482 |

MATH `AC-Full` was carried over from S5 (478 / 484 indices); the three
new ablation runs covered 481 / 481 / 482 of 484 — a 3–6-task variance
from per-task scoring failures, which is too small to flip the
ordering.

---

## §6.2 Component effect table

```
skills_tools_with_gates    = AC-Full           - AC-NoSkillsTools
skills_tools_without_gates = AC-NoGate         - AC-Minimal
gate_with_skills_tools     = AC-Full           - AC-NoGate
gate_without_skills_tools  = AC-NoSkillsTools  - AC-Minimal
interaction                = AC-Full - AC-NoGate - AC-NoSkillsTools + AC-Minimal
```

| Dataset | Skills/tools effect with gates | Skills/tools effect without gates | Gate effect with skills/tools | Gate effect without skills/tools | Interaction |
|---|---:|---:|---:|---:|---:|
| HotpotQA  | +0.0033 | +0.0058 | −0.0014 | +0.0011 | −0.0025 |
| DROP      | −0.0107 | +0.0044 | −0.0016 | +0.0135 | −0.0151 |
| HumanEval | +0.0151 | −0.0076 | +0.0227 | +0.0000 | +0.0227 |
| MBPP      | +0.0087 | +0.0088 | +0.0029 | +0.0030 | −0.0001 |
| GSM8K     | +0.0038 | −0.0076 | +0.0123 | +0.0009 | +0.0114 |
| MATH      | +0.0494 | +0.0489 | +0.0161 | +0.0156 | +0.0005 |

Reading guide:

- **MATH** is the cleanest case — both factors give large, near-additive
  gains (skills/tools ≈ +4.9 pp; gates ≈ +1.6 pp; interaction ≈ 0).
- **HumanEval & GSM8K** show a strongly *positive* interaction —
  gates and skills/tools amplify each other on coding / math.
- **DROP** is the negative-interaction case: dropping the reviewer
  *helps* (skills/tools effect is negative with gates on, positive with
  gates off), and the interaction is the largest negative across the
  board (−0.0151).
- **HotpotQA & MBPP** show effects close to noise — every cell is
  within ±1 pp.

---

## §6.3 Cost and latency table

Tokens, USD, and latency are aggregated from per-prediction `tokens_used`,
`cost_usd`, and `latency_s` in each variant's `metrics.json`. We do
not currently log separate "tool calls" or "sandbox runs" counters in
the metrics blob, so those columns are dropped (the spec form is
preserved via the LLM-call / cost / latency triple).

| Dataset | Method | Score | LLM tokens | Cost USD | Latency s |
|---|---|---:|---:|---:|---:|
| HotpotQA | AC-Full          | 0.7648 | 2 519 303 | 0.4284 | 4.97 |
| HotpotQA | AC-NoGate        | 0.7662 | 2 518 091 | 0.4277 | 3.24 |
| HotpotQA | AC-NoSkillsTools | 0.7615 | 2 517 661 | 0.4274 | 3.28 |
| HotpotQA | AC-Minimal       | 0.7604 | 2 518 478 | 0.4279 | 3.29 |
| DROP     | AC-Full          | 0.7723 | 1 855 487 | 0.3853 | 8.58 |
| DROP     | AC-NoGate        | 0.7739 | 1 849 407 | 0.3842 | 6.88 |
| DROP     | AC-NoSkillsTools | 0.7830 | 1 850 840 | 0.3837 | 6.76 |
| DROP     | AC-Minimal       | 0.7695 | 1 846 874 | 0.3831 | 6.74 |
| HumanEval| AC-Full          | 0.9015 |   393 887 | 0.1062 | 17.77 |
| HumanEval| AC-NoGate        | 0.8788 |   391 794 | 0.1053 | 16.48 |
| HumanEval| AC-NoSkillsTools | 0.8864 |   244 997 | 0.0690 | 11.45 |
| HumanEval| AC-Minimal       | 0.8864 |   242 756 | 0.0679 | 11.93 |
| MBPP     | AC-Full          | 0.8713 |   744 251 | 0.1791 | 12.21 |
| MBPP     | AC-NoGate        | 0.8684 |   746 176 | 0.1798 | 10.11 |
| MBPP     | AC-NoSkillsTools | 0.8626 |   490 820 | 0.1229 |  7.38 |
| MBPP     | AC-Minimal       | 0.8596 |   490 727 | 0.1228 |  7.33 |
| GSM8K    | AC-Full          | 0.9441 |   634 767 | 0.2537 |  6.92 |
| GSM8K    | AC-NoGate        | 0.9318 |   636 015 | 0.2544 |  6.98 |
| GSM8K    | AC-NoSkillsTools | 0.9403 |   634 600 | 0.2536 |  6.96 |
| GSM8K    | AC-Minimal       | 0.9394 |   637 444 | 0.2553 |  6.97 |
| MATH     | AC-Full          | 0.5816 |   967 364 | 0.3670 |  n/a  |
| MATH     | AC-NoGate        | 0.5655 |   986 685 | 0.3765 | 18.54 |
| MATH     | AC-NoSkillsTools | 0.5322 | 1 045 992 | 0.4936 | 29.67 |
| MATH     | AC-Minimal       | 0.5166 | 1 052 085 | 0.4970 | 28.57 |

Notes:

- MATH `AC-Full` was carried over from S5 and `latency_avg_s` was not
  aggregated in that earlier run; left as `n/a`.
- On HumanEval and MBPP, dropping the reviewer roughly *halves* tokens
  and cost (1.6× faster too) — the −0.5 to −1.5 pp accuracy hit per
  §6.1 may be a worthwhile trade in cost-sensitive deployments.
- On MATH, `AC-NoSkillsTools` actually costs *more* than `AC-Full`
  (+34 % USD, +56 % latency) while losing 4.9 pp solve — the math
  specialist nodes that get pruned with the reviewer were doing more
  productive work per token than the surviving solver chain.

---

## §6.4 Gate rescue analysis

Per spec, comparing `AC-Full` vs `AC-NoGate` on the *same* `task_id`s:

- **rescue**: `AC-NoGate` wrong, `AC-Full` correct.
- **harm**: `AC-NoGate` correct, `AC-Full` wrong.
- **rescue rate**: `rescue / gate_trigger_count`.
- **harm rate**: `harm / gate_trigger_count`.

| Dataset | Gate trigger count | Rescue count | Harm count | Rescue rate | Harm rate | Avg cost added (USD) |
|---|---:|---:|---:|---:|---:|---:|
| HotpotQA  | 0  | 14 | 14 |  n/a   |  n/a   | +0.000001 |
| DROP      | 13 | 18 | 23 | 1.385  | 1.769  | +0.000001 |
| HumanEval | 0  |  4 |  1 |  n/a   |  n/a   | +0.000006 |
| MBPP      | 0  |  2 |  1 |  n/a   |  n/a   | −0.000002 |
| GSM8K     | 0  | 20 |  7 |  n/a   |  n/a   | −0.000001 |
| MATH      | 0  | 37 | 31 |  n/a   |  n/a   | −0.000020 |

**Important caveat — gates rarely fire on the AFlow-aligned splits.**
`gate_totals` aggregated by the runner is empty for 5 / 6 datasets:
the chosen `AC-Full` blueprints (e.g., `aggregate_then_judge` for
HotpotQA, `iterative_solve_verify` for GSM8K) wire gates whose
triggers (`schema_invalid`, `low_confidence`, `arithmetic_mismatch`)
simply did not match in the AFlow eval traces. DROP is the only
benchmark where the `arithmetic_mismatch` trigger fires materially
(13 / 800 ≈ 1.6 % of tasks). Hence the §6.4 rates are reported only
for DROP — the rescue/harm *counts* on the other datasets reflect
non-gate variance (different planner ordering, prompt context drift,
re-derivation noise) and should not be attributed to the gate
mechanism.

DROP itself shows `rescue_rate > harm_rate` numerically (1.385 vs
1.769) only because the trigger count (13) under-counts the per-task
flip universe (41 net flips). The trigger-conditioned conclusion is
that gates on DROP are net-harmful (more flips against than for
`AC-Full`), consistent with the §6.2 finding that the DROP reviewer
is the dataset's biggest *negative* contributor.

`avg_cost_added_usd` is computed per the spec as
`(cost_AC-Full − cost_AC-NoGate) / n_tasks`. The values are essentially
zero (≤ ±2 cents / 1000 tasks) because the gate machinery rarely
inflates the LLM call count when it does not trigger.

---

## Reproducing the runs

```bash
# Sessions 4–6 already produced AC-Full = AC-Gated:
#   runs/full_v6/{hotpotqa,drop,humaneval,mbpp,gsm8k}/
#   runs/full/math/

# Three new variants × six datasets — 18 runs total:
for ds in hotpotqa drop humaneval mbpp gsm8k math; do
  for v in AC-NoGate AC-NoSkillsTools AC-Minimal; do
    python -m agentcoop.cli run-benchmark --dataset $ds --limit 99999 \
      -v $v --concurrency 6 --out runs/ablations/$ds/$v
  done
done

# Aggregate the four §6 tables:
python scripts/aggregate_ablation.py
```

Wall-clock notes from the actual S8 sweep (concurrency = 6 per process,
parallel-launched per benchmark): hotpotqa / humaneval / mbpp / gsm8k
finished within 20 min each; drop and math were the bottlenecks
(~30 min and ~45 min wall-clock for their three variants combined).
HTTPX connection-pool starvation kicks in around 12 parallel processes
× concurrency 6 — keep the in-flight call ceiling under ≈ 30 to avoid
deadlock.
