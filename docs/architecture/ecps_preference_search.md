# Evidence-Constrained Preference Search

## Status and scope

This document specifies the first production implementation of
Evidence-Constrained Preference Search (ECPS) for AgentCo-Op v2. ECPS is a
post-execution, non-gradient optimizer for tasks whose dossier declares
rubric- or preference-only evaluation. It treats an agentic generative reward
model as a noisy preference sensor, never as observed truth.

The implementation resolves the current `SelectionPolicy(mode="expert")`
handoff after candidates have executed. It does not change compile-time
selection semantics, the six objective dimensions, fault diagnosis, or the
repair transaction protocol.

The first release supports:

- every admissible compiler candidate, including the sufficient
  single-component candidate;
- matched evaluation cases sharing one dossier contract;
- an anonymized outcome packet;
- a mandatory read → generated rubric → criterion scorepad protocol;
- conservative order swapping and a diverse judge panel;
- criterion-wise active Bradley–Terry preference inference;
- an optional K3-inspired verbosity auto-loss policy;
- bounded, one-key configuration mutations; and
- a selected candidate only when preference dominance is stable. Otherwise it
  returns an unresolved contender set.

It deliberately does not add a live model provider, CLI command, benchmark
score, structural mutation, component replacement, or automatic prompt
rewriter. A live judge is injected through the existing component-adapter
boundary. Structural/component alternatives must be recompiled so their
design-evidence ledger is rebuilt by grammar rules.

## Architectural position

```text
dossier + certified components
        ↓
bounded compiler candidates + design ledgers
        ↓
matched executions for every evaluation case
        ↓
strict runtime admission + compatible observed Pareto front
        ↓
anonymous packets → rubric → scorepad → order swaps → panel
        ↓
criterion-wise preference archive + active comparison model
        ↓
selected candidate | unresolved contender set
        ↘ optional one-key config children, then repeat
```

ECPS lives in `agentcoop/optimize/`. It is not part of `compile/`, because
outputs do not exist there; not part of `repair/`, because a preference loss is
not a diagnosed fault; and not an `evaluate/` package, because the repository
removed that package after it duplicated IR and had no caller.

## Non-negotiable invariants

1. **Objective admission precedes preference.** A judge never sees a candidate
   rejected by dossier, design-evidence, static-analysis, runtime-output, or
   resource gates.
2. **Preference never becomes evidence.** Judge records are independent
   nominal models. No ECPS code calls `EvidenceLedger.add()`,
   `outcome_evidence()`, or `UtilityVector.with_value()` with judge output.
3. **Unavailable is not pass.** A blocking `FAIL`, `UNAVAILABLE`, or
   `INCONCLUSIVE`, a failed node, a missing required output, or a blocking
   detector signal makes a run inadmissible.
4. **No hidden scalarization.** Scores in a scorepad are diagnostic. They are
   never summed across dossier preferences. Inference and dominance are
   criterion-wise.
5. **Matched cases.** Every candidate is run with the same ordered case list,
   input fingerprint, seed, limits, evaluator set, and tool snapshot identifier.
6. **One mutation, one cause.** A config child changes exactly one key on one
   `Atomic`. Its fingerprint includes config even though
   `WorkflowTerm.structural_key()` intentionally does not.
7. **Abstention is a normal result.** Position disagreement, malformed judge
   output, missing citations, cycles, insufficient judge diversity, or budget
   exhaustion can leave the front unresolved.

## Existing IR mapping

ECPS reuses these authoritative types without changing their meaning:

| Existing type | ECPS use |
|---|---|
| `TaskEvidenceDossier.preferences` | Frozen top-level meta-rubric; `weight_hint` is ignored. |
| `EvaluatorAvailability` | ECPS is eligible only when `dossier.availability(CheckLevel.PREFERENCE)` is `RUBRIC` or `PREFERENCE_ONLY`; deterministic evaluators remain authoritative. |
| `CompiledWorkflow.evidence` | Pre-judge design-admission gate; never mutated by selection. |
| `CheckReport` / `CheckResult` | Objective runtime gate and packet material only after subjective checks are removed; preference results are not appended. |
| `UtilityVector` | Observed, masked objective front before judging. |
| `CompilationResult.estimates` | Static evidence/risk basis; execution replaces measured validity/cost/latency. |
| `ExecutionResult` | Final outputs, lineage-preserving trace, checks, and measured resources. |
| `ComponentAdapter` / `AdapterRegistry` | Optional structured judge transport. |

