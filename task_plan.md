# AgentCo-Op Framework Implementation Plan

## Goal
Implement the AgentCo-Op framework — a task-conditioned workflow compiler — following `architecture.md` and `instructions.md`. Do **not** configure or run experiments yet; produce a framework skeleton with working core modules, typed IR, runtime orchestrator, gate/patch logic, skill registry, execution-backend abstractions, and memory system.

## Success Criteria
- All 8 core modules scaffolded with clear contracts.
- Typed IR (TaskProfile, WorkflowBlueprint, NodeSpec, EdgeSpec, GatePolicy) compiles and round-trips JSON.
- Skill registry loads meta-skills (Markdown+YAML frontmatter) and agent-skills (YAML).
- Runtime orchestrator runs a hand-written blueprint end-to-end with a mock LLM backend.
- Gate evaluator + patch planner enforce `max_activations` and global `max_graph_patches`.
- Memory system isolates node scratch, aggregates a typed blackboard, writes JSONL trace.
- CLI exposes `compile`, `run`, `repo wrap`, `repo smoke` entry points (stubs where needed).
- `pytest` unit tests for schema/registry/compiler/runtime/gates all pass on synthetic data.
- `implement.md` updated with repo layout, contracts, and per-module notes.

## Out of Scope (this session)
- Real benchmark data loading (AFlow-aligned, SpatialBench, BioDiscoveryAgent).
- Real LLM API calls — use mock backend + a thin OpenAI/Anthropic shim.
- Actual Docker builds or repo smoke tests — ship templates + manifest format only.
- Experiment configs in `configs/benchmarks/`.

---

## Phases

### Phase 1: Repo scaffold + tooling — status: complete
Create `agentcoop/` package tree, `pyproject.toml`, `README.md`, `configs/` skeleton, `.gitignore`, `docker/` dir, `runs/.gitkeep`, `data/.gitkeep`.
**Verify:** `python -c "import agentcoop"` works inside a venv; `pytest --collect-only` finds the tests dir.

### Phase 2: Typed IR (`core/schema.py`) — status: complete
Implement Pydantic models: `Budget`, `TaskProfile`, `NodeSpec`, `EdgeSpec`, `GatePolicy`, `WorkflowBlueprint`, `EvalContract`, `MemoryPlan`, `NodeResult`, `RunState`. Include `MetaSkill` and `AgentSkill` Pydantic models for registry objects.
**Verify:** unit tests round-trip each model through `model_dump_json()` / `model_validate_json()`; invalid inputs raise `ValidationError`.

### Phase 3: Tracing + cost (`core/tracing.py`, `core/cost.py`) — status: complete
JSONL span writer with `run_id`, `ts`, `event`, node span start/end, gate_triggered, budget_exceeded. Cost tracker aggregates tokens/USD per node.
**Verify:** unit test writes spans to a tmp file, parses them back, asserts shape.

### Phase 4: Memory system (`memory/`) — status: complete
`Blackboard` (typed, visibility-scoped write/read/summarize), `ArtifactStore` (filesystem-backed), `TraceStore` (wraps JSONL), `SkillMemory` (append-only JSONL of task_profile_hash → outcome), `Summarizer` (token-bounded concat).
**Verify:** unit test enforces that `private` scope blocks cross-node reads; artifact write/read returns byte-identical content.

### Phase 5: Execution backends (`backends/`) — status: complete
Abstract `Backend` protocol with `execute(node, payload, state) -> NodeResult`. Implementations: `llm.py` (with injectable client — default `MockLLM`), `mcp.py` (stub that validates JSON), `python_sandbox.py` (subprocess-isolated Python runner for simple code), `repo_sandbox.py` (Docker-command builder — no actual run; returns manifest+command for dry-run), `human_review.py` (auto-approve in dev, flag in prod).
**Verify:** unit test drives MockLLM to return a canned output; python_sandbox runs `print(1+1)` and captures stdout.

### Phase 6: Skill registry (`skills/`) — status: complete
`registry.py` loads Markdown-with-YAML-frontmatter from `skills/meta/` and YAML from `skills/agents/`. `retriever.py` ranks by keyword+tag overlap against `TaskProfile` (embedding-free v0). Provide 12 meta-skill stubs matching `architecture.md` §5.1 and 7 agent-skill stubs matching the tree in `instructions.md` §1.
**Verify:** unit test loads the repo's skill dir, asserts all 12 meta-skills retrievable, `search_meta(profile)` returns ranked list with deterministic order for a fixed profile.

