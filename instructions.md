# AgentCo-Op instructions.md

This document provides the implementation guide for AgentCo-Op. The goal is to first build a reproducible experimental system, then gradually extend it to specialized agent collaboration.

---

## 1. Recommended repository structure

```text
agentcoop/
  pyproject.toml
  README.md
  configs/
    models.yaml
    budgets.yaml
    safety.yaml
    benchmarks/
      aflow_aligned.yaml
      biodiscovery.yaml
      spatialbench.yaml
  agentcoop/
    __init__.py
    core/
      schema.py              # TaskProfile, WorkflowBlueprint, NodeSpec, GatePolicy
      compiler.py            # skill-guided compilation
      runtime.py             # graph executor
      gates.py               # gate triggers and patch operations
      integrator.py
      reviewer.py
      tracing.py
      cost.py
    skills/
      registry.py
      retriever.py
      meta/
        simple_direct_answer.md
        single_agent_tool_use.md
        retrieval_grounded_qa.md
        numeric_reading_comprehension.md
        math_specialist_route.md
        parallel_self_consistency.md
        code_test_repair_loop.md
        orchestrator_workers_research.md
        evaluator_optimizer.md
        sandbox_repo_execution.md
        domain_agent_collaboration.md
      agents/
        math_number_theory.yaml
        math_algebra.yaml
        code_programmer.yaml
        hotpot_retriever.yaml
        drop_numeric_reasoner.yaml
        biodiscovery_agent.yaml
        spatial_agent.yaml
    backends/
      llm.py
      mcp.py
      python_sandbox.py
      repo_sandbox.py
      human_review.py
    memory/
      blackboard.py
      artifact_store.py
      trace_store.py
      skill_memory.py
      summarizer.py
    wrappers/
      biodiscovery/
        Dockerfile.agentcoop
        adapter.py
        manifest.yaml
      spatialagent/
        Dockerfile.agentcoop
        adapter.py
        manifest.yaml
    benchmarks/
      common.py
      aflow_splits.py
      hotpotqa.py
      drop.py
      humaneval.py
      mbpp.py
      gsm8k.py
      math.py
      spatialbench.py
      biodiscovery.py
    cli.py
  tests/
    unit/
    integration/
    smoke_repos/
  runs/
    .gitkeep
  data/
    .gitkeep
  docker/
    base-python.Dockerfile
```

---

## 2. Basic technical stack

### 2.1 Python environment

Use Python 3.11 or 3.12.

Core dependencies:

```toml
[project]
dependencies = [
  "pydantic>=2",
  "typer>=0.12",
  "rich>=13",
  "networkx>=3",
  "jinja2>=3",
  "pyyaml>=6",
  "jsonschema>=4",
  "tenacity>=8",
  "httpx>=0.27",
  "python-dotenv>=1",
  "docker>=7",
  "gitpython>=3",
  "datasets>=2.20",
  "evaluate>=0.4",
  "numpy>=1.26",
  "pandas>=2.2",
  "scikit-learn>=1.5",
  "pytest>=8",
]
```

Model calls can use any of the following layers:

- Direct OpenAI / Anthropic SDK calls.
- LiteLLM to unify multiple providers.
- Later integration with the OpenAI Agents SDK / Anthropic MCP as backends, while keeping the framework itself provider-agnostic.

### 2.2 Configuration principles

All experimental parameters must live in YAML: model name, temperature, max tokens, seed, budget, maximum repair count, network permissions, repo commit, and data split. Do not scatter these settings across the codebase.

Example:

```yaml
models:
  default_executor:
    provider: openai
    model: gpt-4o-mini
    temperature: 0
    max_tokens: 4096
  high_reasoning:
    provider: anthropic
    model: claude-3-5-sonnet-20240620
    temperature: 0

runtime:
  max_graph_patches: 3
  max_node_retries: 2
  max_cost_usd: 2.0
  max_wall_time_s: 900

safety:
  sandbox_network_default: false
  require_approval_for:
    - network_enable
    - write_outside_workspace
    - unknown_shell_command
    - package_install_from_url
```