New persistent protocol records, including the explicit verbosity policy, live
in `agentcoop/ir/preference.py`, because
they cross adapters, optimization, JSON logs, and future human review. They do
not inherit from `EvidenceItem`, `CheckResult`, or `UtilityVector`.

## Persistent preference protocol

### Meta-rubric and pair rubric

`MetaRubric.from_dossier(dossier, version)` copies each declared `Preference`
into an immutable `MetaCriterion`. IDs must be non-empty and unique. A
canonical SHA-256 fingerprint covers task ID, version, and criteria.

For each pair, the first ordered judge call returns a `PairRubric` containing
exactly one operational `RubricCriterion` per meta criterion. Each item must
retain the parent `preference_id`, direction, and symmetric instructions. The
reverse-order request freezes the same rubric fingerprint. Any missing,
additional, duplicated, or changed criterion invalidates that judge's pair.

### Scorepad

Every `ScorepadEntry` records:

```python
preference_id: str
criterion_id: str
verdict: PairwiseVerdict       # left | right | tie | abstain
left_score: float | None       # diagnostic, finite, 0..10
right_score: float | None
evidence_refs: tuple[str, ...]
confidence: float              # finite, 0..1
missing_evidence: tuple[str, ...]
rationale: str
```

Directional entries require at least one resolvable packet reference and a
non-empty rationale. The `OrderedPairJudgment` final verdict is derived without
weights: left wins only when no criterion prefers right and at least one
prefers left; right is symmetric; all ties produce tie; mixed directions or
any abstention produce abstention.

`OrderedPairJudgment` contains no cost field. Transport cost is stamped onto
`JudgeCallRecord` by the adapter boundary, so a model payload cannot report its
own call as free. `PairwisePreferenceObservation` is reserved for two
order-swapped judge calls. A separate `PolicyPreferenceObservation` records a
deterministic verbosity auto-loss and has no judge ID, family, generated
rubric, or forward/reverse judgments. Policy observations never count as judge
diversity and never enter leave-one-family-out fitting.

### Order-swap normalization

For canonical candidates A and B:

| Forward `(A,B)` | Reverse `(B,A)` | Observation |
|---|---|---|
| `LEFT` | `RIGHT` | A preferred |
| `RIGHT` | `LEFT` | B preferred |
| `TIE` | `TIE` | genuine tie |
| `ABSTAIN` | `ABSTAIN` | abstain |
| anything else | anything else | unresolved |

The two calls must share pair ID, judge ID/family, candidate set, rubric hash,
case ID, and strictly reversed packet order.

## Candidate packets and trust boundary

`build_candidate_view()` creates a deterministic view from one
`ExecutionResult`. It uses a positive allowlist and contains:

- task goal/context and case ID;
- final artifact type, dossier-allowlisted semantic facets, safe payload
  snapshot, public content fingerprint, and public hashed lineage references;
- artifact/process/claim/workflow checks represented only by public check ref,
  level, status, source, finite score, and blocking flag;
- a canonical trace digest containing event/check counts and failures;
- measured `CostProfile`;
- canonical output length in characters when substantive snapshots are
  available; and
- resolvable reference IDs for every artifact, check, trace digest, and resource
  record.

