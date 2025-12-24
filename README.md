# Agent-Cop

Minimal multi-agent workflow runner with an Orchestrator, Execution Engine, and Judge.

## Quick start

```bash
python run.py task.goal="Summarize the key risks in this plan." method=orchestrated
```

### Methods

- `baseline`: single worker agent
- `sequential`: planner -> worker -> verifier
- `orchestrated`: orchestrator + engine + judge

### Model backends

- `mock` (default)
- `openai` (requires `openai` package, `model.name=...`, and `model.api_key` or `OPENAI_API_KEY`)

### Config files (Hydra)

- `conf/config.yaml` controls single runs.
- `conf/eval.yaml` controls batch evaluation.

Example overrides:

```bash
python run.py task.goal="Draft a checklist." model.backend=mock
python eval/harness.py tasks=data/tasks.jsonl method=sequential max_samples=10
python run.py task.goal="Summarize" model.backend=openai model.name=gpt-4o-mini
```
