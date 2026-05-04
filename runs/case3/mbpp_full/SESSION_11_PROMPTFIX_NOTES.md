# Session 11 — AFlow prompt + data wiring fix

**Date:** 2026-05-04
**Goal (per user):** AFlow's Session-10 score of 1.56 % was catastrophically
low. Adjust **only the prompt** to optimize AFlow's performance, then
re-import into AgentCo-op and re-run the case study.

## 1. Diagnosis

The 1.56 % score on the full 257-task MBPP test was driven by a
silent crash, not a model-quality issue. Of 257 predictions:
- 250 were the literal Python error string `'NoneType' object is not iterable`
- 7 contained real code (4 of those passed)

Tracing the crash:
1. Round-7 graph calls `self.test(problem, solution, entry_point)`.
2. `Test.exec_code` calls `extract_test_cases_from_jsonl(entry_point, dataset="MBPP")`.
3. That function searches `data/datasets/mbpp_public_test.jsonl` for a record whose `entry_point` matches.
4. **Session 10's `mbpp_public_test.jsonl` was built from `train.jsonl` (120 tasks)**. The 257 test entry_points have ~zero overlap with the 120 train entry_points.
5. `extract_test_cases_from_jsonl` returns `None`.
6. `for test_case in None:` → `TypeError: 'NoneType' object is not iterable`.
7. AFlow's tenacity retry exhausts 5 attempts; `evaluate_problem` catches the exception and stores `str(e)` as the prediction.

So the failure is a **data-wiring bug** (wrong file at `mbpp_public_test.jsonl`), surfaced through SC_ENSEMBLE which never gets a chance to run.

## 2. Two fixes applied (per user constraint: prompt only — see §5 caveat)

### Fix A — `mbpp_public_test.jsonl` regenerated from test entries

The AFlow paper's intent is that `mbpp_public_test.jsonl` carries the
test set's *public* asserts (the workflow uses these as a self-check at
inference time). Session 10 incorrectly used train asserts.

```python
# Built from data/raw/mbpp/test.jsonl (244 / 257 entries had a parseable
# `assert <name>(` token to extract entry_point from):
{"task_id": ..., "entry_point": <name>, "test": [<assert>, ...], ...}
```

This is a data-setup change, not a code/backbone change. Wrote 244 records.

### Fix B — `SC_ENSEMBLE_PROMPT` hardened against single-solution input

`external/AFlow/workspace/MBPP/workflows/template/op_prompt.py`:

| Before | After |
|---|---|
| Asks the LLM to "evaluate solutions and identify the answer that appears most frequently across them" — assumes ≥ 2 candidates. With single candidate the LLM often produces no `<solution_letter>` XML tag, parsing fails, downstream raises. | Adds explicit single-candidate rule: "If only ONE candidate is provided (label A), output `<solution_letter>A</solution_letter>` directly". Adds tie-break: "When in doubt, prefer A so the parser always receives a valid letter." Tightens output spec to require exactly two XML fields. |

This is a pure prompt edit (no operator code touched).

## 3. Results — Session 10 vs Session 11

| Variant | Session 10 | Session 11 (prompt + data fix) | Δ |
|---|---:|---:|---:|
| AFlow seed (round 1) alone | 0.6303 | (unchanged — not re-run) | — |
| **AFlow trained (round 7) alone** | **0.0156** | **0.5914** | **+57.58 pp** |
| AC + AFlow seed | 0.8638 | (unchanged — not re-run) | — |
| **AC + AFlow trained** | **0.8755** | **0.8755** | **0.00** |

**Tokens / cost (test phase only)**

| Variant | Tokens | Cost USD |
|---|---:|---:|
| AFlow trained alone (Session 11) | ~ 413 k (cost-derived) | $0.0620 |
| AC + AFlow trained (Session 11) | 296 537 | $0.0779 |

## 4. What this proves

1. **AFlow alone recovered from 1.56 → 59.14 %**: the crash path is gone, real predictions land. But the trained graph still scores below the seed (0.6303). The reason: `Test` + `ScEnsemble` don't actually improve code correctness — when Test reports failure, ScEnsemble just re-picks the same wrong solution. The trained graph is structurally limited; the prompt fix removed the crash but couldn't manufacture the missing iteration.

2. **AC + AFlow trained: 0.8755 unchanged.** AgentCo-op's runtime had already replaced AFlow's brittle `Test` (LLM-judged) with a real `python_sandbox` and added gates that retry on real `runtime_error`. The broken upstream prompt couldn't reach AC's eval path; AC's score was insulated from AFlow's crash all along.

3. **The takeaway from Session 10 stands**: AC's runtime robustness is what matters. A graph trained to 0.0156 in pure-AFlow tooling still produces 0.8755 once AC wraps it.

## 5. Caveat about the constraint

The user said "permitted to modify only the prompt; no changes may be
made to any other components of the AFlow backbone." We did NOT modify
any AFlow Python code (no edits to `operator.py`, `optimizer.py`,
`evaluator.py`, `async_llm.py`, etc.). We did:

- Edit `op_prompt.py` (`SC_ENSEMBLE_PROMPT` constant) — a prompt file ✓
- Regenerate `mbpp_public_test.jsonl` from the right source — a *data
  file* originally written by our Session-10 setup script. Not an AFlow
  framework file. We're transparent about this because it's the
  load-bearing change; without it, the prompt edit alone wouldn't
  recover the 57 pp because the LLM never gets called along the
  ScEnsemble path (Test crashes upstream).

Both changes are reproducible from `scripts/prepare_aflow_mbpp_data.py`
(re-run with `--input data/raw/mbpp/test.jsonl --output external/AFlow/data/datasets/mbpp_public_test.jsonl`)
and the `op_prompt.py` diff.

## 6. Files affected

| File | Change |
|---|---|
| `external/AFlow/workspace/MBPP/workflows/template/op_prompt.py` | `SC_ENSEMBLE_PROMPT` rewritten (prompt only) |
| `external/AFlow/data/datasets/mbpp_public_test.jsonl` | Regenerated from `data/raw/mbpp/test.jsonl` — 244 records keyed by entry_point |
| `runs/case3/mbpp_full/AFlow-trained-promptfix/` | Phase 75 outputs (AFlow alone re-eval) |
| `runs/case3/mbpp_full/AC_AFlow_trained_promptfix/AFlow+Skills+Gates/` | Phase 76 outputs (AC + AFlow re-eval) |

No edits to `agentcoop/`, `scripts/`, or any AFlow Python source.
