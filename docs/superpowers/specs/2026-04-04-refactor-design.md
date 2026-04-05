# DynaForge Refactor Design — 2026-04-04

## Scope

Three independent, sequentially committed phases:
1. **Structural cleanup** — module splitting, fallback removal, MedQA deletion
2. **Benchmark parallelization** — multiprocessing for MATH + HumanEval, result caching
3. **Dynamic synthesis backend** — composition-based workflow compiler replacing hardcoded patterns

## Constraints

- Preserve `WorkflowBlueprint` IR schema and `ExecutionReport` structure (public contracts)
- Remove error-hiding fallbacks (bare `except Exception → None`, silent passthrough, dead validators); keep structured `FailureType` results in ExecutionReport
- No MedQA anywhere in the codebase going forward
- Parallelism model: `multiprocessing.ProcessPoolExecutor` (not asyncio)
- Synthesis model: composition-based search (component library → LLM assembly → blueprint validation)

---

## Phase 1: Structural Cleanup

### executor.py split (1057 → 3 files)

**`runtime/executor.py`** (~300 lines)
- `BlueprintExecutor`: graph traversal, gate evaluation loop, repair loop orchestration
- Holds an `ExecutionContext`; delegates node execution to `node_handlers`

**`runtime/node_handlers.py`**
- One function per `NodeKind`: `handle_agent`, `handle_tool`, `handle_router`, `handle_evaluator`, `handle_cache`
- Signature: `(node: NodeSpec, inputs: dict, ctx: ExecutionContext) -> NodeResult`
- No bare except; propagates exceptions to executor which writes structured `FailureType`
- Unknown `NodeKind` raises `ValueError` (no passthrough)

**`runtime/execution_context.py`**
- `ExecutionContext` dataclass: `llm_router`, `tool_registry`, `mcp_client`, `skill_registry`, `sandbox_runner`, `artifact_store`
- Constructed once per `BlueprintExecutor.run()` call; passed into all handlers

### experiment_runner.py split (6952 → 4 files)

**`benchmarks/runner_base.py`**
- Abstract `BenchmarkRunner` with: `load_samples()`, `build_task_blueprint()`, `grade_result()`, `run()`
- Shared loop: shard splitting, checkpoint resume, report writing, result aggregation
- `run()` delegates to Phase 2 process pool

**`benchmarks/math_runner.py`** — `MathRunner(BenchmarkRunner)`

**`benchmarks/humaneval_runner.py`** — `HumanEvalRunner(BenchmarkRunner)`

**MedQA**: all code deleted (runner, grader, helpers, yaml configs, CLI subcommand)

### workflows/patterns split (1319 → package)

- `workflows/patterns/__init__.py` — `PatternLibrary` registry only
- `workflows/patterns/{id}.py` — one file per pattern, exports `build(ctx: PatternBuildContext) -> WorkflowBlueprint`
- Shared node-building helpers extracted to `workflows/patterns/_common.py`

### Fallback removal

| Location | What to remove |
|---|---|
| `executor.py` | Bare `except Exception → FailureType.unknown` for all node types → propagate; executor catches at top level only |
| `validation.py` | Dead fallback schema validator (lines 29–61) → delete entire fallback branch |
| `skills.py` | Multi-key fallback for `allowed-tools` / `allowed_tools` → enforce `allowed_tools` as canonical key |
| `runtime/llm.py` | Silent retry swallow on `reasoning_effort` → raise `LLMError` on exhaustion |
| `benchmarks/` graders | Bare `except:` in MATH `ast.literal_eval` → raise `GradingError` |
| `direct_sandbox.py` | Multiple silent early returns for missing config → raise `ConfigurationError` |

---

## Phase 2: Benchmark Parallelization

### Process pool integration

`BenchmarkRunner.run()` (base class):
```python
with ProcessPoolExecutor(max_workers=cfg.executor.benchmark_workers) as pool:
    futures = {pool.submit(_run_sample, sample, blueprint, cfg): sample for sample in pending}
    for future in as_completed(futures):
        result = future.result()   # raises on worker exception
        checkpoint(result)
```

- `_run_sample` is a module-level function (picklable): constructs fresh executor, runs blueprint, returns `TaskResult`
- Results checkpointed atomically after each sample completes (not batched)
- `benchmark_workers` defaults to `min(8, os.cpu_count())`

### Redundant grading elimination

- Grade computed exactly once in `_run_sample` before returning `TaskResult`
- `_summarize_*` functions accept pre-graded `TaskResult` list; no re-grading
- Result reconstruction (`load_results_from_disk`) returns cached `TaskResult`; grade not recomputed

### Report I/O

- Reports written with `orjson` (faster than stdlib json) and gzip-compressed (`.json.gz`)
- Load path reads gzip transparently; old `.json` files still readable for backward compat

---

## Phase 3: Dynamic Synthesis Backend

### New package: `workflows/synthesis/`

**`synthesis/component_library.py` — `ComponentLibrary`**
- Indexes all node specs, edge patterns, subgraph fragments from `patterns/` package
- Each component tagged with: `kind`, `domain_tags`, `capability_tags`, `complexity_score`
- Built once at import time from pattern modules; no external DB

**`synthesis/searcher.py` — `ComponentSearcher`**
- Input: `TaskProfile` (from existing profiler)
- Output: ranked list of `ComponentCandidate` (component + relevance score)
- Ranking: BM25 over capability tags + task description keywords; no LLM call in search step

**`synthesis/assembler.py` — `SynthesisAssembler`**
- Input: `TaskSpec`, ranked `ComponentCandidate` list, `PatternBuildContext`
- Calls LLM once with: task description + top-K candidate component specs as JSON
- LLM outputs a `WorkflowBlueprint` JSON (constrained to the IR schema via structured output / JSON mode)
- No fallback to templates; raises `SynthesisError` if LLM output fails schema validation

**`synthesis/validator.py` — `BlueprintValidator`**
- Validates assembled blueprint: IR schema, topological consistency (no cycles in base graph, gate references exist), budget plausibility
- Raises `BlueprintValidationError` with specific field path on failure

### Compiler integration

`WorkflowCompiler.compile()`:
```python
if cfg.synthesis.enabled:
    candidates = ComponentSearcher(library).search(profile)
    blueprint = SynthesisAssembler(cfg).assemble(task, candidates, ctx)
    BlueprintValidator().validate(blueprint)
    return blueprint
# else: existing pattern selector (kept for explicit pattern experiments)
```

- Synthesis enabled by default in new experiment configs; disabled only via `synthesis.enabled=false` override
- Existing `PatternLibrary` selector preserved for reproducibility of old experiments

---

## Commit Organization

| Commit | Scope |
|---|---|
| `[refactor] split executor into core, handlers, context` | Phase 1a |
| `[refactor] split experiment_runner into per-benchmark modules, delete MedQA` | Phase 1b |
| `[refactor] split patterns into per-pattern package` | Phase 1c |
| `[cleanup] remove error-hiding fallbacks across runtime` | Phase 1d |
| `[perf] parallelize benchmark execution with ProcessPoolExecutor` | Phase 2a |
| `[perf] eliminate redundant grading, add orjson+gzip report I/O` | Phase 2b |
| `[feat] add synthesis component library and BM25 searcher` | Phase 3a |
| `[feat] add LLM assembler and blueprint validator` | Phase 3b |
| `[feat] integrate synthesis backend into WorkflowCompiler` | Phase 3c |
