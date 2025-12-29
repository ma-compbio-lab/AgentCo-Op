# Agent-Cop

Minimal multi-agent workflow runner with an Orchestrator, Execution Engine, and Judge.
Implemented using the OpenAI Agents SDK (no LangGraph).

## Quick start

```bash
export OPENAI_API_KEY="your-key"
pip install -r requirements.txt
python run.py task.goal="Summarize the key risks in this plan." method=orchestrated
```

### Methods

- `baseline`: single worker agent
- `sequential`: planner -> worker
- `orchestrated`: orchestrator + engine + judge

### Model backends

- `openai` (requires `openai-agents` and `OPENAI_API_KEY`)

### Config files (Hydra)

- `conf/config.yaml` controls single runs.
- `conf/eval.yaml` controls batch evaluation.

Example overrides:

```bash
python run.py task.goal="Draft a checklist." model.backend=openai model.name=gpt-4o-mini
python eval/harness.py tasks=data/tasks.jsonl method=sequential max_samples=10
python run.py task.goal="Summarize" model.backend=openai model.name=gpt-4o-mini
```

Role-specific overrides:

```bash
python run.py model.planner=gpt-4o model.worker=gpt-4o-mini model.judge=gpt-4o
```

Note: local agent builders live in `app_agents/` to avoid colliding with the SDK's `agents` module.

### Tests

```bash
pytest -q
```

### Logging

Use Hydra config to control terminal logs:

```bash
python run.py log.level=debug log.use_color=true log.use_icons=true
```

`log.level=debug` shows step inputs and output previews.

Show more debug payloads:

```bash
python run.py log.level=debug log.show_prompts=true log.show_outputs=true log.preview_chars=600
```