---

## 3. Implementation order

### Phase 0: Define the IR and runtime event log

Implement the data structures first. Do not start with complex agents.

```python
# agentcoop/core/schema.py
from pydantic import BaseModel, Field
from typing import Any, Literal

class Budget(BaseModel):
    max_tokens: int = 20000
    max_cost_usd: float | None = None
    max_wall_time_s: int = 900
    max_iterations: int = 3

class TaskProfile(BaseModel):
    task_id: str
    raw_task: str
    domain: list[str]
    answer_type: str
    objective: str
    difficulty: Literal["trivial", "simple", "moderate", "complex", "open_ended"]
    decomposition_need: float = Field(ge=0, le=1)
    tool_need: float = Field(ge=0, le=1)
    retrieval_need: float = Field(ge=0, le=1)
    repo_execution_need: float = Field(ge=0, le=1)
    verification_available: str
    risk_level: Literal["low", "medium", "high"]
    budget: Budget
    output_schema: dict[str, Any] | None = None
    constraints: list[str] = []

class NodeSpec(BaseModel):
    node_id: str
    role: str
    backend: Literal["llm", "mcp", "python_sandbox", "sandbox_repo", "human_review"]
    skill_refs: list[str] = []
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}
    memory_scope: Literal["private", "shared_read", "shared_write", "artifact_only"] = "private"
    prompt_template: str | None = None
    tool_policy: dict[str, Any] = {}
    timeout_s: int = 300
    max_retries: int = 1

class EdgeSpec(BaseModel):
    source: str
    target: str
    condition: str | None = None
    payload_map: dict[str, str] = {}

class GatePolicy(BaseModel):
    name: str
    trigger: str
    threshold: float | None = None
    action: str
    max_activations: int = 1

class WorkflowBlueprint(BaseModel):
    blueprint_id: str
    task_profile: TaskProfile
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    gate_policies: list[GatePolicy]
    memory_plan: dict[str, Any]
    eval_contract: dict[str, Any]
    budget: Budget
    provenance: list[str] = []
```

Also implement JSONL tracing:

```json
{"run_id":"...","ts":"...","event":"node_start","node_id":"programmer","input_hash":"..."}
{"run_id":"...","ts":"...","event":"node_end","node_id":"programmer","tokens":1234,"cost":0.01,"latency_s":4.2,"confidence":0.74}
{"run_id":"...","ts":"...","event":"gate_triggered","gate":"test_failure","action":"repair_node"}
```

### Phase 1: Implement the skill registry

Skill files should use Markdown + YAML frontmatter. This makes them both human-readable and machine-retrievable.

Example:

```markdown
---
name: retrieval_grounded_qa
kind: meta_skill
version: 0.1
tags: [qa, retrieval, evidence, hotpotqa]
complexity_level: 3
complexity_penalty: 0.35
requires_verification: evidence_overlap
---

# Intent
Use a retrieval-grounded workflow for multi-hop or evidence-sensitive QA.

# Task signals
- The question requires facts not fully provided in prompt.
- Multiple entities or bridge questions appear.
- The answer should be short but evidence must be checked.

# Topology template
TaskProfiler -> QueryPlanner -> Retriever -> EvidenceSelector -> Answerer -> EvidenceReviewer -> Finalizer

# Gates
- evidence_missing: add_retrieval
- answer_evidence_conflict: rewrite_answer
- budget_near_limit: terminate_with_uncertainty

# When not to use
- The answer is already in the prompt.
- The task is simple arithmetic or coding.
```

The registry should support:

```python
class SkillRegistry:
    def load_dir(self, path: str) -> None: ...
    def search_meta(self, profile: TaskProfile, top_k: int = 8) -> list[MetaSkill]: ...
    def search_agents(self, profile: TaskProfile, top_k: int = 20) -> list[AgentSkill]: ...
```

The first version can use keyword + tag matching; add embeddings later.

### Phase 2: Implement the Task Profiler

The task profiler can first use a hybrid of rules + LLM structured output.

#### Rule layer

