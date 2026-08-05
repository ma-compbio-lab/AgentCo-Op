# AgentCo-Op v2 — method

**Framing.** v1 was *Retrieval-Based Synthesis of Interoperable Multi-Agent
Workflows*. v2 is **an evidence-driven compiler for heterogeneous agent
workflows**: it certifies external components by executing probes against them,
compiles typed workflows whose every node and edge carries explicit design
evidence, diagnoses failures by artifact-level causal tracing, and improves
workflows through transactional repair and Pareto optimization when no scalar
reward exists.

The problem this stakes out:

> When components are independently developed black boxes, execution is
> expensive, evaluation is multi-dimensional and often unavailable, and there
> are too few tasks to search over — how do you compile, verify, diagnose, and
> improve an auditable agent workflow?

That setting is deliberately *not* the one AFlow, MASS, ADAS, or RL-trained
designers operate in. They assume candidate workflows can be re-run cheaply and
ranked by a scalar. Neither assumption holds here, so copying their search
formulation would be answering a question we do not have.

---

## The five objections, and the mechanism answering each

### 1. "The system design is imagined by an LLM"

**Mechanism: admissible design evidence, enforced structurally.**

`ir/evidence.py` defines four evidence kinds — `REQUIREMENT` (derived from the
dossier), `CAPABILITY` (from an executed probe), `COORDINATION` (from a typed
data dependency, an independence analysis, or a registered merge algebra), and
`OUTCOME` (from recorded run statistics) — and five strengths. Only `DERIVED`,
`OBSERVED`, and `STATISTICAL` are *load-bearing*.

Every structural decision carries a `DesignEvidenceRecord`. `REQUIRED_EVIDENCE`
maps each decision kind to the evidence kinds it must cite, and `admissible` is
computed, not asserted. Binding a component requires both a requirement and a
*probe-observed* capability. A README claim registers as `DOCUMENTED` and is not
load-bearing. An LLM proposal registers as `ASSERTED` and is not load-bearing.

An LLM may still propose a workflow. It simply cannot justify one — and the
compiler rejects candidates containing inadmissible decisions rather than
back-filling a rationale.

Three further mechanisms make this bite:

- **Grammar with applicability conditions** (`ir/workflow.py`,
  `compile/grammar.py`). Structure is a term in a small typed grammar, and each
  production has a condition that must be discharged. `Parallel` requires branch
  independence *plus* a positive reason (complementary artifact types, distinct
  modality facets, or recorded error decorrelation). "The task is complex" is
  not a reason. `Join` requires a *registered merge algebra* for the branches'
  common output type — no merge, no join, and majority voting is one registered
  merge among several rather than the silent default.
- **Certification instead of tag overlap** (`ir/capability.py`). v1 bound a
  component to a node when a domain tag overlapped. Here `certification_level`
  is computed from executed probes, and a component cannot be bound to a subgoal
  demanding more certification than it has earned.
- **Declining structure is a first-class result.** `DecisionKind.DECLINE_STRUCTURE`
  exists so the compiler can conclude "one component covers every subgoal; no
  composition added", record why, and be *measured* on getting that right.

### 2. "Repair is unclear and decided by the LLM"

**Mechanism: Detect → Localize → Diagnose → Propose → Shadow-Validate →
Commit/Rollback**, with a taxonomy that constrains what may be proposed.

v1 collapsed symptom recognition, root-cause attribution, and action into one
step: `trigger == "test_failure" → action: retry_node`. But a failing test can
come from a bad prompt, bad generated code, a malformed upstream artifact, a
broken dependency, an incorrect grader, or a topology that never supplied the
information the node needed.

- **Localize** performs backward slicing over artifact lineage: start at the
  failing assertion, walk `Artifact.derived_from` backwards, re-validate each
  artifact against its own declared type, and blame the *earliest* artifact that
  fails its own check. A downstream component is never blamed for consuming a
  corrupted upstream artifact.
