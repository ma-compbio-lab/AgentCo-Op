# ECPS Preference Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an execution-grounded, non-gradient preference optimizer that resolves admissible workflow fronts through rubric/scorepad judgments without turning judge output into scientific evidence.

**Architecture:** Materialize every admissible compiler candidate, run candidates under matched cases, construct a compatible observed Pareto front, and pass only that front to an anonymized order-swapped judge panel. Fit independent regularized Bradley–Terry models per dossier preference, actively compare uncertain pairs, optionally enumerate one-key configuration children, and return either a stable winner or an unresolved front with a complete audit archive.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio, standard-library math/hash/json, pytest, pytest-asyncio. No new dependency or live provider.

---

## File map

| File | Responsibility |
|---|---|
| `docs/architecture/ecps_preference_search.md` | Approved architecture, invariants, state machine, and test contract. |
| `agentcoop/ir/preference.py` | Immutable rubric, scorepad, ordered judgment, judge/policy observations, and archive records. |
| `agentcoop/ir/__init__.py` | Export the new persistent preference records. |
| `agentcoop/compile/compiler.py` | Retain every justified, statically admissible compiled candidate. |
| `agentcoop/probe/suite.py` | Build executable schema/smoke/resource suites over certified config domains. |
| `agentcoop/probe/runner.py` | Stamp parameter-domain scope evidence only from real probe verdicts. |
| `agentcoop/probe/__init__.py` | Export the parameter-domain suite helper. |
| `agentcoop/optimize/state.py` | Cases, candidate evaluations, budgets, policy, events, stop reasons, and outcome. |
| `agentcoop/optimize/packets.py` | Deterministic anonymized execution views and reference validation. |
| `agentcoop/optimize/judge.py` | Judge protocol, component adapter bridge, order swapping, panel, and verbosity guard. |
| `agentcoop/optimize/preference.py` | Pure-Python criterion-wise Bradley–Terry fit, dominance, cycles, and active acquisition. |
| `agentcoop/optimize/mutations.py` | Pure one-key config mutation, fingerprints, and finite deterministic mutation source. |
| `agentcoop/optimize/loop.py` | Execution-coupled objective gate and ECPS state machine. |
| `agentcoop/optimize/__init__.py` | Explicit public API. |
| `tests/unit/test_ir_preference.py` | Persistent protocol validation and order-swap truth table. |
| `tests/unit/test_optimize_packets.py` | Packet redaction, references, hashing, and malformed data. |
| `tests/unit/test_optimize_judge.py` | Adapter failures, frozen rubric, panel diversity, position bias, verbosity. |
| `tests/unit/test_optimize_preference.py` | Fit, uncertainty, cycles, dominance, acquisition, stability. |
| `tests/unit/test_optimize_mutations.py` | Mutation purity, identity, deterministic finite grids. |
| `tests/unit/test_probe.py` | Executable parameter-domain evidence generation. |
| `tests/unit/test_optimize_loop.py` | Admission, utility/front, budgets, transitions, stop reasons. |
| `tests/integration/test_preference_optimization.py` | Compiler archive through selected/unresolved workflow. |

## Task 1: Persistent rubric, scorepad, and observation IR

**Files:**
- Create: `agentcoop/ir/preference.py`
- Modify: `agentcoop/ir/__init__.py`
- Test: `tests/unit/test_ir_preference.py`

- [ ] **Step 1: Write failing model and protocol tests**

Cover strict fields, frozen rubric records, finite score ranges, unique criteria,
stable fingerprints, complete scorepads, reference requirements, derived final
verdicts, JSON round trips, and every order-swap row. The wished-for API is:

```python
meta = MetaRubric.from_dossier(dossier, version="v1")
rubric = PairRubric(
    rubric_id="pair-rubric",
    version="v1",
    meta_rubric=meta.ref(),
    criteria=(
        RubricCriterion(
            criterion_id="clarity-op",
            preference_id="clarity",
            description="Prefer the clearer actionable result.",
            direction=Direction.MAXIMIZE,
        ),
    ),
)
forward = OrderedPairJudgment(
    judgment_id="j:f",
    pair_id="p",
    case_id="case",
    left_packet_id="pa",
    right_packet_id="pb",
    judge_id="judge",
    judge_family="family",
    source=SignalSource.LLM_JUDGE,
    rubric=rubric,
    scorepad=Scorepad(entries=(
        ScorepadEntry(
            criterion_id="clarity-op",
            preference_id="clarity",
            verdict=PairwiseVerdict.LEFT,
            left_score=8.0,
            right_score=6.0,
            evidence_refs=("pa:artifact:answer",),
            confidence=0.8,
            rationale="The left result states an executable recommendation.",
        ),
    )),
)
observations = normalize_order_swaps(
    forward,
    reverse,
    packet_to_candidate={"pa": "candidate-a", "pb": "candidate-b"},
)
assert observations[0].preferred_candidate_id == "candidate-a"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin tests/unit/test_ir_preference.py -v
```

