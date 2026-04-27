# Compiled Workflow Blueprints

Each `*.json` here is a `WorkflowBlueprint` produced by AgentCo-Op's compiler
on a real task from the matching AFlow-aligned dataset. They are the
**canonical, reproducible, human-readable** artifact for the Session-4 paper
runs, captured under `runs/midsize/{dataset}/workflow_blueprint.json` (and
the same blueprint shape lands in every per-task trace under
`runs/full/{dataset}/traces/run-*/blueprint.json`).

## Topologies the compiler picked

| File | Topology | Level | Nodes |
|---|---|---:|---|
| `gsm8k.json` | `simple_direct_answer` | L0 | solver → formatter |
| `hotpotqa.json` | `simple_direct_answer` | L0 | solver → formatter |
| `drop.json` | `numeric_reading_comprehension` | L2 | passage_reader → span_extractor → op_classifier → numeric_reasoner → answer_formatter |
| `math.json` | `math_specialist_route` | L3 | math_router → specialist → verifier → final_extractor |
| `humaneval.json` | `code_test_repair_loop` | L6 | parser → programmer → sandbox_test → repair_planner → formatter |
| `mbpp.json` | `code_test_repair_loop` | L6 | (same as humaneval) |

The same compiler picks identical topologies for every task within a dataset
because `agentcoop.core.profiler.DATASET_PROFILE_OVERRIDES` locks-in the
profile per AFlow-aligned dataset.

## How to use a blueprint directly

```python
import asyncio
from pathlib import Path
from agentcoop.core.runtime import RuntimeConfig, run_blueprint
from agentcoop.core.schema import WorkflowBlueprint
from agentcoop.backends import default_registry
from agentcoop.backends.llm import OpenAIClient

bp = WorkflowBlueprint.model_validate_json(Path("workflows/gsm8k.json").read_text())
backends = default_registry(llm_client=OpenAIClient(model="gpt-4o-mini"))
cfg = RuntimeConfig(backends=backends, run_root="runs/quick")

outcome = asyncio.run(run_blueprint(
    bp,
    config=cfg,
    payload={
        "task": "Janet's ducks lay 16 eggs per day...",
        "task_id": "gsm8k_test_0",
        "input": {"prompt": "Janet's ducks lay 16 eggs per day..."},
        "dataset": "gsm8k",
    },
))
print(outcome.final.output["final_answer"])
```

…or via the CLI:

```bash
python -m agentcoop.cli run \
  --blueprint workflows/gsm8k.json \
  --input data/aflow_aligned/gsm8k/test.jsonl \
  --out runs/quick
```

Both paths consult `agentcoop.backends.prompts` for the role × dataset
system/user prompts, so you get identical behaviour to the Session-4 runs
without re-compiling.

## Re-compiling from scratch

If you change `DATASET_PROFILE_OVERRIDES` or any meta-skill YAML, regenerate
the canonical blueprint:

```bash
python -m agentcoop.cli compile \
  --task "$(head -1 data/aflow_aligned/gsm8k/test.jsonl | python -c 'import json,sys; print(json.loads(sys.stdin.read())[\"input\"][\"prompt\"])')" \
  --out workflows/gsm8k.json
```

(Or just re-run a benchmark: `runs/full/gsm8k/workflow_blueprint.json` is
written on every run.)

## Field reference

Every blueprint carries:

- `task_profile` — the dataset, domain, difficulty, etc. that the compiler
  resolved for this task. Strip `task_profile.task_id` and
  `task_profile.raw_task` if you want a per-dataset (not per-task) artifact.
- `nodes[]` — `node_id`, `role`, `backend` (`llm` / `python_sandbox` / etc.),
  optional `prompt_template`, `tool_policy`, `params`, and `skill_refs`.
- `edges[]` — DAG edges; `condition` strings are advisory (the runtime
  treats them as ordering only and detects/excludes back-edges).
- `gate_policies[]` — runtime-repair triggers + actions
  (`retry_node` / `add_formatter` / `add_specialist` / `terminate_with_uncertainty` / etc.)
  plus `max_activations` caps.
- `memory_plan` — visibility scope and artifact-dir layout.
- `eval_contract` — grader, normalization, and answer schema.
- `topology_level`, `complexity`, `provenance` — meta/agent-skill bindings,
  for analysis in `agentcoop analyze-gates`.