Before this allowlist is applied, every check with
`level == CheckLevel.PREFERENCE` or source in
`{SignalSource.LLM_JUDGE, SignalSource.HUMAN}` is removed. Raw `check_id`,
`subject`, `summary`, and `evidence` are never copied. Raw artifact IDs,
producer IDs, local paths, notes, raw content hashes, and lineage IDs are never
copied either. Runtime facet keys are retained only when the dossier's artifact
type or subgoal output contract declares that key; undeclared keys such as
`model` or `provider` are dropped. Public content fingerprints hash canonical
safe payload content, or hash the stored content-hash string again when content
is unavailable. Public references are hashes of identity-free content.
Identity-free duplicate checks are coalesced into one `PacketCheck` with an
`occurrences` count before hashing; citations stay deterministic without a raw
ID-based ordinal.

Safe payload snapshots accept JSON primitives, finite numbers, string-keyed
mappings, lists/tuples, and bytes (base64-tagged). A path-backed artifact is
never dereferenced by default: when it has no in-memory payload, the packet
contains only type/allowlisted facets/public content fingerprint and
`content_unavailable_reason="path-backed artifact not snapshotted"`. Arbitrary
objects, non-finite numbers, and non-string mapping keys are similarly replaced
by a deterministic unavailable marker without serializing `repr()`. Canonical
payloads larger than the configured symmetric character limit are replaced by
an over-limit marker and public content fingerprint, not truncated differently
by candidate.
The judge may abstain when substantive content is unavailable.
`CandidateView.output_length` is `None` unless every required output has a safe
substantive snapshot; unavailable-marker length is never treated as candidate
verbosity.

The packet ID is a hash of normalized content. System-generated metadata
contains no workflow ID, candidate ID, incumbent/mutation label,
component/model/provider identity, raw topology, or private reasoning.
Candidate payloads are explicitly untrusted and may themselves self-identify;
the anonymity guarantee does not rewrite user-produced content. Judges can
return only `LEFT`/`RIGHT`/`TIE`/`ABSTAIN`, never an arbitrary workflow
identifier.

`ComponentPreferenceJudge` has an exact transport constructor:

```python
ComponentPreferenceJudge(
    registry: AdapterRegistry,
    component: str,
    *,
    judge_id: str,
    family: str,
    source: SignalSource = SignalSource.LLM_JUDGE,
    request_type: str = "agentcoop.preference_judge_request",
    response_type: str = "agentcoop.preference_judge_response",
    limits: ResourceLimits | None = None,
)
```

`plan()` serializes `JudgeRequest` into one in-memory request artifact under
`Invocation.inputs[request_type]`, sets `Invocation.component`, `limits`, and
`seed=request.seed`, and never places candidate IDs in config. `compare()`
invokes `registry.get(component)`, requires `result.ok` and exactly the expected
in-memory response artifact, then accepts a mapping or JSON string matching
`ComponentJudgePayload`. A path response is invalid and is never dereferenced.
Adapter exceptions/missing output are unavailable; wrong type, malformed JSON,
mismatched packet/rubric, and protocol violations are invalid. Neither creates
a default winner.

Each request also carries the evaluation case's explicit seed.
`JudgePanel.compare(..., seed=case.seed)` passes it into both orderings, and the
component bridge propagates it to `Invocation.seed`; neither packet
construction nor pair scheduling reads global randomness. A component-backed
judge uses `InvocationResult.cost` as the sole authoritative call cost and
ignores any cost-like field in the returned artifact. An optional
`reported_cost` in the transport payload is untrusted audit text only and never
enters the ledger. A native injected judge reports cost through its trusted
adapter response, never inside the judgment payload.

## Objective admission and observed front

`CompilationResult` gains `workflows: dict[str, CompiledWorkflow]`, populated
only after justification and blocking static analysis pass. This makes the
bounded candidate archive executable instead of retaining only the pre-run
winner.

For each candidate and evaluation case, `OptimizationLoop` performs a fresh
run. Before admission, it constructs an objective snapshot by removing from
both `CheckReport.results` and `Trace.checks` every check with
`level == CheckLevel.PREFERENCE` or source in
`{SignalSource.LLM_JUDGE, SignalSource.HUMAN}`. Only that snapshot is passed to
blocking-check logic, `detect_all()`, utility derivation, and packet check
construction. Because `TraceEvent` has no unambiguous check identity/source, the
snapshot removes **all** `EventKind.CHECK` events while preserving every other
event's original step; filtered `Trace.checks` and `CheckReport` are the only
check inputs. This avoids parsing `detail` or colliding duplicate check IDs.