Expected: collection fails because `agentcoop.ir.preference` does not exist.

- [ ] **Step 3: Implement minimal persistent models**

Implement these exact public names:

```python
class PairwiseVerdict(str, Enum): LEFT, RIGHT, TIE, ABSTAIN
class ObservationStatus(str, Enum): DIRECTIONAL, TIE, ABSTAIN, UNRESOLVED
class VerbosityMode(str, Enum): NONE, AUTO_LOSS
class VerbosityPolicy(BaseModel): mode, baseline_candidate_id, sigma
class RubricRef(BaseModel): rubric_id, version, content_hash
class MetaCriterion(BaseModel): preference_id, description, dimension, direction
class MetaRubric(BaseModel): rubric_id, task_id, version, criteria
class RubricCriterion(BaseModel): criterion_id, preference_id, description, direction
class PairRubric(BaseModel): rubric_id, version, meta_rubric, criteria
class ScorepadEntry(BaseModel): criterion_id, preference_id, verdict, left_score,
    right_score, evidence_refs, confidence, missing_evidence, rationale
class Scorepad(BaseModel): entries
class OrderedPairJudgment(BaseModel): judgment_id, pair_id, case_id,
    left_packet_id, right_packet_id, judge_id, judge_family, source, rubric,
    scorepad, notes
class PairwisePreferenceObservation(BaseModel): observation_id, pair_id,
    case_id, preference_id, candidate_a_id, candidate_b_id, status,
    preferred_candidate_id, judge_id, judge_family, source, rubric, forward,
    reverse, confidence
class PolicyObservationStatus(str, Enum): DIRECTIONAL, ABSTAIN
class PolicyPreferenceObservation(BaseModel): observation_id, pair_id, case_id,
    preference_ids, candidate_a_id, candidate_b_id, status,
    preferred_candidate_id, source, policy, baseline_candidate_id,
    candidate_a_length, candidate_b_length, baseline_length, threshold, reason
class JudgeCallStatus(str, Enum): AVAILABLE, UNAVAILABLE, INVALID
class JudgeCallRecord(BaseModel): call_id, status, request_id, judge_id,
    judge_family, source, judgment, cost, cost_complete, error
class PreferenceArchive(BaseModel): calls, judgments, observations,
    policy_observations, notes
def normalize_order_swaps(
    forward: OrderedPairJudgment,
    reverse: OrderedPairJudgment,
    *,
    packet_to_candidate: Mapping[str, str],
) -> tuple[PairwisePreferenceObservation, ...]
```

Use canonical `model_dump(mode="json")` with sorted-key JSON for every hash.
`OrderedPairJudgment.final_verdict()` must implement criterion Pareto logic and
must not read `Preference.weight_hint` or sum scores. A judge observation
requires both order-swapped judgments. A policy observation requires
`SignalSource.DETERMINISTIC`, has no judge identity/judgments, and is validated
independently.

- [ ] **Step 4: Verify GREEN and regression safety**

