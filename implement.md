# AgentCo-Op — Implementation Record

This file tracks the framework implementation. Treat it as the single entry point for re-orientation after `/clear` or when resuming work.

**Current status:** Framework skeleton complete (2026-04-22). Experiment configuration is intentionally out of scope at this stage.

---

## What exists

### Top-level layout

```
AgentCo-Op/
  agentcoop/
    __init__.py                   re-exports core Pydantic models
    cli.py                        Typer CLI (compile / run / benchmark / repo wrap / repo smoke)
    core/
      schema.py                   TaskProfile, NodeSpec, EdgeSpec, GatePolicy, WorkflowBlueprint,
                                  NodeResult, RunState, MetaSkill, AgentSkill, EvalContract, MemoryPlan
      profiler.py                 rule-based task profiler; async LLM-augmented profiler
      compiler.py                 instantiate_topology, bind_agent_skills, gate/memory/eval
                                  attachment, minimal-sufficient graph selection
      gates.py                    GraphPatchOp enum, evaluate_gates, plan_patch, apply_patch,
                                  GateLimits
      runtime.py                  async DAG orchestrator, cycle-safe ready-set, sink-aware final
      integrator.py               majority-vote integration with contract-aware normalization
      reviewer.py                 deterministic grader clamps LLM reviewer confidence
      tracing.py                  JSONL TraceWriter + read_events
      cost.py                     CostLedger with per-node + total totals
    skills/
      registry.py                 SkillRegistry with markdown + YAML loaders and ranked retrieval
      retriever.py                thin wrappers
      meta/                       12 meta-skills (simple_direct_answer … human_review_for_high_risk)
      agents/                     7 agent-skills (math / code / retriever / bio / spatial)
    backends/
      base.py                     Backend protocol + BackendRegistry + NodeContext
      llm.py                      LLMBackend with injectable client; MockLLM default
      mcp.py                      tool-registry backend
      python_sandbox.py           subprocess-based runner with timeout
      repo_sandbox.py             command builder enforcing §6.5 safety flags (dry-run in fwk phase)
      human_review.py             auto-approve in dev mode
    memory/
      blackboard.py               typed visibility-scoped entries
      artifact_store.py           filesystem-backed artifact root
      trace_store.py              per-run JSONL trace wrapper
      skill_memory.py             append-only JSONL + task_profile_hash
      summarizer.py               token-bounded summarizer
    wrappers/
      biodiscovery/               Dockerfile.agentcoop + adapter.py + manifest.yaml (stubbed)
      spatialagent/               Dockerfile.agentcoop + adapter.py + manifest.yaml (stubbed)
  configs/
    models.yaml                   mock defaults
    budgets.yaml                  default + large presets
    safety.yaml                   sandbox + approval policy
    benchmarks/                   empty (experiments not configured yet)
  docker/
    base-python.Dockerfile        non-root base image
  tests/unit/                     45 passing tests
  runs/ data/                     .gitkeep placeholders
```

### Core contracts

- **TaskProfile** — 14 typed fields including `difficulty`, `{tool,retrieval,repo_execution}_need`, `verification_available`, `risk_level`, `budget`, and `rationale_summary`. Numeric fields bounded [0,1]; literals enforced.
- **WorkflowBlueprint** — ordered nodes, edges, gate policies, memory plan, eval contract, budget, topology level (L0–L7), complexity score, and provenance (which meta/agent skills produced the graph).
- **NodeSpec.backend ∈ {llm, mcp, python_sandbox, sandbox_repo, human_review}**. Each backend implements `async execute(node, payload, ctx) -> NodeResult`.
- **GatePolicy** — `trigger`, `threshold`, `action`, `max_activations`, scope. Triggers recognized today: `schema_invalid`, `low_confidence`, `test_failure`, `tool_error`, `evidence_missing`, `specialist_disagreement`, `budget_near_limit`, `risk_escalation`.
- **GraphPatchOp** — 10 bounded operations; `apply_patch` enforces `GateLimits.max_graph_patches`, `max_same_node_repairs`, `max_total_activations`.
- **EvalContract** — `grader` ∈ {pytest, exact_match, deterministic, llm_rubric, None}. Deterministic grader confidence always clamps the LLM reviewer (§5-Phase-5 of instructions.md).

### Compiler objective

```
score = coverage
      + 0.5·evidence_support
      + 0.5·verification_strength
      - 0.7·complexity
      - 0.3·cost
      - 0.5·risk
      - 0.2·meta.complexity_penalty
      - 0.35·max(0, meta.complexity_level - target_level_for(difficulty))
```
Tie-break by smaller complexity. Hard constraints gate candidates:
- `repo_execution_need ≥ 0.5` ⇒ must include a `sandbox_repo` node.
- `retrieval_need ≥ 0.5` ⇒ must include a retrieval node.
- `retrieval_need < 0.3` ⇒ must **not** include a retrieval node (avoid needless cost).
- `answer_type == program` ⇒ must include `python_sandbox` or programmer/tool role.
- `risk_level == high` ⇒ either a `human_review` node or an `escalate_human` gate.

