# AgentCo-Op v2 — module interface contract

This document is the coordination contract for the v2 rebuild. The typed IR in
`agentcoop/ir/` is already written and is **authoritative**: read it before
implementing anything here. Every subsystem below compiles against it.

## Non-negotiable constraints

1. **No LLM is required for any core mechanism.** Compilation, static analysis,
   localization, diagnosis, patch proposal, shadow validation, and evaluation
   must all work deterministically with zero API keys. An LLM may only ever be
   an *optional refinement layer*, and anything it produces enters the system as
   `EvidenceStrength.ASSERTED`, which is never load-bearing.
2. **Determinism.** No `random` without an explicit seed, no wall-clock in
   decisions, no dict-iteration-order dependence. Same inputs → same outputs.
3. **Dependencies are frozen**: pydantic v2, typer, rich, networkx, pyyaml,
   jsonschema, httpx, python-dotenv. `numpy`/`pandas` only inside `bench` and
   only behind a guarded import. Do not add dependencies.
4. **Every module ships tests** under `tests/unit/test_<module>.py`. Tests must
   run offline in under a second each.
5. **Stay in your lane.** Only create/modify files under your assigned paths.
   Never edit `agentcoop/ir/` — if the IR seems wrong, note it in your report.
6. Python ≥ 3.11, `from __future__ import annotations`, full type annotations,
   `model_config = ConfigDict(extra="forbid")` on new pydantic models.

## Shared vocabulary

- **Component** — anything invocable: a repo, a container, a Python function, an
  LLM, a coding agent, a human. Described by a `CapabilityCard`.
- **Subgoal** — a typed unit of required work, from the dossier.
- **Term** — a node of the workflow grammar. **ExecGraph** — its lowering.
- **Signal** — an observation. **Check** — an evaluated assertion.
  **Hypothesis** — a candidate root cause. **Patch** — a proposed change.
- A component is only bindable to a subgoal if
  `card.certification_level >= subgoal.min_certification`.

---

## 1. `agentcoop/components/` — invocation adapters

`base.py` defines what everything else depends on:

```python
class Invocation(BaseModel):
    component: str
    subgoal_id: str | None
    inputs: dict[str, Artifact]          # keyed by artifact type name
    config: dict[str, Any]
    workdir: Path | None
    limits: ResourceLimits | None
    seed: int = 0                        # determinism control

class InvocationResult(BaseModel):
    ok: bool
    outputs: dict[str, Artifact]         # keyed by artifact type name
    cost: CostProfile
    logs: str = ""
    errors: list[str] = []
    exit_code: int | None = None
    #: Facets actually observed on emitted artifacts; probes promote these
    #: into the capability card. Observation beats documentation.
    observed_facets: dict[str, FacetSet] = {}

class ComponentAdapter(Protocol):
    name: str
    async def invoke(self, inv: Invocation) -> InvocationResult: ...

class AdapterRegistry:
    def register(self, adapter: ComponentAdapter) -> None
    def get(self, name: str) -> ComponentAdapter
    def has(self, name: str) -> bool
    def names(self) -> list[str]
```

Adapters, each in its own module:

| Module | Class | Notes |
|---|---|---|
| `python_fn.py` | `PythonFunctionAdapter` | wraps a callable; the deterministic workhorse |
| `subprocess_adapter.py` | `SubprocessAdapter` | runs a command, collects declared output files, enforces timeout, captures exit code |
| `container.py` | `ContainerAdapter` | `docker run`; must degrade to a clear `ok=False` with a `FaultClass.ENVIRONMENT`-shaped error when Docker is absent — never silently fall back to the host |
| `coding_agent.py` | `CodingAgentAdapter` | headless `codex exec` / `claude -p`; **this is the Codex/Claude-Code-as-executor path**, see §10 |

Every adapter records `observed_facets` where it can. `ContainerAdapter` and
`CodingAgentAdapter` must be importable and unit-testable without Docker or the
CLIs present, which is why each exposes a pure `plan()`.

