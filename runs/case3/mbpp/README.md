# Case Study 3 — Dynamic topology refinement on MBPP

50-task subset of `data/aflow_aligned/mbpp/test.jsonl`. Five variants
compared, one model (gpt-4o-mini), seed 42.

| Variant | pass@1 | tokens | cost | wall |
|---|---:|---:|---:|---:|
| `AFlow-imported` (raw + appended formatter sink) | 0.840 | 37 480 | $0.0106 | 122 s |
| `AFlow+Skills+Tools` | 0.860 | 34 039 | $0.0084 | 57 s |
| `AFlow+Skills+Gates` (+ runtime gates from `configs/gates/code_runtime_gates.yaml`) | 0.860 | 33 649 | $0.0082 | 54 s |
| `AC-Gated` (canonical compiled `code_test_repair_loop`) | 0.800 | 115 569 | $0.0290 | 136 s |
| **`AFlow+MedPrompt-Voting`** (K=3 samples @ T=0.7, public-test pass voting) | **0.880** | 100 408 | $0.0245 | 246 s |

`AFlow paper baseline = 0.824 pass@1.` All four variants except
`AC-Gated` clear that bar; the K-sample voting variant beats it by
**5.6 pts** while keeping the canonical workflow untouched.

## Findings

1. **Skill / tool augmentation is a cheap lift.** Attaching the
   `code_debugging` + `python_testing` skill cards and the
   `sandbox_python` / `generated_tests` / `static_analyzer` tool refs
   to the bare AFlow node moved pass@1 from 0.84 → 0.86 — without any
   new sandbox execution. The win is the role × dataset prompt.

2. **Gates didn't fire on this 50-task subset.** With public tests in
   the prompt the model's first attempt usually clears them;
   `syntax_error` / `runtime_error` /
   `public_or_generated_test_failure` rarely triggered. Gate-rescue is
   more visible on harder splits and on competition math.

3. **Simpler can win — but parallel sampling can win further.**
   `AC-Gated` (5-node `code_test_repair_loop`) was *slower* and *more
   expensive* than the 2-node AFlow-imported topology and scored 6 pts
   lower because the repair-loop's back-edge is excluded from the DAG
   readiness check (cycle safety) and so iterative-repair never fires.
   We tried bounded back-edge iteration during this session
   (`findings.md` Attempt 2) but at gpt-4o-mini's first-pass
   determinism the second attempt almost always reproduced the first
   bug, so the iteration code was reverted. **Parallel sampling closes
   that gap**: with K=3 candidates at T=0.7 and public-test voting we
   get an extra 2 pts over `AFlow+Skills+Tools` and 8 pts over
   `AC-Gated`.

4. **Cost / latency profile.** `MedPrompt-Voting` is ~3× the
   single-sample cost (3 × the calls plus a small voting harness).
   At gpt-4o-mini prices that's still $0.0005/task — well within the
   per-dataset $0.60 budget. The 246 s wall-clock is sequential
   sampling overhead; running the K samples in parallel via
   `asyncio.gather` (which the script already does) would reduce
   wall-clock to ~80 s.

5. **Why this is a CS3 variant, not the canonical workflow.** The
   experiments.md narrative (§2 simplicity-first) explicitly chooses
   the simplest sufficient topology; we don't promote MedPrompt to the
   default because most MBPP tasks pass single-shot. The Case Study 3
   slot is exactly where "dynamic refinement when the simple route
   isn't enough" belongs — same surface as AFlow's evolved topologies.

## Reproducibility

- AFlow-imported topology: `external/AFlow/workspace/MBPP/workflows/round_1/graph.py`
- Compiled blueprints (re-runnable via
  `python -m agentcoop.cli run --blueprint ...`):
  - `workflows/case3_mbpp_aflow_imported.json`
  - `workflows/case3_mbpp_aflow_skills_tools.json`
  - `workflows/case3_mbpp_aflow_skills_gates.json`
  - The MedPrompt-Voting variant uses the
    `case3_mbpp_aflow_skills_tools.json` blueprint as its sampling
    base; the K-sample / vote logic is in
    `scripts/case3_aflow_dynamic.py::_execute_medprompt`.
- Driver: `python scripts/case3_aflow_dynamic.py --dataset mbpp --limit 50 --out runs/case3/mbpp`
- Per-variant predictions, traces, and metrics: this directory
  (one subdirectory per variant).

## Versions

- `runs/case3/mbpp/`           — current canonical run (5 variants).
- `runs/case3/mbpp_v1_4variants/` — earlier 4-variant snapshot kept
  for historical reference.