The snapshot also recomputes `objective_ok` instead of trusting
`ExecutionResult.ok`, which the current engine derives from the unfiltered
report. It is exactly `execution.state.ok` plus the filtered objective report's
blocking-status rule. Preference evaluators run through `JudgePanel`, outside
candidate execution. Removed checks and the raw `ok` may remain in the audit
record, but have zero objective effect and are never converted to preference
observations.

A candidate is feasible only when:

```text
dossier.specification_defects() is empty
workflow.evidence is non-empty and fully justified
every Atomic has a binding evidence target
fresh static analysis has no blocking failure
every case fingerprint returned by the harness matches the request
recomputed objective_ok is true (raw execution.ok is ignored)
all required outputs are present
no objective blocking check is FAIL / UNAVAILABLE / INCONCLUSIVE
detect_all(objective_trace, objective_report, ...).blocking() is empty
search and per-run resource limits hold
```

The default post-execution utility builder records:

- `validity = 1.0` only after admission;
- `evidence = workflow.evidence.evidence_coverage()`;
- one front-wide measured cost basis, if available;
- measured mean latency when present; and
- compile-time risk when available.

`CaseExecution` explicitly stamps `cost_unit` (`USD`, `TOKENS`, or absent) and
`latency_measured`; the zero-filled fields of `CostProfile` alone never prove a
measurement. Before setting `Objective.COST`, the loop chooses one basis for
the entire feasible epoch: USD only when every case of every candidate is
explicitly priced in USD; tokens only when every case of every candidate is
explicitly token-measured; otherwise COST is unavailable for every candidate.
Mixed priced/unpriced candidates are therefore never compared as `$0.10`
versus `500 tokens`. An explicit unit makes a true zero a valid observation;
an unstamped zero remains unknown. LATENCY is available only when every matched
case of every feasible candidate has `latency_measured=True`; otherwise it is
unavailable for the whole epoch. Whenever a mutation adds a feasible candidate,
the loop recomputes common cost/latency availability and every candidate's
utility/front; it never compares new measurements against stale utilities.

At execution and judge ingestion, every `CostProfile` field must be finite and
non-negative (and tokens an integer). Invalid authoritative accounting sets
`cost_accounting_complete=False`, starts no further work, and stops
`RESOURCE_ACCOUNTING_INVALID`; it is never coerced to zero or allowed to bypass
a soft limit.

Robustness and scientific utility remain unavailable unless a later,
explicitly registered objective builder can cite non-preference observations.
The first release exposes no arbitrary utility callback, preventing judge
signals from being laundered into objective coordinates.

Dominance requires identical measured-objective masks. Different masks remain
incomparable. Only the resulting `ObservedParetoFront` enters preference search.

## Judge panel and verbosity policy

`JudgePanel` requires two distinct families for a selectable result. Both are
run in both presentation orders. A third configured family is called only when
the first two produce conflicting normalized outcomes for any criterion or one
primary family fails to produce a valid normalized observation. Repeated calls
from one family are not treated as new independent families; diversity counts
only families with usable order-swapped observations.

The optional `VerbosityPolicy.AUTO_LOSS` names a cold-start candidate and
multiplier `sigma > 1`. For case `t`, the threshold is

```text
sigma * canonical_output_length(cold_start, t)
```