- `answer_type=program`: contains “write code / implement / bug / function / class / HumanEval / MBPP”.
- `domain=math`: contains numbers, equations, proof requests, or GSM8K/MATH labels.
- `retrieval_need=high`: user asks for latest information, papers, citations, or external facts.
- `repo_execution_need=high`: user provides a GitHub repo, requests experiments, asks to install tools, or asks to process data.
- `verification_available=unit_test`: tests, a grader, or expected answers are available.

#### LLM layer output schema

```json
{
  "domain": ["math"],
  "answer_type": "short_answer",
  "objective": "solve",
  "difficulty": "moderate",
  "decomposition_need": 0.3,
  "tool_need": 0.1,
  "retrieval_need": 0.0,
  "repo_execution_need": 0.0,
  "verification_available": "exact",
  "risk_level": "low",
  "rationale_summary": "One paragraph, no hidden chain-of-thought."
}
```

Save only `rationale_summary`; do not save full hidden reasoning.

### Phase 3: Implement the Workflow Compiler

The core idea is “minimal sufficient topology.” Pseudocode:

```python
def choose_simplest_sufficient_graph(graphs, profile):
    scored = []
    for g in graphs:
        coverage = capability_coverage(g, profile)
        evidence = evidence_support(g, profile)
        verify = verification_strength(g, profile)
        complexity = graph_complexity(g)
        cost = estimated_cost(g)
        risk = estimated_risk(g)
        score = coverage + 0.5 * evidence + 0.5 * verify - 0.7 * complexity - 0.3 * cost - 0.5 * risk
        if coverage >= required_coverage(profile) and risk_policy_ok(g, profile):
            scored.append((score, complexity, g))
    # first maximize score, tie-break by smaller complexity
    return sorted(scored, key=lambda x: (-x[0], x[1]))[0][2]
```

`graph_complexity` should not only count nodes. It should also consider:

- Upper bound on LLM calls.
- Whether it uses parallel specialists.
- Whether it uses sandbox/repo execution.
- Whether it has loops.
- Whether it has handoffs.
- Whether human approval is required.

Recommended complexity formula:

```python
complexity = (
    0.10 * num_llm_nodes
  + 0.20 * num_tool_nodes
  + 0.35 * num_sandbox_nodes
  + 0.25 * num_gates
  + 0.40 * has_loop
  + 0.30 * has_parallel
  + 0.50 * has_human_review
)
```

### Phase 4: Implement the Runtime Orchestrator

Minimal runner:

```python
async def run_blueprint(blueprint, input_payload):
    state = RunState(...)
    graph = build_dag(blueprint)
    ready = initial_nodes(graph)

    while ready and not state.budget_exceeded():
        node = ready.pop()
        result = await execute_node(node, state)
        state.blackboard.write(node.node_id, result)
        trace.node_end(node, result)

        gate_actions = evaluate_gates(blueprint.gate_policies, state, node, result)
        for action in gate_actions:
            patch = plan_patch(action, state)
            apply_patch(graph, patch, state)
            trace.gate(action, patch)

        ready.extend(next_ready_nodes(graph, state))

    return finalize(state)
```

Avoid infinite loops:

- `max_graph_patches`.
- Each gate has `max_activations`.
- Each node has `max_retries`.
- Global `max_wall_time_s` and `max_cost_usd`.

### Phase 5: Implement the Integrator / Reviewer

Integrator input:

```json
{
  "task": "...",
  "candidate_outputs": [
    {"node_id": "solver_a", "answer": "...", "confidence": 0.72, "evidence": []},
    {"node_id": "solver_b", "answer": "...", "confidence": 0.68, "evidence": []}
  ],
  "eval_contract": {
    "answer_type": "short_answer",
    "normalization": "gsm8k_numeric"
  }
}
```

The reviewer must output:

```json
{
  "valid": true,
  "confidence": 0.81,
  "detected_issues": [],
  "repair_request": null,
  "final_answer": "42"
}
```

For tasks with deterministic evaluators, the LLM reviewer’s commentary must not override the evaluator:

```python
if evaluator_result.is_deterministic:
    reviewer_confidence = min(reviewer_confidence, evaluator_result.confidence)
```

---

## 4. Concrete meta-skill specifications

### 4.1 `simple_direct_answer`

Purpose: prevent overuse of multi-agent workflows.

Triggers:

- `difficulty in [trivial, simple]`
- `tool_need < 0.2`
- `repo_execution_need == 0`
- `risk_level == low`

Compilation:

```text
SingleAgent -> Finalizer
```

Gates:

- schema invalid → formatter.
- low confidence → one-shot self-check, without escalating to multi-specialist execution.

### 4.2 `single_agent_tool_use`

Purpose: simple retrieval, computation, and lightweight code tasks.

Compilation:

```text
SingleAgentWithTools -> ToolResultChecker -> Finalizer
```

Note: if the number of tools is > 6, or tool descriptions overlap heavily, split into specialists or a router.

### 4.3 `retrieval_grounded_qa`

For HotpotQA / open-domain QA.

Nodes:

1. QueryPlanner: generate 2-5 retrieval queries.
2. Retriever: BM25 / dense retriever / web / dataset corpus.
3. EvidenceSelector: choose supporting facts.
4. Answerer: answer only from evidence.
5. EvidenceReviewer: check whether the answer is supported by evidence.
6. Finalizer: output a short answer.

Gates:

- evidence count < 2 → add_retrieval.
- answer not in evidence → rewrite_answer.
- conflicting evidence → ask a second Answerer.

### 4.4 `numeric_reading_comprehension`

For DROP.

Nodes:

```text
PassageReader -> SpanExtractor -> OperationClassifier -> NumericReasoner -> AnswerFormatter
```

Key point: DROP errors often come from numeric operations and answer normalization. Do not default to debate. Prefer an arithmetic checker.

### 4.5 `math_specialist_route`

For GSM8K / MATH.

Nodes:

```text
MathRouter -> Specialist -> Verifier -> FinalAnswerExtractor
```

Specialist types:

- algebra
- geometry
- number theory
- combinatorics
- probability
- prealgebra
- precalculus
- word problem arithmetic

Gates:

- verifier fails → retry with error summary.
- low confidence + high difficulty → parallel specialist.
- answer extraction fails → formatter only; do not redo the reasoning.

### 4.6 `parallel_self_consistency`

Use only when the task is difficult, the model is low-cost, and answers can be automatically verified. Do not make it the default.

```text
Solver_1
Solver_2
Solver_3
   ↓
Vote / Verifier / Integrator
```

Limits:

- Maximum parallel count: 3 or 5.
- If a deterministic verifier exists, prefer the verifier over majority voting.
- For evidence-sensitive tasks such as HotpotQA, majority voting may amplify hallucination and must be paired with an evidence reviewer.

### 4.7 `code_test_repair_loop`

For HumanEval / MBPP.

```text
ProblemParser -> Programmer -> UnitTestRunner -> RepairPlanner -> Programmer
                                   ↓ pass
                              FinalFormatter
```

Implementation notes:

- Public tests are included as prompt input; hidden tests are used only by the evaluator.
- Repair prompts should include only failed tests, stderr, and relevant code snippets, avoiding very long logs.
- Distinguish environment failures from code logic failures.
- Limit repairs to 2-3 rounds; after that, return the best candidate.

### 4.8 `sandbox_repo_execution`

For any existing GitHub repo / agent.

Compilation:

```text
RepoSetupInspector -> SandboxBuilder -> SmokeTest -> RepoAdapterNode -> ArtifactReviewer
```

The first three nodes can be skipped at runtime if the manifest and image cache already exist.

---

## 5. Agent-skill discovery and registration

### 5.1 Pre-registered skills

Each agent-skill should use YAML:

```yaml
name: math_number_theory_solver
kind: agent_skill
backend_type: llm
capabilities: [modular_arithmetic, divisibility, diophantine, olympiad_style]
prompt_file: prompts/math_number_theory.md
input_contract:
  problem: str
output_contract:
  final_answer: str
  solution_summary: str
  confidence: float
cost_class: low
risk_level: low
```