Run the focused test, then:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin tests/unit/test_ir_dossier.py tests/unit/test_ir_evidence.py tests/unit/test_ir_utility.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agentcoop/ir/preference.py agentcoop/ir/__init__.py tests/unit/test_ir_preference.py
git commit -m "[feat] add auditable preference protocol IR"
```

## Task 2: Anonymous packets and enforced judge panel

**Files:**
- Create: `agentcoop/optimize/__init__.py`
- Create: `agentcoop/optimize/packets.py`
- Create: `agentcoop/optimize/judge.py`
- Test: `tests/unit/test_optimize_packets.py`
- Test: `tests/unit/test_optimize_judge.py`

- [ ] **Step 1: Write failing packet tests**

Construct real `ExecutionResult`, `Trace`, `Artifact`, and `CheckResult` models.
Assert that two equivalent runs create byte-equivalent packet IDs; workflow,
component, provider, and incumbent labels are absent from system metadata; all
scorepad references resolve; and changed output content changes the packet
hash. Embed identity sentinels in check ID/subject/summary/evidence, artifact
ID/producer/path/notes/raw content hash/undeclared facets, and lineage, and
assert none occurs in serialized metadata. Change only those identity fields
and assert the packet ID is unchanged. Separately show that candidate payload
text is preserved as explicitly untrusted data.

The API is:

```python
view = build_candidate_view(
    dossier, execution, case_id="dev-0", max_payload_chars=20_000
)
assert view.packet_id == build_candidate_view(dossier, execution, case_id="dev-0").packet_id
assert validate_evidence_refs((f"{view.packet_id}:trace",), (view,)) == []
```

- [ ] **Step 2: Verify packet RED, implement, and verify GREEN**

`CandidateView` must contain only dossier task text, output views, objective
allowlisted checks, a canonical trace digest, resources, output length, and
public reference IDs. Filter checks whose level is `PREFERENCE` or whose source
is `LLM_JUDGE`/`HUMAN`; include no raw check ID, subject, summary, evidence,
artifact ID, producer, path, note, or lineage ID. Safe canonicalization accepts
JSON-native values, finite numbers, tuples, and base64-tagged bytes. It never
dereferences a path or serializes arbitrary `repr()`. Unsupported, non-finite,
path-only, or over-limit content becomes a symmetric unavailable marker plus
public content fingerprint. Copy only facet keys declared by the dossier's
artifact/subgoal output contract and re-hash any stored content hash before it
becomes public. Coalesce identical safe checks with an `occurrences` count so
refs never depend on raw-ID ordering. Test each case. Set `output_length=None` whenever a required
output lacks a substantive safe snapshot; marker length never counts as
verbosity. Never interpolate untrusted payload into protocol instructions.

- [ ] **Step 3: Write failing judge and panel tests**

Use deterministic in-test judges. Test forward/reverse calls, frozen rubric,
always-left positional bias, two-family agreement, third-family invocation only
on disagreement or unusable primary coverage, unavailable exceptions,
insufficient usable diversity, and malformed references. Test the existing
adapter boundary through an in-test registered component.

Required interfaces:

```python
class CandidateView(BaseModel):
    packet_id: str
    task_id: str
    case_id: str
    goal: str
    context: str
    outputs: tuple[OutputView, ...]
    checks: tuple[PacketCheck, ...]
    trace: TraceDigest
    resources: CostProfile
    output_length: int | None
    reference_ids: tuple[str, ...]

class JudgeRequest(BaseModel):
    request_id: str
    pair_id: str
    case_id: str
    seed: int
    meta_rubric: MetaRubric
    left: CandidateView
    right: CandidateView
    frozen_rubric: PairRubric | None = None

class PreferenceJudge(Protocol):
    judge_id: str
    family: str
    source: SignalSource
    async def compare(self, request: JudgeRequest) -> JudgeResponse: ...

class JudgeResponse(BaseModel):
    judgment: OrderedPairJudgment
    cost: CostProfile

class ComponentJudgePayload(BaseModel):
    judgment: OrderedPairJudgment
    reported_cost: CostProfile | None = None  # accepted for audit, never trusted

class ComponentPreferenceJudge:
    def __init__(
        self, registry: AdapterRegistry, component: str, *, judge_id: str,
        family: str, source: SignalSource = SignalSource.LLM_JUDGE,
        request_type: str = "agentcoop.preference_judge_request",
        response_type: str = "agentcoop.preference_judge_response",
        limits: ResourceLimits | None = None,
    ) -> None: ...
    def plan(self, request: JudgeRequest) -> Invocation: ...
    async def compare(self, request: JudgeRequest) -> JudgeResponse: ...

class JudgePanel:
    async def compare(
        self, *, pair_id, case_id, candidate_a_id, candidate_b_id,
        view_a, view_b, meta_rubric, seed, remaining_calls,
    ) -> PanelResult: ...

class PanelResult(BaseModel):
    pair_id: str
    case_id: str
    calls: tuple[JudgeCallRecord, ...]
    judgments: tuple[OrderedPairJudgment, ...]
    observations: tuple[PairwisePreferenceObservation, ...]
    judge_calls: int
    cost: CostProfile
    cost_complete: bool
    notes: tuple[str, ...]