### Runtime algorithm

1. Build `nx.DiGraph` from blueprint nodes + edges.
2. Detect back-edges once per outer iteration and remove them from the readiness computation (so cycles don't deadlock).
3. Execute ready nodes in sorted (deterministic) order; run each node via its backend; append `NodeResult` to blackboard and `events.jsonl`.
4. After every node, evaluate gates. Fire patches while respecting `max_activations`, `max_same_node_repairs`, `max_graph_patches`, and budget fraction.
5. Stop when no node is ready, `state.terminated`, budget exceeded, or all nodes executed.
6. `_pick_final_result` prefers sinks with `formatter`/`integrator`/`reviewer` roles; falls back to the last result.

### Skill files

- **Meta-skills** use Markdown + YAML frontmatter. The YAML carries `name`, `tags`, `complexity_level`, `complexity_penalty`, `task_signals`, `topology_template.nodes/edges`, `gates`, `memory_policy`, `expected_benefit`, and optional `requires_verification` + `evidence_refs`.
- **Agent-skills** are YAML with `backend_type`, `capabilities`, `applicable_tags`, `input_contract`, `output_contract`, `source` (repo + commit for sandbox backends), `risk`, `cost_class`.
- Meta-skill `code_test_repair_loop` marks the `repair_planner → programmer` back-edge `condition: needs_repair` so the runtime identifies it as optional.

### Safety envelope (framework defaults)

- `repo_sandbox.build_run_command` always emits `--network`, `--read-only`, `--tmpfs`, `--cpus`, `--memory`, `--pids-limit`.
- Manifests are rejected unless `image`, `commit_sha`, and `non_root=True` are present.
- Secrets are only referenced by env-variable name; never interpolated into the generated command.
- CLI `repo wrap` scaffolds manifests with `--network none` by default.

---

## Known limitations (intentional for framework phase)

1. **No live Docker execution.** `RepoSandboxBackend.execute_live` defaults to False; live mode is stubbed. Experiment phase will wire `docker run` through the same command builder.
2. **Conditional-edge semantics are ordering-only.** The runtime respects "target waits for source" but doesn't evaluate edge `condition` strings; back-edges are discovered topologically and skipped. Real control-flow will be added together with the evaluator-optimizer end-to-end.
3. **Mock LLM only.** Real OpenAI / Anthropic / LiteLLM adapters are not implemented. The `LLMClient` Protocol is stable; experiment phase can drop in a concrete client without runtime changes.
4. **Benchmark loaders absent.** `agentcoop/benchmarks/` is empty; the CLI `benchmark` subcommand returns a `not_wired` status by design.
5. **Cost table is symbolic.** `CostLedger` uses `mock-llm` at $0. Real prices go into the `price_table` when providers are wired.
6. **Skill retrieval is keyword/tag-based.** Embedding retrieval hook is reserved but not implemented.

---

## How to run

```bash
# Unit tests
python -m pytest tests/unit -v

# Compile + run (mock backends)
python -m agentcoop.cli compile --task "What is 13 * 17?" --out runs/demo_bp.json
python -m agentcoop.cli run --blueprint runs/demo_bp.json --out runs

# Scaffold a repo manifest
python -m agentcoop.cli repo wrap \
  --repo https://github.com/Genentech/SpatialAgent \
  --commit <sha> --name SpatialAgent --out runs/spatial_manifest.yaml

# Dry-run the sandbox command builder
python -m agentcoop.cli repo smoke --manifest runs/spatial_manifest.yaml
```

Tests cover schema round-trip, memory permissions, cost ledger, registry loading, profiler rules, compiler routing on GSM8K/code/HotpotQA/DROP/repo shapes, gate activation limits, patch application, integrator/reviewer clamp, runtime DAG walk (including a cyclic code blueprint), and wrapper adapter contracts.

---

## Next (when experiments start)

1. Real LLM client (`backends/llm.py` `LLMClient` implementations).
2. Benchmark loaders (`benchmarks/aflow_splits.py` + per-benchmark files).
3. Deterministic graders (`pytest` runner for HumanEval/MBPP; GSM8K / MATH numeric grader; HotpotQA F1; DROP EM/F1).
4. Pin SpatialAgent / BioDiscoveryAgent commits and Docker-build the real wrappers.
5. Fill `configs/benchmarks/*.yaml`.
6. Wire `RepoSandboxBackend.execute_live=True` behind an opt-in flag + image-digest check.