Four further adapters were specified and built — `llm.py`, `human.py`,
`converter.py`, `evaluator.py` — and then deleted for the reason given in §2–3:
nothing called them and nothing tested them. `evaluator.py` in particular
existed only to dynamically import the withdrawn `evaluate` package. Reinstate
them when there is a caller, not before.

---

## 2–3. `agentcoop/evaluate/` and `agentcoop/memory/` — WITHDRAWN

Both packages were specified here, implemented, and then deleted. The reason is
worth recording, because the same mistake is easy to repeat.

`evaluate/` was specified as a six-level contract stack with its own check
registry and its own metrics module. What got built duplicated work that the
wired pipeline already did: `metrics.py` reimplemented localization accuracy,
detection recall, repair success, and collateral-regression rate, all of which
`bench/harness.py` computes from the run record; `contract.py` re-declared
`payload_is_empty` and the trace accessors from `components/base.py`, against
`Any`-typed duck-typing rather than the real models. Nothing in the pipeline
ever imported it — `ExecutionEngine` accepted a `check_registry` argument and
never read it — so 5,192 lines existed only to be unit-tested.

The *idea* survives where it belongs: `CheckLevel` in `ir/checks.py` defines the
six levels, `CheckStatus.UNAVAILABLE` is never `PASS`, and
`bench/harness.contract_summary` reports coverage and pass rate per level for
tasks with no scalar oracle. That is the whole load-bearing content of the
section this replaces.

`memory/` was specified as reliability statistics and outcome evidence. Nothing
supplied it and nothing consumed it. `ReliabilityPosterior` lives in
`ir/capability.py`, where the certification ladder actually reads it to decide
`TRUSTED`, and `EvidenceLedger.observed_fraction` covers outcome evidence.

**If either is reintroduced, wire it before writing it.** A subsystem with no
caller cannot be kept honest by its own tests.

---

## 4. `agentcoop/probe/` — executable capability certification

```python
class ProbeSpec(BaseModel):
    probe_id: str
    kind: Literal["reachable","schema","smoke","invalid_input","determinism","resource"]
    description: str
    inputs: dict[str, Artifact]
    expectations: dict[str, Any]
    timeout_s: float = 60.0

def standard_suite(card: CapabilityCard, *, registry: TypeRegistry) -> list[ProbeSpec]

class ProbeRunner:
    def __init__(self, adapters: AdapterRegistry, *, registry: TypeRegistry)
    async def run_probe(self, card, spec) -> ProbeOutcome
    async def certify(self, card, specs=None) -> CapabilityCard   # returns a NEW card

def certification_report(card: CapabilityCard) -> CheckReport
def explain_level(card: CapabilityCard) -> str   # why the level is what it is
```

The `invalid_input` probe is the important one: it must distinguish a component
that *rejects* bad input from one that returns exit code 0 with empty or
plausible-looking output. A component that silently succeeds on garbage gets
`FailureSignature(silent=True)` recorded and cannot reach `CERTIFIED`.

The `determinism` probe runs twice with the same seed and compares content
hashes, writing the result into `BehaviorContract.deterministic`.

---

## 5. `agentcoop/compile/` — grammar-constrained synthesis

### `grammar.py`

```python
class RuleContext(BaseModel):
    dossier, library, type_registry, statistics, merges

class RuleVerdict(BaseModel):
    allowed: bool
    evidence: list[EvidenceItem]
    reason: str

def can_bind(component, subgoal, ctx) -> RuleVerdict
def can_sequence(upstream_term, downstream_term, ctx) -> RuleVerdict
def can_parallelize(branches, ctx) -> RuleVerdict
def can_join(branches, merge, ctx) -> RuleVerdict
def can_verify(body, verifier, ctx) -> RuleVerdict
def can_fallback(primary, alternate, ctx) -> RuleVerdict
def can_human_gate(body, condition, ctx) -> RuleVerdict

class MergeSpec(BaseModel): name, applies_to: str, semantics: str, fn_ref: str
class MergeRegistry: register/get/for_type
```