### Phase 7: Task profiler (`core/profiler.py`) — status: complete
Hybrid rules + LLM structured-output. Rules layer pure-Python; LLM layer uses the injected backend. Do **not** store hidden chain-of-thought, only `rationale_summary`.
**Verify:** unit test feeds sample prompts ("Write a function that reverses a string", "What is 13 × 17?", "HotpotQA multi-hop sample"), asserts correct domain/answer_type/verification_available.

### Phase 8: Workflow compiler (`core/compiler.py`) — status: complete
Implement `compile_workflow`, `instantiate_topology`, `bind_agent_skills`, `attach_memory_policy`, `attach_gate_policy`, `attach_eval_contract`, `choose_simplest_sufficient_graph`. Complexity formula per `instructions.md` §3-Phase3. L0–L7 topology templates as code.
**Verify:** unit test asserts GSM8K-shaped profile compiles to an L1-or-below topology; HumanEval-shaped profile compiles to L6 with a repair gate.

### Phase 9: Gates + patches (`core/gates.py`) — status: complete
`evaluate_gates`, `plan_patch`, `apply_patch` with ops: retry_node, replace_backend, add_reviewer, add_retrieval, add_specialist, add_test_node, reroute_to_simple, escalate_human, terminate_with_uncertainty. Enforce `max_activations` per policy, `max_total_activations`, `max_same_node_repairs`.
**Verify:** unit test fires `schema_invalid`, `low_confidence`, `test_failure` and asserts corresponding patch; a repeated trigger past `max_activations` becomes a no-op.

### Phase 10: Integrator + reviewer (`core/integrator.py`, `core/reviewer.py`) — status: complete
Integrator merges typed candidate outputs per `EvalContract`. Reviewer bound to `EvalContract` — deterministic-grader confidence clamps LLM reviewer confidence.
**Verify:** unit test where two solvers disagree → integrator triggers `specialist_disagreement`; deterministic grader mismatch overrides reviewer "valid=true".

### Phase 11: Runtime orchestrator (`core/runtime.py`) — status: complete
Async executor. Builds DAG via `networkx`, topologically walks, enforces budgets, calls gate evaluator after each node, applies patches, bounded loop.
**Verify:** unit test runs a 3-node blueprint (planner → programmer mock → reviewer) to completion; asserts events.jsonl has the expected sequence.

### Phase 12: CLI (`agentcoop/cli.py`) — status: complete
Typer app: `compile`, `run`, `benchmark` (prints "not wired in framework-only mode"), `repo wrap`, `repo smoke`.
**Verify:** `agentcoop --help` works; `agentcoop compile --task-file ...` emits a blueprint JSON.

### Phase 13: Wrappers skeleton (`wrappers/`) — status: complete
Dockerfile templates, `adapter.py` stub that conforms to §6.4 request/result contract, `manifest.yaml` examples for biodiscovery + spatialagent. No image build, no network.
**Verify:** `yaml.safe_load` accepts both manifests; adapter stub parses a request.json and writes a well-formed result.json.

### Phase 14: Tests + implement.md — status: complete
Consolidate unit tests under `tests/unit/`, run full `pytest`, update `implement.md` with a module map, contracts, and known limitations.
**Verify:** all unit tests pass; `implement.md` has one section per module.

---

## Error Log
See `progress.md` for the session 1 error table.

## Decisions Log
- **Model-call abstraction**: inject a `Backend` rather than hard-coding OpenAI/Anthropic. A `MockLLM` is the default so tests run offline.
- **Skill files live in `agentcoop/skills/meta|agents/`** (not a top-level `skills/`) so they're shipped with the package.
- **Embedding-free retrieval v0**: keyword + YAML-tag overlap; embedding hook reserved for later.
- **No real Docker exec in framework phase**: `repo_sandbox.py` returns the run command; executing it is deferred to the experiment phase.
- **Tracing format**: single `events.jsonl` per run, as spec'd in `instructions.md` §3-Phase0.