On a multi-candidate objective front, the loop deterministically compares the
baseline against each other candidate on every case. A candidate is removed
before Bradley–Terry fitting if **any** case exceeds its baseline threshold;
each such baseline/candidate/case decision is a
`PolicyPreferenceObservation`. A within-budget case records policy abstention,
not a preference, and non-baseline candidates are never compared to one another
by this policy. The policy cannot establish judge-family diversity or rank two
compliant candidates. If it leaves one compliant contender, the typed stop is
`VERBOSITY_POLICY_SINGLETON`; if the baseline is not in the current objective
front, was not admitted on all cases, or lacks a substantive output length,
the policy is not guessed and the run stops
`VERBOSITY_BASELINE_UNAVAILABLE`. This policy uses canonical characters because
the core package has no tokenizer. A non-baseline candidate with unavailable
length causes its baseline comparison to abstain rather than treating the
unavailable marker as short. With the default `NONE`, length remains a
reported resource and the judge must assess concision under a declared
preference.

## Preference model and active acquisition

For each dossier preference `k`, fit an independent regularized
Bradley–Terry model. Candidate IDs are canonically sorted; for each unordered
pair `(a,b)` with `a < b`, a directional observation contributes `y=1` when A
wins, `y=0` when B wins, and one tie contributes one fractional Bernoulli row
with `y=0.5` (not two half-weight rows). Abstain/unresolved and verbosity policy
records contribute no likelihood. Each
`(judge_family, pair, case, preference)` contributes at most once; inconsistent
duplicates for that key are excluded and mark evaluator instability. Confidence
is diagnostic and never weights fitting.

For `d = theta_a - theta_b`, the exact penalized objective is

```text
sum_o [y_o d_o - log(1 + exp(d_o))] - (ridge / 2) ||theta||²
subject to sum_i theta_i = 0
```

Use the reduced coordinates `theta = B beta`, where the first `n-1` rows of
`B` form the identity and the last row is all `-1`; this enforces the constraint
for both updates and covariance. Deterministic Newton updates solve the
positive reduced information system. Backtracking starts at step 1 and halves
until the objective does not decrease; failure below `2^-20`, a singular or
non-finite solve, or exceeding `max_iterations` without projected-gradient
infinity norm `<= tolerance` sets `converged=False`. At the solution,
`C = B H_beta^-1 B^T`; pair variance is exactly
`(e_a-e_b)^T C (e_a-e_b)`. Non-converged/non-finite models can never dominate
or select. Stable log-sigmoid arithmetic and matrix operations use only the
standard library.

Connectivity and cycles are criterion-wise. A usable directional or tie
observation creates an undirected connectivity edge; abstain/unresolved does
not. For each unordered pair, equally weighted family×case directional wins
are counted; a majority arc `a -> b` exists only when A wins strictly exceed B
wins, while equal wins and ties create no arc. A directed cycle of length at
least three blocks selection for that criterion. Cross-criterion trade-offs are
not cycles. Every criterion model over the current contenders must be connected,
converged, cycle-free, and have finite covariance.

Active acquisition operates on the finite set
`U={(unordered pair, case): no panel attempt exists}`. In dossier preference
order, first select the lexicographically smallest target in `U` that bridges
two connectivity components for the first disconnected criterion. Otherwise,
for each target compute

```text
acquisition = max_k p_k(1-p_k) * pair_variance_k / 4
```

where four is the documented two-primary-family forward/reverse call cost; the
conditional third family is not estimated. If no criterion has a finite value,
choose the lexicographically first target. Ties break by
`(candidate_a, candidate_b, case_id)`, and empty `U` returns `None`.
Unavailable/invalid attempts remain audited and are never retried in that run.

Dominance is also exact. For `N` contenders and `K` criteria, set
`alpha = delta / (K * max(1, N-1))` and
`z = NormalDist().inv_cdf(1-alpha)`. For candidate A versus B and criterion k:

```text
d_k = theta[A,k] - theta[B,k]
v_k = C[A,A] + C[B,B] - 2*C[A,B]
L_k = d_k - z * sqrt(max(v_k, 0))
```

A dominates B iff every `L_k >= -epsilon_k` and at least one
`L_k > epsilon_k`. Epsilon is a non-negative log-odds tolerance, and the policy
must cover every preference ID exactly. A selected candidate must dominate
every rival under this simultaneous correction.