Applicability conditions, stated precisely — these are the research content:

- **bind**: component certified at or above the subgoal's `min_certification`;
  produces every artifact the subgoal requires with compatible facets; consumes
  nothing the subgoal cannot supply.
- **sequence**: downstream consumes at least one artifact type upstream
  produces, and `check_compatibility` is `COMPATIBLE` or `NEEDS_ADAPTER`.
- **parallelize**: no branch consumes an artifact any other branch produces;
  no shared mutable state; **and** at least one of — distinct artifact types
  (complementary observation), distinct modality facets, or recorded error
  decorrelation. Complexity alone is never sufficient.
- **join**: a `MergeSpec` is registered for the branches' common output type.
  No merge algebra ⇒ no join. Voting is not a default.
- **verify**: the verifier is available for this dossier
  (`dossier.availability(level) != UNAVAILABLE`).
- **fallback**: both arms produce the same artifact type, and outcome evidence
  shows a non-trivial failure rate for the primary.
- **human_gate**: a matching `HumanReviewCondition` exists in the dossier.

### `requirements.py`

```python
def plan_requirements(dossier) -> RequirementPlan
class RequirementPlan(BaseModel):
    order: list[str]                    # topologically sorted subgoal ids
    producers: dict[str, list[str]]     # artifact type -> subgoals producing it
    consumers: dict[str, list[str]]
    defects: list[str]
```

### `candidates.py`

```python
def enumerate_candidates(dossier, library, ctx, *, max_candidates=32) -> list[CandidateWorkflow]
class CandidateWorkflow(BaseModel):
    candidate_id: str
    term: WorkflowTerm
    ledger: EvidenceLedger
    construction_log: list[str]
```

**Requirement**: whenever a single component covers every subgoal, the
single-component candidate is always enumerated and always carries a
`DECLINE_STRUCTURE` evidence record. The system must be able to conclude "you
do not need a multi-agent workflow here", and must say so explicitly.

### `static_analysis.py`

```python
def analyze(workflow, dossier, library, ctx) -> CheckReport
```

Checks (all pre-execution, all deterministic, each emitting a `CheckResult` with
`subject` set so blame is attributable):

`artifact_reachability`, `edge_type_compatibility` (structural **and** facet,
returning `UNDERSPECIFIED` as a failure not a pass), `environment_conflict`,
`missing_verifier`, `unnecessary_composition`, `provenance_completeness`,
`resource_feasibility`, `cycle_freedom`, `silent_failure_exposure`,
`shared_state_leak`, `join_without_merge`, `unjustified_decision`.

### `select.py` / `compiler.py`

```python
class CompilationResult(BaseModel):
    workflow: CompiledWorkflow | None
    candidates: list[CandidateWorkflow]
    static_reports: dict[str, CheckReport]
    pareto_front: list[str]
    selection_rationale: str
    dossier_defects: list[str]
    rejected: dict[str, str]

class Compiler:
    def compile(self, dossier, library, *, probe_execution=None,
                policy: SelectionPolicy | None = None) -> CompilationResult
```

Order of operations: dossier defects → enumerate → justify → static analysis →
drop candidates failing blocking static checks → estimate utility (static
estimate, or real probe-execution utilities when supplied) → Pareto → select.

---

## 6. `agentcoop/execute/` — typed execution with lineage

```python
class TraceEvent(BaseModel): event_id, step, kind, node_id, payload, timestamp_index
class Trace(BaseModel):
    run_id: str
    events: list[TraceEvent]
    artifacts: dict[str, Artifact]
    node_results: dict[str, InvocationResult]
    def lineage(self, artifact_id) -> list[str]
    def producer_of(self, artifact_id) -> str | None
    def artifacts_of(self, node_id) -> list[Artifact]

class ExecutionEngine:
    def __init__(self, adapters, *, type_registry, check_registry=None)
    async def run(self, workflow, dossier, inputs, *, limits, seed=0) -> ExecutionResult

class ExecutionResult(BaseModel):
    ok, trace, outputs, cost, report: CheckReport, terminated_reason
```

