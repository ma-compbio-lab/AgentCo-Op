# Case Study 3 — Dynamic topology refinement on MBPP

50-task subset of `data/aflow_aligned/mbpp/test.jsonl`. Four variants
compared, one model (gpt-4o-mini), seed 42.

| Variant | pass@1 | tokens | cost | wall-clock |
|---|---:|---:|---:|---:|
| `AFlow-imported` (raw, formatter sink only) | 0.840 | 37 729 | $0.0107 | 113 s |
| `AFlow+Skills+Tools` (skills + tools attached) | **0.880** | 33 549 | $0.0082 | 53 s |
| `AFlow+Skills+Gates` (+ runtime gates from `configs/gates/code_runtime_gates.yaml`) | 0.840 | 33 981 | $0.0083 | 56 s |
| `AC-Gated` (canonical compiled workflow) | 0.820 | 115 231 | $0.0289 | 116 s |

## Findings

1. **Skill/tool augmentation is the lift, not gates.** Adding the
   `code_debugging` + `python_testing` skill cards and the `sandbox_python`
   / `generated_tests` / `static_analyzer` tool refs to the AFlow node
   pushed pass@1 from 0.84 → 0.88 — without doing any actual sandbox
   execution. The win is purely from the role × dataset prompts firing on
   the augmented node.

2. **Gates didn't fire in this subset.** With 50 MBPP tasks the model's
   first attempt cleared the public tests almost every time;
   `syntax_error` / `runtime_error` / `public_or_generated_test_failure`
   never triggered. Gate-rescue is more visible on harder splits and on
   competition math.

3. **Simpler can win.** AC-Gated (5-node `code_test_repair_loop`) was
   *slower* and *more expensive* than the 2-node AFlow-imported topology
   on this subset, and scored 6 pts lower. Two interpretations:
   (a) the parser/sandbox/repair_planner overhead in `code_test_repair_loop`
   doesn't help when the back-edge can't actually iterate (cycles are
   excluded from readiness for deadlock safety);
   (b) public tests in the prompt + a strong programmer prompt + a
   formatter sink is already a near-saturated workflow for MBPP.

## Reproducibility

- Imported AFlow topology: `external/AFlow/workspace/MBPP/workflows/round_1/graph.py`
- Blueprints (re-runnable via `python -m agentcoop.cli run --blueprint ...`):
  - `workflows/case3_mbpp_aflow_imported.json`
  - `workflows/case3_mbpp_aflow_skills_tools.json`
  - `workflows/case3_mbpp_aflow_skills_gates.json`
- Driver: `scripts/case3_aflow_dynamic.py --dataset mbpp --limit 50`
- Per-variant predictions and traces: this directory.