The full archive must provide at least `min_judge_families` distinct usable
families **for every criterion**, not merely globally. Then remove each
contributing family in turn and refit with the same ridge/iteration/tolerance
policy. LOO skips only the minimum-family count; it still requires every model
to be connected, converged, cycle-free, finite, and the same winner to dominate
every rival. Exactly two agreeing families can therefore select only when each
family alone supplies enough case coverage to clear the same lower-bound test.

## Bounded configuration search

`ConfigMutation` changes one top-level key on one `Atomic` term.
`apply_config_mutation()` is pure and rejects unknown targets, non-atomic
targets, no-ops, and a second changed key. Candidate identity is a SHA-256
fingerprint of canonical term JSON including config and excluding workflow ID,
notes, evidence, and provenance. It rejects a config or proposed value that is
not canonical JSON rather than using `default=str` or object `repr()`.

Mutation is not authorized by arbitrary `Atomic.config`. A
`CertifiedParameterDomain` names a component/key, finite canonical values, and
probe IDs produced by `parameter_domain_suite()`. That helper uses the existing
executable `schema`, `smoke`, and `resource` probe handlers for every canonical
value (and every declared output context); it does not invent a hand-written
`ProbeOutcome` kind. `ProbeRunner._outcome()` copies the suite's scope
marker into evidence only after the real handler verdict is known.

`ConfigGridMutationSource` receives a `ComponentLibrary`, these domains, and
requested mutations. It admits a domain only when the component is at least
`CERTIFIED`, the key exists in `CapabilityCard.io.parameters`, and every cited
probe exists and passed. For every value hash, the cited set must cover
`schema`, `smoke`, and `resource`; each outcome must contain
`probe_scope="parameter_domain"`, the same `parameter`, an
`allowed_values_hash` equal to SHA-256 of the canonical finite tuple, the
matching `value_hash`, and `contract_preserving=True`. Schema/smoke records must
collectively cover every declared output context. The mutation target must bind
that component and its value must be in the certified finite set. Reserved identity/contract keys such as
`component`, `subgoal_id`, `node_id`, output/type/facet, binding, merge,
verifier, and condition fields are rejected even if declared.

The source returns typed `MutationProposal(candidate, record)` values in
`(parent_id, target, key, canonical value)` order, at most once, from the
current preference front or provisional incumbent. `record` carries the full
parent/child/key/value/domain/probe/rationale snapshot; the loop never reads
source-private state to reconstruct it. A child gets deterministic
`workflow_id="ecps::<task_id>::<fingerprint>"` while identity hashing still
excludes workflow ID. Only then may it retain a deep copy of the parent's design
ledger: the probe-backed domain is the evidence that the parameter variation
preserves component and artifact contracts. An uncertified parameter,
component/topology change, or contract-affecting config must return through
probe plus `Compiler` and rebuild the ledger.

Judge rationales are retained for humans or an external proposal component,
but the first release never converts free-text critique into a mutation.

## State machine

```text
INITIALIZING
  → EXECUTING
  → OBJECTIVE_GATING
  → COMPARING
  → MODELING
  → MUTATING ─┐
       ↑      │ new candidates
       └──────┘
  → STOPPED
```

| State | Success transition | Fail/terminal transition |
|---|---|---|
| `INITIALIZING` | validate policy/cases/archive → `EXECUTING` | no candidates/preferences/cases → typed stop |
| `EXECUTING` | all pending matched cases recorded → `OBJECTIVE_GATING` | run/search budget exhausted → `STOPPED` |
| `OBJECTIVE_GATING` | multi-candidate front → verbosity/`COMPARING`; singleton with authorized unseen child → `MUTATING` | empty front → `NO_ADMISSIBLE_CANDIDATES`; exhausted singleton → `OBJECTIVE_SINGLETON` |
| `COMPARING` | panel observations appended → `MODELING` | no judge/budget/protocol coverage → unresolved stop |
| `MODELING` | unjudged informative pair → `COMPARING`; unseen authorized child → `MUTATING` | stable winner with no remaining child → `STOPPED(CONFIDENT_PREFERENCE)`; otherwise unresolved stop |
| `MUTATING` | unseen config child → `EXECUTING` | no mutation/front stable → unresolved stop |