Requirements: validate every artifact against its declared type **at the edge**
before the consumer runs, and emit an `ARTIFACT` check for each handoff; honour
`conditional`/`guard` nodes so `Fallback` works; enforce budget; support
`snapshot()` for shadow validation; record every artifact with `derived_from`
populated, because localization depends on it.

Timestamps use a monotonic step counter, not wall clock, so traces diff cleanly.

---

## 7. `agentcoop/diagnose/` — detect, localize, diagnose

```python
class Signal(BaseModel):
    signal_id, kind, subject, subject_kind, severity, source: SignalSource,
    detail, evidence: dict

class Detector(Protocol):
    name: str
    def detect(self, trace, report, workflow, dossier) -> list[Signal]
```

Built-in detectors: `check_failure`, `schema_violation`, `facet_violation`,
`tool_error`, `timeout`, `empty_output`, `cost_anomaly`, `branch_disagreement`,
`missing_evidence`, `progress_stall`, `distribution_shift`.

```python
class Localization(BaseModel):
    subject: str | None
    subject_kind: BlameTarget
    slice_path: list[str]        # backward slice, failure first
    earliest_anomalous_artifact: str | None
    rationale: str

def localize(trace, signals, workflow, type_registry) -> Localization
```

Localization is **backward slicing over artifact lineage**: start from the
failing assertion, walk `derived_from` backwards, re-run each artifact's own
validity check, and blame the earliest artifact that fails its own check. If
every upstream artifact is valid, blame the node itself. This is the mechanism
that stops a downstream agent being punished for consuming a bad upstream
artifact.

```python
class Diagnoser:
    def __init__(self, *, statistics=None, llm=None)
    def diagnose(self, signals, localization, workflow, dossier, library) -> Diagnosis
```

Scoring is rule-based and explainable: each `(signal kind, blame target)` pair
contributes likelihood to fault classes, priors come from
`statistics.failure_distribution(component)`, and the result is normalized into
a ranked `Diagnosis`. When `Diagnosis.entropy` exceeds the repair policy
threshold, the correct output is "gather more evidence", not a guess. If an LLM
is supplied it may *reorder within* the top-k and add rationale text; it may not
introduce a hypothesis that no signal supports.

---

## 8. `agentcoop/repair/` — propose, shadow-validate, commit or roll back

```python
class Patch(BaseModel):
    patch_id: str
    family: str                       # must be in FAULT_TAXONOMY admissible list
    tier: RepairTier
    target: str                       # term id / node id / edge id
    description: str
    rationale: str
    hypothesis: FaultHypothesis

class PatchApplication(Protocol):
    def apply(self, workflow: CompiledWorkflow) -> CompiledWorkflow   # PURE
```

Patch families (each cause-specific, each registered against the fault classes
that admit it): `retry_node`, `backoff_retry`, `insert_adapter`,
`replace_component`, `add_verifier`, `define_merge`, `parallelize`,
`serialize_branches`, `isolate_state`, `invalidate_cache`, `pin_environment`,
`isolate_environment`, `raise_resource_limit`, `retune_config`,
`rewrite_prompt`, `add_fallback`, `add_specialist`, `decompose_subgoal`,
`diversify_evaluator`, `add_deterministic_check`, `add_human_gate`,
`tighten_contract`, `add_information_edge`, `report_uncertainty`,
`clarify_specification`.

```python
def propose(diagnosis, workflow, dossier, library, ctx) -> list[Patch]
```

**Hard rule**: `propose` may only emit patches whose family is in
`hypothesis.spec.admissible_patches`. A namespace mismatch can never be
"repaired" by raising a temperature. Assert this in tests.

