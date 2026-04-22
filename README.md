# AgentCo-Op

AgentCo-Op is a **task-conditioned workflow compiler** for multi-agent systems. Given a task profile and a two-level skill library, it compiles the simplest sufficient multi-agent workflow and performs only local, gate-controlled repair at runtime.

See `architecture.md` for the design and `instructions.md` for the implementation plan.

## Install

```bash
pip install -e .[dev]            # core + dev
pip install -e .[dev,bench,repo] # with benchmark + docker extras
```

## Quick start

```bash
# Framework-only: compile a blueprint from a task description
agentcoop compile --task "What is 13 * 17?" --out runs/demo_blueprint.json

# Execute a blueprint with the mock backend
agentcoop run --blueprint runs/demo_blueprint.json --out runs/demo_run
```

## Layout

```
agentcoop/
  core/        schemas, compiler, runtime, gates, tracing, reviewer, integrator
  skills/      meta-skill markdown + agent-skill YAML
  backends/    llm, mcp, python_sandbox, repo_sandbox, human_review
  memory/      blackboard, artifact_store, trace_store, skill_memory
  wrappers/    per-repo docker/adapter bundles
  benchmarks/  evaluator shims (no experiment configs yet)
```

Experiments are **not** configured yet — this is the framework skeleton.
