# Agent-Cop

Minimal multi-agent workflow runner with an Orchestrator, Execution Engine, and Judge.

## Quick start

```bash
python run.py --task "Summarize the key risks in this plan." --method orchestrated
```

### Methods

- `baseline`: single worker agent
- `sequential`: planner -> worker -> verifier
- `orchestrated`: orchestrator + engine + judge

### Model backends

- `mock` (default)
- `openai` (requires `openai` package and `--model_name`)