```

- [ ] **Step 4: Verify judge RED, implement, and verify GREEN**

The reverse call uses swapped views and the exact first-call `PairRubric`.
Adapter-stamp judge ID, family, and source rather than trusting returned data.
Catch adapter/protocol failures as audited unavailable calls. Never coerce an
unavailable call into tie or a winner. The component bridge must put the
request seed on `Invocation.seed`. It stamps `JudgeCallRecord.cost` from
`InvocationResult.cost` and ignores cost-like fields in the judgment artifact;
`ComponentJudgePayload.reported_cost` is discarded and never used for
accounting. `plan()` uses the configured component/request type/response type
exactly as specified in the architecture; response payload is an in-memory
mapping or JSON string, never a dereferenced path. Native test judges report
cost through `JudgeResponse`, not the judgment. Reject every negative or
non-finite authoritative cost, mark accounting incomplete, and allow the loop
to stop `RESOURCE_ACCOUNTING_INVALID`. Rebuild stamped Pydantic records through
validation rather than unchecked `model_copy(update=...)`.

- [ ] **Step 5: Commit**

```bash
git add agentcoop/optimize tests/unit/test_optimize_packets.py tests/unit/test_optimize_judge.py
git commit -m "[feat] enforce agentic preference judge protocol"
```

## Task 3: Criterion-wise preference inference and acquisition

**Files:**
- Create: `agentcoop/optimize/preference.py`
- Test: `tests/unit/test_optimize_preference.py`

- [ ] **Step 1: Write failing numerical-behavior tests**

Test a two-candidate unanimous preference, ties, abstentions, disconnected
graphs, a three-candidate cycle, candidate ordering invariance, deterministic
fits, criterion separation, Bonferroni dominance, leave-one-family-out
instability, exactly two agreeing families, anchor exploration, and
maximum-information acquisition. Assert that confidence changes do not change
the fit, policy observations are ignored by model/diversity logic, and an
attempted-but-unavailable pair/case is not scheduled repeatedly. Cover one
fractional tie row, canonical pair orientation, reduced-space covariance,
pair-variance calculation, damping/non-convergence, per-criterion connectivity,
strict-majority cycles of length at least three, simultaneous
`delta/[K(N-1)]` lower bounds, and per-criterion family coverage.

The API is:

```python
model = fit_preference_models(
    candidate_ids=("a", "b"),
    preference_ids=("clarity", "coverage"),
    observations=archive.observations,
    ridge=1.0,
)
front = preference_front(
    model, epsilon={"clarity": 0.0, "coverage": 0.0}, delta=0.1
)
pair = choose_next_comparison(
    candidate_ids=("a", "b", "c"),
    case_ids=("dev-0", "dev-1"),
    archive=archive,
    models=model,
)
```

- [ ] **Step 2: Verify RED**

Run the focused module and confirm the missing import failure.

- [ ] **Step 3: Implement deterministic pure-Python inference**

Define:

```python
class CandidateEstimate(BaseModel): candidate_id, theta, standard_error
class CriterionModel(BaseModel): preference_id, estimates, covariance,
    connected, cycle_detected, converged, iterations
class PreferenceModelSet(BaseModel): models, candidate_ids, notes
class ComparisonTarget(BaseModel): candidate_a_id, candidate_b_id, case_id, acquisition
def fit_preference_models(
    candidate_ids: Sequence[str],
    preference_ids: Sequence[str],
    observations: Sequence[PairwisePreferenceObservation],
    *,
    ridge: float = 1.0,
    max_iterations: int = 100,
    tolerance: float = 1e-8,
) -> PreferenceModelSet
def pair_probability(
    model: CriterionModel, candidate_a: str, candidate_b: str, margin: float = 0.0
) -> float
def preference_dominates(
    models: PreferenceModelSet,
    candidate_a: str,
    candidate_b: str,
    *,
    epsilon: Mapping[str, float],
    delta: float,
) -> bool
def preference_front(
    models: PreferenceModelSet, *, epsilon: Mapping[str, float], delta: float
) -> tuple[str, ...]
def choose_next_comparison(
    candidate_ids: Sequence[str],
    case_ids: Sequence[str],
    archive: PreferenceArchive,
    models: PreferenceModelSet,
) -> ComparisonTarget | None
def stable_selected_candidate(
    candidate_ids: Sequence[str],
    preference_ids: Sequence[str],
    archive: PreferenceArchive,
    *,
    epsilon: Mapping[str, float],
    delta: float,
    min_judge_families: int,
    ridge: float,
    max_iterations: int,
    tolerance: float,
) -> str | None
```

Implement the architecture's exact constrained objective with
`theta=B beta`, one `y=.5` tie row, deterministic Gauss–Jordan information
solves, objective-nondecreasing backtracking, `C=B H_beta^-1 B^T`, and explicit
non-convergence. Use `(e_a-e_b)^T C (e_a-e_b)` for pair variance. Collapse
repeated `(family,pair,case,preference)` keys; exclude inconsistent duplicates,
abstain, unresolved, and policy records. Never weight confidence.

Build connectivity and strict-majority directed graphs per criterion; only
cycles of length at least three block. Implement the exact lower bound
`L=d-z*sqrt(max(v,0))` with `alpha=delta/[K*max(1,N-1)]`, all
`L>=-epsilon`, and at least one `L>epsilon`. Require finite, connected,
converged, cycle-free models. Full-archive family diversity is per criterion;
LOO skips only the count gate and uses the same ridge/iterations/tolerance.

Acquisition uses the finite unattempted target set, bridges disconnected
criterion graphs in dossier order, then maximizes
`max_k p_k(1-p_k)v_k/4` with lexicographic ties. Any recorded panel call marks
the target attempted, including invalid/unavailable, so no target is retried.

- [ ] **Step 4: Verify GREEN and property regressions**

Run the new tests twice and assert identical serialized model output. Run
`tests/unit/test_ir_utility.py` to ensure objective selection is unchanged.

- [ ] **Step 5: Commit**

```bash
git add agentcoop/optimize/preference.py tests/unit/test_optimize_preference.py
git commit -m "[feat] add active criterion-wise preference inference"
```

## Task 4: Executable compiler archive and optimization state

**Files:**
- Modify: `agentcoop/compile/compiler.py`
- Create: `agentcoop/optimize/state.py`
- Test: `tests/unit/test_compile.py`
- Test: `tests/unit/test_optimize_loop.py`

- [ ] **Step 1: Write failing compiler archive tests**

Assert `CompilationResult.workflows` contains exactly justified candidates that
passed blocking static analysis, not rejected candidates, and that the selected
workflow is the same object stored under the selected candidate ID after its
selection-rationale provenance is added.

- [ ] **Step 2: Verify RED, implement archive retention, verify GREEN**

Add:

```python
class CompilationResult(BaseModel):
    workflows: dict[str, CompiledWorkflow] = Field(default_factory=dict)