Every transition emits an `OptimizationEvent`. The outcome contains all
schema/algorithm version, dossier fingerprint, matched cases, frozen policy,
candidates and their executions/rejections/objective utilities, compiler
rejections, packet views, ordered calls and judgments, normalized judge and
policy observations, every preference-model snapshot, full mutation records
(parent/child/target/key/value/domain values/probe refs/rationale), budget
ledger, explicit objective and preference fronts, selected ID if any, typed
stop reason, events, and notes. These are sufficient to replay selection
without executing a component or judge. Returning a selected workflow does not
mutate its evidence ledger or append preference provenance.

A provisional confident incumbent or objective singleton does not end search
while an authorized unseen configuration child remains. Mutation parents are
the current preference front (or provisional incumbent). A stable winner whose
authorized neighborhood is exhausted stops `CONFIDENT_PREFERENCE` (or
`OBJECTIVE_SINGLETON` before any preference comparison), even when a mutation
source was configured. `NO_MUTATIONS` is reserved for an unresolved front that
requires expansion but whose configured source has no authorized unseen child.
If a child exists but candidate/generation/execution capacity prevents running
it, stop `BUDGET_EXHAUSTED` with no selected ID; do not claim terminal
confidence.

## Budgets and stopping

`OptimizationBudget` hard-caps candidate executions, judge calls, candidates,
and generations by preflight reservation. The loop does not start an
order-swapped family comparison if the remaining judge-call budget cannot
cover both calls. It reserves `len(cases)` executions before starting a
candidate, so a candidate is never admitted on a partial case set. Before the
first run it also verifies that every initial archived candidate can receive
the full matched case set; insufficient capacity returns
`BUDGET_EXHAUSTED` without selecting from a biased subset. Initial candidates
are never silently truncated by `max_candidates`.

USD, token, and execution-latency limits are explicitly named
`soft_max_usd`, `soft_max_tokens`, and `soft_max_execution_latency_s`. Existing
adapters and the harness do not provide reliable pre-call estimates, so these
are observed post-call ceilings, not hard caps. Candidate execution is one
atomic accounting unit; a judge family's forward+reverse pair is another. The
ledger records any one-unit overshoot, then starts no further work. USD and
tokens include both execution and authoritative judge transport cost; the
latency soft limit covers candidate executions. No payload-supplied cost is
trusted.

Normal stop reasons are:

- `OBJECTIVE_SINGLETON`
- `CONFIDENT_PREFERENCE`
- `VERBOSITY_POLICY_SINGLETON`
- `NO_ADMISSIBLE_CANDIDATES`
- `NO_PREFERENCES`
- `NO_CASES`
- `INELIGIBLE_EVALUATOR`
- `NO_JUDGES`
- `INSUFFICIENT_JUDGE_DIVERSITY`
- `BUDGET_EXHAUSTED`
- `RESOURCE_ACCOUNTING_INVALID`
- `EVALUATOR_UNSTABLE`
- `VERBOSITY_BASELINE_UNAVAILABLE`
- `NO_MUTATIONS`
- `FRONT_STABLE_UNRESOLVED`

Initialization stop precedence is deterministic: `NO_CASES`, then
`NO_PREFERENCES`, then `INELIGIBLE_EVALUATOR`, then an empty archive. A missing
panel is checked only after objective gating, so an objective or verbosity
singleton needs no judge. `NO_JUDGES` means no panel was injected;
`INSUFFICIENT_JUDGE_DIVERSITY` means too few families were configured;
`EVALUATOR_UNSTABLE` means enough families were configured but valid
order-swapped coverage could not be obtained. `FRONT_STABLE_UNRESOLVED` means
valid coverage was exhausted with no stable winner. `NO_MUTATIONS` has only the
unresolved-front meaning above; exhausted neighborhoods never override an
objective or confident winner's successful stop reason.