### 5.2 Dynamic skill discovery

When a task mentions an external repo / library / benchmark:

1. Parse the URL, repo name, and paper name.
2. Check whether the local registry already has a manifest.
3. If not, invoke `RepoSetupInspector`:
   - Read README, pyproject, requirements, Dockerfile, and examples.
   - Identify entry commands, required API keys, and data paths.
   - Generate an initial manifest.
4. Run a smoke test in an isolated sandbox.
5. Register it as an `AgentSkill` only after it passes.

Note: dynamic discovery is not automatic trust. Repos that do not pass smoke tests may be used only as documentation references, not as execution nodes.

---

## 6. GitHub repo wrapping guide

### 6.1 Automatic wrapping flow

```text
Input repo URL + optional commit
  → clone to cache
  → pin commit SHA
  → inspect metadata
  → generate Dockerfile.agentcoop if needed
  → build image
  → run smoke test
  → create manifest.yaml
  → expose adapter as sandbox_repo node
```

### 6.2 Repo inspector

It needs to read:

- README / docs.
- `pyproject.toml` / `setup.py` / `requirements.txt` / `environment.yml`.
- Dockerfile / docker-compose.
- examples / notebooks / scripts.
- license.
- tests.

Output:

```json
{
  "repo_url": "...",
  "commit_sha": "...",
  "language": "python",
  "install_commands": ["pip install -e ."],
  "entrypoints": ["python main.py ..."],
  "needs_api_keys": ["ANTHROPIC_API_KEY"],
  "smoke_tests": ["python -c 'import spatialagent'"],
  "risk_notes": ["requires optional network for LLM calls"]
}
```

### 6.3 Generic Dockerfile template

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 agent
WORKDIR /workspace

COPY repo/ /workspace/repo/
WORKDIR /workspace/repo

RUN python -m pip install --upgrade pip setuptools wheel
RUN if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
RUN pip install -e . || true

WORKDIR /workspace
COPY adapter.py /workspace/adapter.py
RUN mkdir -p /outputs && chown -R agent:agent /outputs /workspace
USER agent

ENTRYPOINT ["python", "/workspace/adapter.py"]
```

### 6.4 Adapter contract

Each repo adapter should support:

```bash
python adapter.py --input /inputs/request.json --output /outputs/result.json
```

`request.json`:

```json
{
  "task_id": "...",
  "command": "run_dataset",
  "params": {
    "dataset_path": "/inputs/data",
    "question": "..."
  },
  "limits": {
    "timeout_s": 3600,
    "max_steps": 5
  }
}
```

`result.json`:

```json
{
  "ok": true,
  "answer": "...",
  "confidence": 0.72,
  "artifacts": ["/outputs/report.md", "/outputs/result.csv"],
  "metrics": {},
  "logs_summary": "...",
  "errors": []
}
```

### 6.5 Sandbox run command

```bash
docker run --rm \
  --network none \
  --cpus 4 \
  --memory 16g \
  --pids-limit 512 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=2g \
  -v "$PWD/inputs:/inputs:ro" \
  -v "$PWD/outputs:/outputs:rw" \
  agentcoop/spatialagent:<sha> \
  --input /inputs/request.json \
  --output /outputs/result.json