```

Populate it immediately after justification and static analysis pass. For a
compile-time winner, add selection-rationale provenance to the stored object,
replace that archive entry, and assign that exact object to `result.workflow`
instead of reconstructing a second workflow.

- [ ] **Step 3: Write failing state/policy/budget tests**

Test strict Pydantic validation, deterministic case fingerprints, duplicate case
rejection, invalid budget values, event ordering, budget accounting, and every
typed stop reason.

Implement these exact runtime records in `state.py`:

```python
class OptimizationState(str, Enum): INITIALIZING, EXECUTING,
    OBJECTIVE_GATING, COMPARING, MODELING, MUTATING, STOPPED
class OptimizationStopReason(str, Enum): OBJECTIVE_SINGLETON,
    CONFIDENT_PREFERENCE, VERBOSITY_POLICY_SINGLETON,
    NO_ADMISSIBLE_CANDIDATES, NO_PREFERENCES, NO_CASES,
    INELIGIBLE_EVALUATOR, NO_JUDGES, INSUFFICIENT_JUDGE_DIVERSITY,
    BUDGET_EXHAUSTED, RESOURCE_ACCOUNTING_INVALID, EVALUATOR_UNSTABLE,
    VERBOSITY_BASELINE_UNAVAILABLE, NO_MUTATIONS,
    FRONT_STABLE_UNRESOLVED
class EvaluationCase(BaseModel): case_id, input_fingerprint, seed, limits,
    evaluator_ids, tool_snapshot_id
class CostUnit(str, Enum): USD, TOKENS
class CaseExecution(BaseModel): case_fingerprint, result,
    cost_unit: CostUnit | None = None, latency_measured: bool = False
class OptimizationBudget(BaseModel): max_candidate_executions,
    max_judge_calls, max_candidates, max_generations, soft_max_usd,
    soft_max_tokens, soft_max_execution_latency_s
class OptimizationLedger(BaseModel): candidate_executions, judge_calls,
    execution_cost, judge_cost, cost_accounting_complete, soft_limits_exceeded
class OptimizationPolicy(BaseModel): budget, ridge, delta, epsilon,
    max_iterations, tolerance, min_judge_families, max_payload_chars, verbosity
class OptimizationCandidate(BaseModel): candidate_id, workflow, parent_id,
    mutation_id, generation, static_estimate
class CandidateEvaluation(BaseModel): candidate, executions, feasible,
    rejection_reasons, utility, utility_basis, packet_ids
class OptimizationEvent(BaseModel): index, state, detail, candidate_ids, pair_id
class MutationRecord(BaseModel): mutation_id, parent_id, child_id, target,
    key, value, domain_id, domain_component, domain_values, probe_ids,
    rationale, generation
class OptimizationOutcome(BaseModel): selected_candidate_id, contender_ids,
    schema_version, algorithm_version, dossier_fingerprint, cases, policy, candidates,
    compiler_rejections, packet_archive, archive, model_snapshots,
    mutation_records, objective_front, preference_front, events, ledger,
    state, stop_reason, notes