- **Diagnose** emits a ranked, normalized `Diagnosis` over ten fault classes,
  combining rule-based likelihoods with priors from recorded failure
  distributions, and retains contradicting signals. When
  `Diagnosis.entropy` exceeds the policy threshold the correct output is
  "gather more evidence", not a confident guess.
- **Propose** may only emit patch families listed in
  `FaultHypothesis.spec.admissible_patches`. An `ARTIFACT_CONTRACT` fault admits
  `insert_adapter` and `tighten_contract`; it does not admit `retry_node` or
  `rewrite_prompt`. An `EVALUATOR_FAILURE` cannot be answered by patching the
  thing being judged.
- **Shadow-validate** runs the patched workflow on reduced input in a snapshot
  and requires *both* that the original failure is gone *and* that no
  previously-passing check now fails. Components that are not `shadow_safe` are
  never speculatively executed; the transaction escalates instead.
- **Commit/Rollback** treats each patch as a transaction with a recorded
  pre-state, so the workflow is never left in a half-patched state.

### 3. "There is no optimization — repair only makes it run"

**Mechanism: three explicitly separated processes, over a utility vector.**

| Process | Goal | Trigger | Scope |
|---|---|---|---|
| Contract repair | restore executability and hard invariants | a definite failure | minimal implicated subgraph |
| Local optimization | improve utility once the contract holds | weak evidence, high cost, low robustness | node config, component choice, local branch |
| Global redesign | change the decomposition | the same fault class recurring | topology and subgoal structure |

v1 had only the first and called it repair. Escalation between tiers is
triggered by recurrence, not by a hunch.

Optimization does not require a scalar reward. `ir/utility.py` represents
quality as a vector — validity, evidence, robustness, scientific utility, cost,
latency, risk — compared by Pareto dominance. Selection from the non-dominated
set is a separate, recorded act (lexicographic by default; weighted only if the
user declares weights; `expert` mode hands the front back unselected).

Two properties are enforced because they are where this usually goes wrong:

- **An unmeasured objective earns no credit.** A dimension whose checks were all
  unavailable is marked `unavailable`, never set to 0.0. Scoring it 0 would make
  an unevaluable workflow look worse than a measurably bad one; scoring it 1
  would be worse still.
- **Incomparable candidates are reported as incomparable.** Better evidence at
  higher cost is not "better", and v1's hand-weighted score hid exactly that
  judgment inside constants nobody could defend.

For open-ended tasks the **Evaluation Contract Stack** replaces the missing
metric with six strata — hard, artifact, process, claim, preference, resource —
each with honest availability. `CheckStatus.UNAVAILABLE` is a distinct value
from `PASS`, everywhere. The final output is a `ClaimBundle` (claim, supporting
and contradicting evidence, assumptions, sensitivity analyses, alternative
explanations, uncertainty, provenance), so a genomics conclusion can be scored
on marker stability across seeds and thresholds, identifier-mapping coverage,
enrichment sensitivity to database choice, negative-control behaviour, and
traceability to specific cells and genes — none of which requires a ground-truth
answer.

#### Post-execution preference resolution

When objective checks leave several incomparable workflows, v2 may run
**Evidence-Constrained Preference Search (ECPS)**. This is a bounded,
non-gradient selection loop, not RL and not a claim that an LLM judge is an
oracle. Every compiler-admissible candidate is executed on the same case set;
runtime gates remove invalid, incomplete, silently failing, or over-budget
candidates before a judge sees them. The survivors form an observed Pareto
front with one common measurement mask and cost unit.

An optional injected judge panel then follows a forced protocol: read anonymous
outcome packets, generate one operational criterion per dossier preference,
complete an evidence-citing scorepad, and repeat with candidate order reversed.
Two distinct model families are required for selection. Criterion-wise
Bradley–Terry models schedule uncertain comparisons and select only a candidate
that conservatively dominates every rival and remains selected when either
family is removed. Scores are never summed across preferences.