## Test contract

### Preference IR

- strict Pydantic fields and JSON round trips;
- stable rubric/packet fingerprints;
- unique and complete meta/pair criteria;
- finite bounded scores/confidence;
- mechanically resolvable evidence refs;
- final verdict derived without weights;
- all order-swap truth-table cases;
- candidate IDs cannot be returned by a judge.

### Packets and adapters

- packet determinism and internal-identity redaction;
- adversarial identity strings in check ID/subject/summary/evidence, artifact
  ID/producer/path/notes, and lineage never appear in system metadata;
- path-backed, oversized, bytes, non-finite, and arbitrary-object payload
  policies are deterministic and never dereference a local path;
- candidate text remains untrusted data;
- exceptions/missing/wrong/malformed outputs are unavailable;
- source and judge family are adapter-stamped;
- case seed reaches `Invocation.seed`;
- `InvocationResult.cost` overrides any cost-like judge payload;
- negative/non-finite authoritative judge cost invalidates accounting;
- reverse request freezes rubric and strictly swaps views;
- identical packet comparison ties in the control judge.

### Objective admission

- reject empty/inadmissible ledgers and unrecorded atoms;
- reject missing outputs, silent-empty results, blocking unavailable checks,
  and case-fingerprint mismatches;
- preference/LLM-judge/human PASS, FAIL, and UNAVAILABLE checks have identical
  zero effect on objective admission, detectors, and utility;
- raw `execution.ok=False` caused solely by a subjective blocking failure is
  ignored by recomputed `objective_ok`; all CHECK events are removed without
  parsing duplicate or delimiter-bearing IDs;
- use one explicit front-wide COST unit; priced/unpriced/unknown/true-zero
  mixtures never create cross-unit dominance;
- latency is all-cases/front-wide or unavailable; invalid resource values stop;
- preserve candidates with different availability masks as incomparable;
- dominated candidates never reach a judge.

### Preference model and panel

- deterministic constrained fitting, reduced-space covariance, and acquisition;
- fractional ties, damping/non-convergence, abstentions, criterion-wise
  disconnected graphs, strict-majority cycles, and simultaneous LCB dominance;
- third judge only on primary disagreement;
- insufficient families cannot select;
- exactly two agreeing families can select and survive leave-one-family-out;
- leave-one-family-out instability cannot select;
- confidence fields do not weight model fitting;
- verbosity-policy observations do not enter fitting or judge diversity;
- no scorepad summation or `weight_hint` use.

### Mutation and loop

- executable schema/smoke/resource parameter-domain probes, not hand-built
  outcomes;
- mutation purity, one-key rule, no-op rejection, config-aware fingerprints,
  child workflow ID, and typed proposal/record pairing;
- reject undeclared, unprobed, uncertified, out-of-domain, and reserved
  contract-changing configuration mutations;
- same ordered cases/seeds/limits/tool snapshot for every candidate;
- exact state transitions and every stop reason;
- hard count reservations and post-call soft-limit overshoot semantics;
- selected/unresolved outcomes serialize and replay;
- schema and algorithm versions both survive replay;
- workflow evidence and objective utility are byte-equivalent before/after
  preference selection.

### Integration and adversarial controls

- compile archive → execute candidates → observed front → deterministic panel
  → selected workflow;
- unavailable panel keeps the unresolved front;
- always-left judge is neutralized by order swapping;
- A>B, B>C, C>A stays unresolved;
- pure verbosity duplication cannot win under auto-loss;
- real ProbeRunner domain evidence authorizes a child that executes and replays;
- unsupported claim and rubric-keyword/prompt-injection payloads cannot alter
  protocol fields;
- all existing compile/execute/diagnose/repair/bench tests remain green.

## Compatibility

Existing dossier and workflow JSON remain valid. No existing model gains a
required field. No dependency, API key, network call, CLI behavior, benchmark
success definition, repair policy, or default compiler policy changes. When no
judge is injected, the deterministic system stops normally with the observed
front and `selected_candidate_id=None`.