```

`cost_unit=None` means unknown even when the zero-filled `CostProfile` field is
zero; an explicit unit permits a measured true zero. Budget count fields are
hard preflight caps. The `soft_max_*` fields are observed post-call ceilings:
record a complete candidate execution or complete forward+reverse family pair,
including a possible one-unit overshoot, then start no further work. Tests cover
both execution and judge overshoot for USD/tokens and execution latency.
Every ingested cost field must be finite and non-negative; invalid accounting
sets `cost_accounting_complete=False` and stops with its dedicated reason. Test
that `OptimizationOutcome` round-trips both `schema_version` and
`algorithm_version`. Policy validation requires `ridge>0`, `0<delta<1`, positive
iteration/tolerance/payload limits, at least two judge families, and the loop
requires an exact non-negative epsilon entry for every dossier preference.

- [ ] **Step 4: Verify state GREEN and compile regressions**

Run `tests/unit/test_compile.py` and the state section of
`tests/unit/test_optimize_loop.py`.

- [ ] **Step 5: Commit**

```bash
git add agentcoop/compile/compiler.py agentcoop/optimize/state.py tests/unit/test_compile.py tests/unit/test_optimize_loop.py
git commit -m "[feat] retain executable candidate archive"
```

## Task 5: Atomic configuration mutation

**Files:**
- Create: `agentcoop/optimize/mutations.py`
- Modify: `agentcoop/probe/suite.py`
- Modify: `agentcoop/probe/runner.py`
- Modify: `agentcoop/probe/__init__.py`
- Test: `tests/unit/test_optimize_mutations.py`
- Test: `tests/unit/test_probe.py`

- [ ] **Step 1: Write failing mutation tests**

Test pure application, one target/key, unknown/non-atomic target, no-op,
config-aware fingerprinting, deterministic source ordering, duplicate
suppression, and unchanged evidence ledger. Reject an unknown parameter,
uncertified component, missing/failed/wrong-kind probe, out-of-domain value,
reserved contract-changing key, and non-canonical JSON config/value.

First write probe tests showing `parameter_domain_suite(card, parameter,
values)` creates deterministic existing-kind `schema`/`smoke`/`resource` specs
for every value and output context. Run them through a real `ProbeRunner` fake
adapter; only actual passing handlers may stamp `probe_scope`, parameter,
allowed-values hash, value hash, and `contract_preserving=True`. A failed value
must make the domain unusable. Do not add a synthetic `ProbeKind`.

Required API:

```python
class ConfigMutation(BaseModel):
    mutation_id: str
    target: str
    key: str
    value: Any
    domain_id: str
    rationale: str

class CertifiedParameterDomain(BaseModel):
    domain_id: str
    component: str
    key: str
    values: tuple[Any, ...]
    probe_ids: tuple[str, ...]

class MutationProposal(BaseModel):
    candidate: OptimizationCandidate
    record: MutationRecord

def parameter_domain_suite(
    card: CapabilityCard, *, parameter: str, values: Sequence[Any],
    registry: TypeRegistry | None = None,
) -> list[ProbeSpec]: ...

def workflow_fingerprint(workflow: CompiledWorkflow) -> str: ...
def apply_config_mutation(
    workflow, mutation, *, domain, library
) -> CompiledWorkflow: ...

class ConfigGridMutationSource:
    def __init__(
        self, library: ComponentLibrary,
        domains: Sequence[CertifiedParameterDomain],
        mutations: Sequence[ConfigMutation],
    ) -> None: ...
    def propose(
        self, parents: Sequence[OptimizationCandidate], *, seen_fingerprints: set[str]
    ) -> tuple[MutationProposal, ...]: ...
```

- [ ] **Step 2: Verify RED, implement, verify GREEN**

Canonical fingerprints include the complete term JSON including config and
exclude workflow ID, compile notes, evidence, and provenance. The child ID is
`config::<fingerprint>`, retains a deep copy of the parent ledger, and records
parent/mutation/generation only in `OptimizationCandidate`, never in design
evidence. Set child workflow ID to
`ecps::<task_id>::<fingerprint>` while excluding it from the fingerprint.
Return the candidate together with its complete, replayable `MutationRecord`;
the loop must not inspect source-private fields.

A domain is valid only for a `CERTIFIED` component, a key declared in
`IOContract.parameters`, and successful existing-kind probes generated by
`parameter_domain_suite`. For each canonical value hash, require passing
`schema`, `smoke`, and `resource` records with `probe_scope="parameter_domain"`,
the same `parameter`, `allowed_values_hash`, matching `value_hash`, and
`contract_preserving=True`; schema/smoke must cover every output context.
Hard-reserved
identity/output/type/facet/binding/
merge/verifier/condition keys are never mutable.
Do not use `default=str` or `repr()` in identity; reject non-canonical values.

- [ ] **Step 3: Commit**

```bash
git add agentcoop/optimize/mutations.py agentcoop/probe tests/unit/test_probe.py tests/unit/test_optimize_mutations.py
git commit -m "[feat] add bounded configuration search space"
```

## Task 6: ECPS objective gate and state machine

**Files:**
- Create: `agentcoop/optimize/loop.py`
- Modify: `agentcoop/optimize/__init__.py`
- Test: `tests/unit/test_optimize_loop.py`

- [ ] **Step 1: Write failing admission/front tests**

Build real workflows and executions. Assert rejection for dossier defects,
empty/inadmissible ledger, an Atomic without bind evidence, static blocking
failure, case mismatch, failed execution, missing output, blocking unavailable
check, silent-empty detector signal, and exhausted resource budget. Assert that
different measured masks are incomparable and objectively dominated candidates
never reach a spy judge. Add adversarial PASS/FAIL/UNAVAILABLE checks at
`CheckLevel.PREFERENCE` and from both `LLM_JUDGE` and `HUMAN`; all must have
byte-equivalent zero effect on objective admission, detector signals, and
utility. Test an all-USD front, all-token front, mixed units, unstamped zeros,
explicitly stamped true zeros, mixed per-case latency stamps, and invalid
negative/NaN/infinite accounting. Include a subjective blocking FAIL that has
already made raw `ExecutionResult.ok=False`; its recomputed objective result
must equal the run with that check absent. Use duplicate/delimiter-containing
check IDs and assert removing all CHECK events avoids ambiguous parsing.

- [ ] **Step 2: Verify RED, implement objective helpers, verify GREEN**

Implement:

```python
OptimizationHarness = Callable[
    [CompiledWorkflow, EvaluationCase], Awaitable[CaseExecution]
]