```python
class ShadowResult(BaseModel):
    ok, delta_utility: dict[Objective, float], collateral_regressions: list[str],
    fixed_original_failure: bool, cost: CostProfile, notes: list[str]

class ShadowValidator:
    async def validate(self, patch, workflow, dossier, harness, *, budget) -> ShadowResult
```

Shadow validation runs the patched workflow on reduced input, in a snapshot, and
requires **both** that the original failure is gone **and** that no previously
passing check now fails. A patch that only makes the test pass while degrading
evidence coverage, provenance, or cost is rejected.

Components whose `BehaviorContract.shadow_safe` is False (irreversible side
effects) must not be shadow-executed; the validator returns
`ok=False, notes=["not shadow-safe"]` and the transaction escalates instead.

```python
class RepairTransaction(BaseModel):
    transaction_id, pre_state_key, patch, shadow: ShadowResult | None,
    committed: bool, rolled_back: bool, reason: str

class RepairLoop:
    async def run(self, workflow, dossier, library, execution_result, *, harness) -> RepairOutcome
```

The loop is bounded by `RepairPolicy` **per tier**, and escalates
contract-repair → local-optimization → global-redesign when the same fault
class recurs `escalate_after_repeats` times. Every transaction is logged with
its rollback point.

---

## 9. `agentcoop/bench/` — task suites, fault injection, baselines

```python
class BenchTask(BaseModel):
    task_id, dossier, component_specs, inputs,
    ground_truth: GroundTruth | None,
    injected_faults: list[InjectedFault],
    regime: Literal["single_sufficient","multi_necessary","multi_harmful"],
    horizon: Literal["short","medium","long"]

class InjectedFault(BaseModel):
    fault_id, fault_class: FaultClass, target, mechanism, expected_blame: str
```

`faults.py` injects a *known* fault (namespace corruption on an edge, dependency
break, silent empty output, stale cache, miscalibrated grader, needless
serialization) and records the ground-truth blame target, so localization and
diagnosis accuracy are measurable rather than asserted.

`baselines.py` implements, at minimum: `BestSingleComponent`,
`EqualBudgetSingleAgent`, `SameWorkflowSingleAgent` (one model executing the
same steps sequentially — the OneFlow-style counterfactual), and
`CodingAgentBaseline` (Codex / Claude Code given the same task + repos).

The three `regime` values matter: the benchmark must contain tasks where
composition is *harmful*, so that "AgentCo-Op correctly declines to build a
multi-agent workflow" is a measurable success rather than a claim.

---

## 10. Positioning: coding agents as executors, not rivals

`CodingAgentAdapter` exists so a general coding agent can be a *node* inside a
compiled workflow. The comparison harness must support all four arms:

1. coding agent alone, given the task and the repos;
2. coding agent given AgentCo-Op's capability cards, typed contracts, and
   compiled blueprint;
3. AgentCo-Op using the coding agent as its executor backend;
4. AgentCo-Op with native adapters.

Arm 2 is the one that shows the artifacts carry the value; arm 3 shows the
layers compose rather than compete.

---

## 11. CLI surface (`agentcoop/cli.py`)

```
agentcoop dossier lint <dossier.yaml>          # specification defects
agentcoop probe <library.yaml> [--component X] # certify components
agentcoop compile <dossier.yaml> --library L   # candidates, evidence, Pareto, choice
agentcoop explain <workflow.json>              # the design-evidence report
agentcoop run <workflow.json> --inputs I       # execute
agentcoop diagnose <run-dir>                   # detect/localize/diagnose an existing run
agentcoop repair <run-dir>                     # bounded transactional repair
agentcoop bench run <suite> --system <sut>     # benchmark harness
```

`agentcoop explain` is a first-class command: it prints, for every node and edge,
the design evidence that justifies it, the alternatives considered, and the
rejection reasons. If the system cannot explain a decision, that is a bug.