```

When network access / API keys are required, do not simply open full network access:

- Use a controlled proxy.
- Allow only necessary domains.
- Log request metadata.
- Inject secrets through the wrapper and never write them to logs or artifacts.

---

## 7. BioDiscoveryAgent wrapper draft

### 7.1 Capability boundary

BioDiscoveryAgent is suitable for closed-loop genetic perturbation experiment design. It is not a general-purpose biology QA system. When AgentCo-Op invokes it, the task should be translated into:

- Dataset name / perturbation screen.
- Number of genes that can be selected per round.
- Observed results.
- Whether literature review / critique / pathway tools are enabled.
- Output gene list / ranked candidate perturbations.

### 7.2 Adapter input

```json
{
  "command": "run_closed_loop_design",
  "params": {
    "dataset": "IFNG",
    "num_steps": 5,
    "num_genes": 128,
    "flags": {
      "lit_review": true,
      "critique": true,
      "reactome": true
    }
  }
}
```

### 7.3 Adapter output

```json
{
  "ok": true,
  "ranked_genes": ["STAT1", "IRF1", "JAK2"],
  "per_round_metrics": [
    {"round": 1, "hit_rate": 0.12},
    {"round": 2, "hit_rate": 0.18}
  ],
  "artifacts": ["/outputs/results.csv", "/outputs/logs.txt"],
  "logs_summary": "Closed-loop run completed."
}
```

### 7.4 Notes

- Pin the commit corresponding to the paper; model names in the README may be stale, so record the actual model used.
- If Claude / OpenAI APIs are required, the wrapper must explicitly label the cost.
- Run the official small-data smoke test before the full experiment.
- Results depend on randomness, API versions, and literature tool availability; raw logs must be preserved.

---

## 8. SpatialAgent wrapper draft

### 8.1 Capability boundary

SpatialAgent is suitable for spatial biology data analysis: QC, normalization, dimensionality reduction, clustering, cell typing, differential expression, spatial analysis, and hypothesis generation. It needs data files and a clearly defined task.

### 8.2 Adapter input

```json
{
  "command": "answer_spatial_task",
  "params": {
    "dataset_dir": "/inputs/spatial_task",
    "question": "Identify spatially variable genes associated with region X.",
    "output_dir": "/outputs"
  }
}
```

### 8.3 Adapter output

```json
{
  "ok": true,
  "answer": "...",
  "gene_sets": {
    "spatially_variable": ["GENE1", "GENE2"],
    "markers": ["GENE3"]
  },
  "artifacts": ["/outputs/analysis.ipynb", "/outputs/figures/umap.png"],
  "metrics": {},
  "logs_summary": "Plan-Act-Conclude workflow completed."
}
```

### 8.4 Notes

- The full SpatialBench benchmark may have access restrictions; first use public canonical examples for smoke tests and demos.
- If using full SpatialBench, follow the benchmark’s anti-overfitting requirements.
- Save generated code, plots, intermediate data, and grader output for every task.

---

## 9. Memory implementation

### 9.1 Filesystem layout

```text
runs/{run_id}/
  task.json
  blueprint.json
  events.jsonl
  blackboard.jsonl
  nodes/
    planner/
      input.json
      output.json
      summary.md
    spatialagent/
      request.json
      result.json
      stdout.txt
      stderr.txt
  artifacts/
    ...
  final.json
```

### 9.2 Blackboard API

```python
class Blackboard:
    def write(self, node_id: str, key: str, value: Any, visibility: str = "shared") -> None: ...
    def read(self, key: str, requester: str) -> Any: ...
    def summarize_for(self, node_id: str, max_tokens: int) -> str: ...
```

Only allow sharing of:

- final/candidate answers.
- evidence snippets with source ids.
- artifact paths.
- metric results.
- error summaries.
- short rationale summaries.

Do not share:

- full hidden chain-of-thought.
- secrets / credentials.
- unrelated node logs.
- unfiltered external webpage content.

### 9.3 Skill memory

At the end of every run, write:

```json
{
  "task_profile_hash": "...",
  "skills_used": ["math_specialist_route", "parallel_self_consistency"],
  "blueprint_id": "...",
  "success": true,
  "metric": 1.0,
  "cost_usd": 0.12,
  "latency_s": 23,
  "gate_counts": {"low_confidence": 1},
  "failure_modes": [],
  "summary": "Parallel solver helped because initial specialists disagreed."
}
```

In future compilation, use this only as a prior / warning, not as an automatic search mechanism.

---

## 10. Gate and patch implementation

### 10.1 Gate evaluator

```python
def evaluate_gates(policies, state, node, result):
    actions = []
    for p in policies:
        if p.name == "schema_invalid" and not validate_schema(result, node.output_schema):
            actions.append({"policy": p, "reason": "schema validation failed"})
        if p.name == "low_confidence" and result.confidence is not None:
            if result.confidence < (p.threshold or 0.5):
                actions.append({"policy": p, "reason": "confidence below threshold"})
        if p.name == "test_failure" and result.metrics.get("tests_failed", 0) > 0:
            actions.append({"policy": p, "reason": "unit tests failed"})
    return actions
