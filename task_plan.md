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

### Phase 15: Git commit + push — status: complete
Split the framework into 3 commits (scaffold / feat / tests+docs) and push `clean-dev`.

---

## Session 2 phases — Experiment configuration

Per `experiments.md`. Docker daemon isn't running; GitHub + HuggingFace reachable. No OpenAI key provided, so we configure but do not launch.

### Phase 16: External repo clones — status: pending
Create `external/` dir. Clone AFlow, BioDiscoveryAgent, SpatialAgent, SpatialBench, OpenAI human-eval. Pin commit SHAs into `configs/external_commits.yaml`.
**Verify:** each repo's HEAD commit recorded; directory sizes reasonable; `.gitignore` excludes `external/**`.

### Phase 17: Dataset downloads — status: pending
Download HotpotQA, DROP, GSM8K, MBPP, MATH via HuggingFace `datasets`; HumanEval via the openai human-eval repo. Write to `data/raw/` and `data/processed/`. Store file hashes in `data/data_hashes.json`.
**Verify:** each dataset has ≥ expected sample count (HotpotQA distractor ~7400 train / 7405 dev; DROP ~77k; MATH level-5 subset = 617 across 4 categories).

### Phase 18: AFlow split importer + unified JSONL converter — status: pending
Implement `agentcoop/benchmarks/aflow_splits.py` with seed=42, 20/80 val/test split, HotpotQA+DROP 1000-sample cap. Emit `data/aflow_aligned/<dataset>/{validation,test}.jsonl` with `{dataset, split, task_id, prompt, reference, metadata}`.
**Verify:** sample counts match `experiments.md` §2.1; re-running with same seed gives byte-identical files.

### Phase 19: Per-dataset loaders + `BenchmarkTask` protocol — status: pending
Implement `agentcoop/benchmarks/{common,gsm8k,math,humaneval,mbpp,hotpotqa,drop}.py`. Each exposes `load(split, limit) -> list[BenchmarkTask]`.
**Verify:** unit tests load 3 samples from each dataset without API access.

### Phase 20: Deterministic graders — status: pending
GSM8K numeric extractor + normalization; MATH boxed-answer + sympy-based equivalence; HumanEval/MBPP run `check(fn)` in the python sandbox; HotpotQA token F1 (SQuAD-style); DROP EM/F1 (official normalization).
**Verify:** unit tests on canonical gold pairs; HumanEval grader returns `pass` on reference solutions.

### Phase 21: Benchmark YAML configs — status: pending
`configs/benchmarks/{gsm8k,math,humaneval,mbpp,hotpotqa,drop,aflow_aligned,spatialbench,biodiscovery}.yaml` with variants AC-Direct, AC-Compiled, AC-Gated, AC-ForcedMulti, plus per-dataset budgets from §7.3.
**Verify:** `yaml.safe_load` accepts all files; each config declares `model`, `variant`, `budget`, `dataset`, and `grader`.

### Phase 22: Benchmark runner CLI — status: pending
Wire `agentcoop benchmark --dataset X --split Y --config Z --limit N --out dir` to load tasks, compile a blueprint per task, run (or `--dry-run` if no API key), grade, and emit `predictions.csv` + `metrics.json`. Support `AGENTCOOP_MODE=dry_run` env-var fallback.
**Verify:** `agentcoop benchmark --dataset gsm8k --limit 3 --dry-run` produces well-formed output with MockLLM.

### Phase 23: Repo wrapper Docker build scripts — status: pending
`scripts/build_repo_images.sh`, pin commit SHAs, copy `external/<repo>/` into each wrapper's build context, enforce non-root, `--network` default off. Daemon not running → document how to run later.
**Verify:** scripts parse; Dockerfiles lint; `repo smoke --manifest ...` dry-run emits the expected command with the pinned digest placeholder.

### Phase 24: Reproducibility + implement.md update — status: pending
Add `configs/external_commits.yaml`, `data/data_hashes.json` schema, and update `implement.md` with the experiment-config map. Update `.gitignore` for `external/**`, `data/raw/**`, `data/processed/**`, `data/aflow_aligned/**`.
**Verify:** `implement.md` has a "Session 2 — Experiments configured" section; repo remains small after ignore rules applied.

### Phase 25: Test + commit + push — status: pending
Run pytest; split the new work into logical commits (data loaders / graders / configs / docs); push `clean-dev`.
**Verify:** remote ref advances cleanly.

---

## Error Log
See `progress.md` for the session 1 error table.

## Decisions Log
- **Model-call abstraction**: inject a `Backend` rather than hard-coding OpenAI/Anthropic. A `MockLLM` is the default so tests run offline.
- **Skill files live in `agentcoop/skills/meta|agents/`** (not a top-level `skills/`) so they're shipped with the package.
- **Embedding-free retrieval v0**: keyword + YAML-tag overlap; embedding hook reserved for later.
- **No real Docker exec in framework phase**: `repo_sandbox.py` returns the run command; executing it is deferred to the experiment phase.
- **Tracing format**: single `events.jsonl` per run, as spec'd in `instructions.md` §3-Phase0.
