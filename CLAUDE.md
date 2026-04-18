# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AgentCo-Op** is a dynamic-by-construction workflow compiler/runtime for multi-agent research automation. It compiles declarative `WorkflowBlueprint` IR into executable agent graphs with dynamic topology, metric bootstrapping, containerized agent reuse, and causal credit assignment.

## Commands

```bash
# Install (editable, with dev dependencies)
pip install -e ".[dev]"

# Install with benchmark or MCP support
pip install -e ".[benchmarks]"
pip install -e ".[mcp]"

# Run tests
pytest

# Run a single test file
pytest tests/test_runtime.py

# Run a single test by name
pytest tests/test_runtime.py::test_function_name -v

# Run the workflow CLI
agentcoop-run model=openai_gpt41_mini experiment=minimal

# Run with overrides
agentcoop-run model=openai_gpt41 experiment=minimal_repair executor.repair_enabled=true

# Bootstrap meta-skills from existing patterns
agentcoop-bootstrap-skills

# Start the web dashboard
pip install -e ".[dashboard]"
agentcoop-dashboard

# Run benchmarks
agentcoop-exp math run --model openai_gpt41_mini
agentcoop-exp humaneval run --model openai_gpt41_mini

# Run minimal example
python examples/minimal_blueprint.py
```

## Architecture

### Core Abstraction: WorkflowBlueprint

The central IR unit is `WorkflowBlueprint` (defined in `src/agentcoop/ir/schema.py`). A blueprint contains:
- `base_nodes / base_edges` — the primary directed execution graph
- `subgraphs` — conditional branches that can be activated/deactivated at runtime
- `gates` — trigger conditions (`TriggerExpr`) that activate/deactivate subgraphs based on execution state
- `eval` — hard checks (sandboxed deterministic), soft judges (LLM-based), and trace assertions
- `budget` — cost/token/time limits enforced globally

### Execution Flow

```
CLI (cli.py / experiment_cli.py)
  → Hydra config load (config.py)
  → WorkflowCompiler.compile() [workflows/compiler.py]
      → Pattern search or composition-based synthesis [workflows/synthesis/]
      → Blueprint assembled with model specs, budgets, skills
  → BlueprintExecutor.run() [runtime/executor.py]
      → Topological traversal of base_nodes + base_edges
      → Per-node dispatch via node_handlers.py (agent, tool, router, evaluator)
      → Gate evaluation → subgraph activation [runtime/conditions.py]
      → On failure: DeterministicPatchPolicy → repair loop [runtime/patching.py]
  → ExecutionReport [runtime/reports.py]
```

### Node Kinds

Defined in `ir/schema.py` as `NodeKind` enum: `agent`, `tool`, `router`, `evaluator`, `cache`.

- **Agent**: LLM-backed node with optional tools/skills/sandbox
- **Tool**: MCP tool invocation
- **Router**: LLM-based conditional branching (selects next node)
- **Evaluator**: LLM-based scoring/assessment
- **Cache**: Content-addressable memoization via `runtime/cache.py`

### Key Runtime Modules