```

### 10.2 Patch planner

```python
def plan_patch(action, state):
    name = action["policy"].action
    if name == "repair_node":
        return GraphPatch(
            op="add_node",
            node=make_repair_node(state.last_failed_node),
            edges=[...]
        )
    if name == "add_reviewer":
        return GraphPatch(op="add_node", node=make_reviewer_node(), edges=[...])
    if name == "terminate_with_uncertainty":
        return GraphPatch(op="terminate", reason=action["reason"])
```

### 10.3 Gate limits

Every gate must have `max_activations`. There should also be global limits:

```yaml
gates:
  max_total_activations: 3
  max_same_node_repairs: 2
  terminate_on_budget_fraction: 0.9
```

---

## 11. Benchmark evaluator implementation

### 11.1 Unified interface

```python
class BenchmarkTask(BaseModel):
    task_id: str
    prompt: str
    reference: Any
    metadata: dict

class BenchmarkEvaluator(Protocol):
    def load(self, split: str) -> list[BenchmarkTask]: ...
    def evaluate_one(self, task: BenchmarkTask, prediction: Any) -> dict: ...
    def aggregate(self, rows: list[dict]) -> dict: ...
```

### 11.2 Result table output

One row per task:

```csv
dataset,task_id,split,model,blueprint_id,skills,final_answer,score,cost_usd,latency_s,tokens_in,tokens_out,gate_count,error_type
```

Save all raw model outputs separately for later inspection.

---

## 12. CLI design

### 12.1 Compile a single task

```bash
agentcoop compile \
  --task-file examples/task.json \
  --config configs/models.yaml \
  --out runs/demo_blueprint.json
```

### 12.2 Execute a single task

```bash
agentcoop run \
  --blueprint runs/demo_blueprint.json \
  --out runs/demo_run
```

### 12.3 Run a benchmark

```bash
agentcoop benchmark \
  --dataset gsm8k \
  --split test \
  --config configs/benchmarks/aflow_aligned.yaml \
  --limit 100 \
  --out runs/gsm8k_debug
```

### 12.4 Wrap a repo

```bash
agentcoop repo wrap \
  --repo https://github.com/Genentech/SpatialAgent \
  --commit <sha> \
  --name SpatialAgent \
  --out agentcoop/wrappers/spatialagent/manifest.yaml
```

### 12.5 Run a repo smoke test

```bash
agentcoop repo smoke \
  --manifest agentcoop/wrappers/spatialagent/manifest.yaml \
  --input examples/spatial_smoke/request.json \
  --out runs/spatial_smoke