def execution_admission_reasons(workflow, case, execution, ctx) -> tuple[str, ...]
def objective_execution_snapshot(execution) -> ExecutionResult
def determine_front_cost_unit(evaluations) -> CostUnit | None
def front_latency_available(evaluations) -> bool
def validate_cost_profile(cost) -> tuple[str, ...]
def derive_observed_utility(
    candidate, executions, *, front_cost_unit, latency_available
) -> tuple[UtilityVector, dict[str, str]]
def compatible_observed_front(evaluations) -> tuple[str, ...]
```

Do not use `CheckReport.hard_constraints_satisfied`; explicitly reject blocking
non-PASS statuses from the objective snapshot. Filter subjective checks from
report/trace.checks, remove every `EventKind.CHECK` event (never parse its free
text), and preserve other step numbers. Recompute snapshot `ok` from
`execution.state.ok` plus the filtered blocking rule; never trust raw
`execution.ok`. Preference evaluators remain outside candidate execution.

Choose one COST unit for the whole feasible epoch, and expose LATENCY only when
every case of every feasible candidate is stamped. Mixed/unknown coverage makes
the coordinate unavailable for all. Recompute all utilities/fronts after a
mutation. Reject non-finite/negative costs and stop further work with
`RESOURCE_ACCOUNTING_INVALID` rather than coercing them to zero.

- [ ] **Step 3: Write failing loop-transition tests**

Cover no cases, ineligible preference evaluator, objective singleton, no
preferences, no judge, agreed panel winner, position-biased panel, cycles,
judge exception, hard budget reservation before a full order swap, each soft
limit's one-unit overshoot, unavailable verbosity baseline, verbosity singleton,
insufficient capacity for all initial candidate/case combinations, one mutation
generation from an objective singleton, stable-winner exhaustion, unresolved
no-mutation behavior, unseen child blocked by each hard cap, algorithm-version
replay, no unseen mutations, stop-reason precedence, and deterministic repeated
runs.

- [ ] **Step 4: Implement `OptimizationLoop` minimally**

```python
class OptimizationLoop:
    def __init__(self, ctx, *, panel=None, policy=None, mutation_source=None): ...

    async def run(
        self,
        compilation: CompilationResult,
        cases: Sequence[EvaluationCase],
        harness: OptimizationHarness,
    ) -> OptimizationOutcome: ...