Judge output lives in `ir/preference.py`, separate from design evidence,
objective checks, and utility. It cannot justify topology, change a
`UtilityVector`, or prove scientific correctness. Cycles, position bias,
unavailable evaluators, incomplete coverage, or exhausted budgets therefore
produce a typed unresolved front. A deterministic verbosity budget may reject
outputs that exceed a declared baseline multiplier before judging. Optional
search is limited to one-key `Atomic` configuration changes backed by real
schema/smoke/resource probes; topology or component changes return through the
compiler. See [the full ECPS specification](ecps_preference_search.md).

### 4. "Why not just use Codex or Claude Code?"

**Mechanism: treat them as executor backends, and measure the difference.**

Claiming "we support multiple agents / parallel execution / tool calls /
sandboxed repos / typed messages / long-horizon runs" is no longer a
contribution; those are platform features of any modern coding agent. The
defensible layer is above them:

| A general coding agent does | AgentCo-Op does |
|---|---|
| completes a task the user specified inside a workspace | decides which independently developed agents, tools, and repositories should be involved at all |
| spawns subagents when the harness or user says so | derives from evidence whether multiple components are warranted, and declines composition when they are not |
| verifies by tests, diffs, and user feedback | verifies by hard invariants, artifact contracts, robustness, claim provenance, and expert preference |
| debugs and retries on failure | localizes blame to a node/edge/artifact, ranks root causes, shadow-validates the patch, and rolls back |
| optimizes task completion | maintains a Pareto set when no scalar reward exists |

`components/coding_agent.py` makes a coding agent a *node*. The comparison
harness runs four arms: (1) coding agent alone; (2) coding agent given
AgentCo-Op's capability cards, typed contracts, and compiled blueprint;
(3) AgentCo-Op using the coding agent as its executor; (4) AgentCo-Op with
native adapters. Arm 2 tests whether the artifacts carry the value; arm 3 tests
whether the layers compose rather than compete.

### 5. "The benchmarks are outdated and do not show multi-agent value"

**Mechanism: measure composition value directly, including when it is negative.**

Six-way accuracy on HotpotQA/DROP/HumanEval/MBPP/GSM8K/MATH cannot support the
claims above; v1's own ablation showed removing local repair cost 0.8 points and
removing skills and tools a further 0.9, which says those benchmarks barely
exercise the mechanisms. They are demoted to a sanity check.

`bench/` is built around three task regimes — `single_sufficient`,
`multi_necessary`, and `multi_harmful`. The third is essential: without tasks
where composition is measurably *worse*, "the compiler correctly declined to
build a multi-agent workflow" is unmeasurable, and a reviewer will reasonably
ask why the system does not simply always add agents.

`bench/faults.py` injects *known* faults with recorded ground-truth blame
(identifier-namespace corruption, dependency break, silent empty output, stale
cache reuse, miscalibrated grader, needless serialization), which is what makes
localization accuracy, diagnosis top-k accuracy, patch precision, and collateral
regression measurable rather than asserted.

Baselines include `BestSingleComponent`, `EqualBudgetSingleAgent`,
`SameWorkflowSingleAgent` (one model executing the same steps — the
counterfactual for whether separate processes were needed at all), and
`CodingAgentBaseline`. Synergy is reported as

```
Synergy(W) = HV(u(W)) − max_j HV(u(W_j^single))
```

under one shared normalization, and it is reported when negative.

Which *external* benchmarks are worth running is a separate question, answered
in `docs/experiments/feasibility.md` — several of the candidates proposed in
review do not appear to exist in runnable form, and that had to be checked
before committing to them.

---

## What is deliberately not claimed

- No claim that the compiler finds a globally optimal workflow. It searches a
  bounded, grammar-constrained candidate set and reports a Pareto front.
- No claim that diagnosis is correct in general — it is *measured* against
  injected ground truth, and the false-repair rate is reported alongside
  detection recall.
- No claim that the six-level evaluation stack substitutes for a real oracle
  where one exists. Where a deterministic grader exists, it dominates.
- No claim that composition helps. That is the measured quantity, and the
  benchmark contains tasks where the honest answer is that it does not.