| Module | Responsibility |
|---|---|
| `runtime/executor.py` | `BlueprintExecutor` — graph traversal, gate/repair orchestration |
| `runtime/node_handlers.py` | Per-NodeKind dispatch: `handle_llm_node`, `handle_tool_node`, `dispatch_node` |
| `runtime/execution_context.py` | `NodeExecutionContext` dataclass — injected into all handlers |
| `runtime/llm.py` | OpenAI-compatible LLM client with model routing |
| `runtime/skills.py` | SKILL.md registry; bundles skill files into LLM system prompts |
| `runtime/tool_scout.py` | Deterministic tool ranking and discovery |
| `runtime/blame.py` | Classifies failures, ranks blame candidates |
| `runtime/patching.py` | `DeterministicPatchPolicy` — generates repair plans |
| `runtime/validation.py` | JSON Schema enforcement for node IO contracts |
| `runtime/reports.py` | `ExecutionReport` model — traces, costs, blame, judge results |
| `integrations/mcp_client.py` | MCP tool discovery and invocation via SDK |
| `integrations/sandbox.py` | Docker/venv-backed sandboxed code execution |
| `workflows/compiler.py` | Pattern-based + synthesis-based blueprint generation |
| `workflows/patterns/` | Per-pattern blueprint builders (package, one file per pattern) |
| `workflows/synthesis/` | Composition-based synthesis: `ComponentLibrary`, `ComponentSearcher` (BM25), `SynthesisAssembler`, `BlueprintValidator` |
| `skills/library.py` | `SkillLibrary` — unified BM25 index over agent-skills and meta-skills |
| `skills/assembler.py` | `SkillDrivenAssembler` — assembles blueprint from meta-skill topology + per-agent skill selection |
| `skills/bootstrapper.py` | `SkillBootstrapper` — generates meta-skill SKILL.md from existing patterns |

### Configuration (Hydra)

Configs live in `src/agentcoop/conf/`. The composition structure is:

```
config.yaml (root)
  └── defaults:
        - model: openai_gpt41_mini      # src/agentcoop/conf/model/
        - experiment: minimal           # src/agentcoop/conf/experiment/
```

Override on CLI: `agentcoop-run model=openai_gpt5 experiment=math_v2`

Key executor settings (in `config.yaml`):
- `executor.artifact_root` — where reports and artifacts are written
- `executor.repair_enabled` / `executor.max_repair_iterations` — repair loop control
- `executor.benchmark_workers` — max workers for parallel benchmark execution (default: 8)

### Synthesis Backend

When `workflow_design.synthesis.enabled=True`, the compiler uses a composition-based synthesis pipeline:
1. `ComponentLibrary` indexes all node specs from existing patterns
2. `ComponentSearcher` ranks components against the `TaskProfile` using BM25
3. `SynthesisAssembler` calls the LLM with top-K components to generate a `WorkflowBlueprint`
4. `BlueprintValidator` validates the assembled blueprint (acyclicity, edge refs, gate refs)

Falls back to the existing pattern-based builder on synthesis failure.

### Skill-Driven Compilation

When `workflow_design.skill_driven.enabled=True`, the compiler uses a skill-first pipeline before the synthesis backend:
1. `SkillLibrary` indexes all SKILL.md files, partitioning into meta-skills (topology) and agent-skills (behavior)
2. BM25 search ranks meta-skills against the task description; best match defines the agent topology (roles + edges)
3. For each role, `SkillDrivenAssembler` searches agent-skills via BM25 and assigns top-K as `SkillRef` bindings
4. Blueprint assembled: each role becomes a `NodeSpec`, each agent gets different skills for specialized behavior
5. Falls back to pattern/synthesis path if no meta-skill scores above threshold

Bootstrap meta-skills from existing patterns: `agentcoop-bootstrap-skills`

### Benchmarks

Benchmark runners live in `src/agentcoop/benchmarks/`:
- `BenchmarkRunner` — abstract base with shared loop, parallel execution via `ProcessPoolExecutor`
- `MathRunner` — MATH benchmark (EleutherAI/hendrycks_math, AFlow-aligned split)
- `HumanEvalRunner` — HumanEval benchmark (OpenAI official dataset)

Use `agentcoop-exp <benchmark> run` for execution and `agentcoop-exp <benchmark>-aggregate` for result aggregation across runs. Reports are gzip-compressed (`.report.json.gz`).

### Case Studies

Domain-specific workflows are in `src/agentcoop/case_studies/`. Each is a standalone job file that constructs and runs a `WorkflowBlueprint` for a specific research task.

### Planning with Files.
Use planning-with-files for complex tasks (more than two steps) to monitor execution state and enable more effective repair. 

### superpowers

Use `superpowers` for design the framework of the code, and the structure of the code. It can be used to generate the initial codebase, and also to refactor and improve the codebase iteratively.