```

Initialization requires non-empty cases and exactly
`ctx.dossier.availability(CheckLevel.PREFERENCE) in
{EvaluatorAvailability.RUBRIC, EvaluatorAvailability.PREFERENCE_ONLY}`;
otherwise it returns the corresponding typed stop without executing a
candidate. Validate in this order: no cases, no preferences, evaluator
eligibility, then empty archive. Defer panel checks until after objective and
verbosity singleton handling. Distinguish absent panel, too few configured
families, configured-but-invalid order-swap coverage, valid exhausted coverage,
and an explicitly configured mutation source with no authorized child using
their dedicated stop reasons.

On a multi-candidate front, `AUTO_LOSS` compares only baseline versus each
candidate on every case and excludes a candidate if any case is over threshold.
It additionally requires its baseline candidate to remain in the
current objective front, be admitted on every case, and have substantive output
lengths; otherwise return `VERBOSITY_BASELINE_UNAVAILABLE` rather than
reintroducing an objectively dominated baseline.

Execute pending candidates and cases in sorted order. Build packets only after
objective admission. Compare only compatible-front candidates. Refit after each
panel result and ask active acquisition for an unjudged pair/case when the
model is not stable. A provisional stable incumbent still enters `MUTATING` when
it has authorized unseen children and budgets allow them; the same applies to
an objective singleton before it stops. Consume typed `MutationProposal`
records directly. Propose children from
the current preference front/incumbent until the finite neighborhood or
generation/candidate/budget caps are exhausted. Emit an event for every state
transition and preserve compiler rejections, packets, model snapshots, full
mutation records, and both fronts for offline replay.

Hard-budget preflight reserves every initial candidate times every case before
the first run, verifies the initial archive does not exceed `max_candidates`,
and reserves `len(cases)` before each mutation child. Never execute a partial
matched case set, truncate initial candidates, or select from the resulting
biased subset. Reserve two calls per family ordering pair; a third family may
run only if two more calls remain.

Use the same policy ridge/max-iterations/tolerance in full and every LOO fit.
A stable winner with an exhausted/absent mutation neighborhood stops
`CONFIDENT_PREFERENCE` (or `OBJECTIVE_SINGLETON` before judging). Use
`NO_MUTATIONS` only for an unresolved front with a configured but empty
authorized neighborhood. If an unseen child exists but any hard cap prevents
it, stop `BUDGET_EXHAUSTED` with no selected ID.

- [ ] **Step 5: Verify GREEN and focused subsystem regressions**

Run:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin \
  tests/unit/test_optimize_loop.py tests/unit/test_compile.py \
  tests/unit/test_execute.py tests/unit/test_repair.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agentcoop/optimize/loop.py agentcoop/optimize/__init__.py tests/unit/test_optimize_loop.py
git commit -m "[feat] add evidence-constrained preference loop"
```

## Task 7: End-to-end integration, documentation, and adversarial controls

**Files:**
- Create: `tests/integration/test_preference_optimization.py`
- Modify: `docs/architecture/v2_interfaces.md`
- Modify: `docs/architecture/v2_method.md`
- Modify: `README.md`
- Modify: `agentcoop/__init__.py`

- [ ] **Step 1: Write failing end-to-end tests**

Use the synthetic environment only as an offline wiring fixture. Compile at
least two admissible candidates, execute through real `ExecutionEngine`, inject
two deterministic order-aware judges, and assert selected workflow, serialized
archive, unchanged design ledgers, and repeatable IDs. Add unresolved tests for
unavailable judges and an A>B, B>C, C>A scripted cycle. Add a verbosity-duplication
control with `AUTO_LOSS`. Use enough independent matched cases for each of
exactly two families to clear the specified LOO lower-bound test; do not weaken
production math merely to make wiring select.

Add the full evidence closure:
`parameter_domain_suite → ProbeRunner → certified card/domain → MutationProposal
→ child matched execution → replayable MutationRecord`. This test must not
construct `ProbeOutcome` by hand.

- [ ] **Step 2: Verify RED, finish public exports, verify GREEN**

Export only documented public types and functions from `agentcoop.optimize`.
Keep root `agentcoop.__all__` limited to version while adding `optimize/` to its
layout description.

- [ ] **Step 3: Update architecture and README**

Document the actual interface, optional dependency-injected judge, absence of a
default live provider, non-evidence status of preferences, normal unresolved
result, and exact test command. Do not claim that ECPS proves scientific
correctness or reproduces Kimi K3 training.

- [ ] **Step 4: Run complete verification**

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin
python -m agentcoop.cli compile --task syn-multi
python -m agentcoop.cli bench list
```

Expected: all tests pass; existing CLI output remains functional; no network or
API key is required.

- [ ] **Step 5: Audit evidence boundaries and placeholders**

Run:

```bash
rg -n "outcome_evidence|EvidenceLedger\.add|UtilityVector\.with_value|weight_hint" agentcoop/optimize
rg -n "TODO|TBD|implement later|pass #|NotImplemented" agentcoop/optimize agentcoop/ir/preference.py
```

Expected: the first command finds no judge-to-evidence/utility conversion and no
`weight_hint` use; the second finds no placeholder implementation.

- [ ] **Step 6: Commit**

```bash
git add README.md agentcoop/__init__.py docs/architecture tests/integration/test_preference_optimization.py
git commit -m "[docs] document and verify ECPS workflow search"
```

## Plan self-review checklist

- [ ] Every architecture-spec invariant maps to at least one task and test.
- [ ] No task adds `agentcoop/evaluate/`, a judge registry, a hidden scalar, or a dependency.
- [ ] No preference type inherits from scientific evidence/check/utility types.
- [ ] All implementation steps follow RED → observed failure → GREEN.
- [ ] Public names are consistent across tasks.
- [ ] Task-family cases share a dossier contract; cross-dossier workflow templates remain explicitly out of scope.
- [ ] Structural/component/uncertified config mutations return through probe plus compiler; only a certified contract-preserving parameter domain preserves the ledger.
- [ ] The full suite and both existing CLI smoke commands are the final gate.