```

---

## 13. Safety implementation checklist

- [ ] All repo execution defaults to `--network none`.
- [ ] Containers run as non-root users.
- [ ] Inputs are mounted read-only.
- [ ] Output paths are restricted to `/outputs`.
- [ ] privileged mode / host PID / host network are disabled.
- [ ] CPU, memory, pids, and timeout are limited.
- [ ] Secrets are not written to files, prompts, stdout, or stderr.
- [ ] Any network access, external upload, deletion, or system-path write triggers a human approval gate.
- [ ] Image digest, commit SHA, and package versions are saved.
- [ ] Model-generated code uses allowlisted command execution.

---

## 14. Test plan

### 14.1 Unit tests

- Task profiler returns correct outputs for typical inputs.
- Skill registry can retrieve by tag.
- Compiler can compile a single/math graph for GSM8K and a code-test-repair graph for HumanEval.
- Gate evaluator can trigger schema/test/confidence/tool errors.
- Blackboard permissions are enforced.

### 14.2 Integration tests

- 10-task GSM8K debug run.
- 5-task HumanEval run with sandbox tests.
- 5-task HotpotQA retrieval mock.
- 5-task DROP numeric formatter.
- One fake repo wrapper smoke test.

### 14.3 Repo smoke tests

- `import package` succeeds.
- Minimal command finishes.
- `result.json` is produced.
- Behavior is as expected in no-network mode.
- When an API key is required but missing, the system returns a clear error instead of hanging.

### 14.4 Regression tests

After every compiler or skill change, run a fixed 50-task suite:

- 10 GSM8K
- 10 MATH
- 10 HumanEval / MBPP
- 10 HotpotQA
- 10 DROP

Record score, cost, latency, route distribution, and gate count. If performance changes beyond a threshold, require human review.

---

## 15. Development milestones

### Milestone 1: Executable IR

- Complete schema, skill registry, runtime, and tracing.
- Manually write a blueprint that runs one LLM node and one Python node.

### Milestone 2: Basic compiler

- Implement TaskProfiler.
- Implement 6 meta-skills: direct, single tool, math, QA, DROP, and code repair.
- Automatically generate blueprints for standard benchmarks.

### Milestone 3: AFlow-aligned benchmark v0

- Download and process the six datasets.
- Implement evaluators.
- Run the debug set and 20% validation.

### Milestone 4: Gated refinement

- Implement four gates: schema invalid, test failure, low confidence, and evidence missing.
- Run ablations: no gate vs gated repair.

### Milestone 5: Repo sandbox

- Implement repo wrapper, Docker build, smoke test, and manifest registry.
- Wrap a lightweight toy repo first, then SpatialAgent / BioDiscoveryAgent.

### Milestone 6: Specialized collaboration

- SpatialBench canonical examples.
- BioDiscoveryAgent official datasets.
- Cross-repo pilot task, clearly labeled as exploratory.

---

## 16. Implementation cautions

1. **Do not treat multi-agent as the default selling point**: the paper should show that single-agent fallback is better or cheaper on simple tasks.
2. **Do not claim that metrics are unnecessary for evaluation**: AgentCo-Op does not need metrics to “design topology,” but experiments still need metrics to evaluate outcomes.
3. **Do not let dynamic refinement become implicit search**: limit patch types, patch counts, and trigger conditions; emphasize local repair.
4. **Do not directly run arbitrary GitHub repos**: pin commits, sandbox execution, run smoke tests, and record dependencies.
5. **Do not let the reviewer replace deterministic graders**: objective metrics such as code tests, exact match, and F1 take priority.
6. **Do not let memory become unbounded context**: memory is typed artifacts + summaries + traces, not the full chat history.
7. **Do not ignore cost and latency**: the main risks of multi-agent systems are cost and delay; every table should report tokens / dollars / wall time.
8. **Do not report only average score**: analyze route distribution, gate rescue rate, and failure types, otherwise the “compilation” mechanism cannot be validated.
9. **Be careful with biology collaboration**: BioDiscoveryAgent and SpatialAgent do not naturally share a single standard benchmark; keep verifiable experiments separate from exploratory demos.

---

## 17. References

- AFlow: https://arxiv.org/abs/2410.10762
- AFlow code: https://github.com/FoundationAgents/AFlow
- ADAS: https://arxiv.org/abs/2408.08435
- ADAS code: https://github.com/ShengranHu/ADAS
- Anthropic, Building effective agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic MCP: https://www.anthropic.com/news/model-context-protocol
- Anthropic Claude Code subagents: https://docs.anthropic.com/en/docs/claude-code/sub-agents
- Anthropic Agent Skills: https://docs.anthropic.com/en/docs/claude-code/skills
- OpenAI, A practical guide to building agents: https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf
- OpenAI Agents SDK: https://platform.openai.com/docs/guides/agents-sdk
- OpenAI Sandbox Agents: https://platform.openai.com/docs/guides/sandbox-agents
- BioDiscoveryAgent: https://arxiv.org/abs/2405.17631
- BioDiscoveryAgent code: https://github.com/snap-stanford/BioDiscoveryAgent
- SpatialAgent: https://github.com/Genentech/SpatialAgent
- SpatialBench: https://github.com/Genentech/SpatialBench
