# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Research artifact for the AgentCo-Op paper. The `revision` branch is a **ground-up rebuild** (v2)
of the method. The published version (v1) is preserved untouched under [legacy/](legacy/) because
it is the reproducibility record for numbers already in the paper — do not "fix" anything in there,
and do not import from it.

v2 is a compiler for heterogeneous agent workflows built on one rule: **every structural decision
must rest on evidence produced by execution, never asserted by a model.** Most of the surprising
code in this repo exists to enforce that rule against the easier alternative.

## Commands

```bash
pip install -e .[dev]                 # core + pytest
pytest                                # ~535 tests, ~1s, fully offline
pytest tests/unit/test_probe.py -v
pytest tests/integration -q
pytest -k "silent or regime"
```

`asyncio_mode = "auto"` is set in [pyproject.toml](pyproject.toml) — async tests need no marker.
No linter/formatter is configured; match surrounding style. Every core mechanism is deterministic
and needs no API key.

```bash
agentcoop bench list                       # tasks, regimes, injected faults
agentcoop probe   --task syn-multi         # certify by executing probes
agentcoop compile --task syn-multi         # candidates, evidence, Pareto, choice
agentcoop run     --task syn-silent-empty --out runs/demo
agentcoop diagnose runs/demo               # works offline from runs/demo/run.json
agentcoop repair  runs/demo                # needs live components (shadow validation)
agentcoop bench run --system agentcoop --system best_single_component
agentcoop dossier lint path/to/dossier.yaml
agentcoop explain runs/demo/workflow.json
```

## Architecture

```
dossier ─▶ probe ─▶ compile ─▶ execute ─▶ diagnose ─▶ repair
```

Each stage is a package; [ir/](agentcoop/ir/) is the typed IR they all compile against and has no
I/O and no LLM dependency. [docs/architecture/v2_interfaces.md](docs/architecture/v2_interfaces.md)
is the interface contract the implementation was written against;
[docs/architecture/v2_method.md](docs/architecture/v2_method.md) maps each reviewer objection to
the mechanism that answers it. Read those before making a design change.

### The invariants that most edits will be tempted to break

- **`UNAVAILABLE` is never `PASS`.** A check that could not run does not satisfy anything
  ([ir/checks.py](agentcoop/ir/checks.py)). `certification_report` emits an entry for every probe
  kind including the ones never run, precisely so the gap is visible.
- **`Compatibility.UNDERSPECIFIED` is not compatible.** A producer that does not declare a facet
  the consumer requires is a compile-time failure, not an optimistic wiring
  ([ir/artifacts.py](agentcoop/ir/artifacts.py)).
- **`certification_level` is a computed property, never assigned.** It is derived from executed
  probes. `probe/certificate.py::derived_level` recomputes it from the same ladder the explanation
  prints; a test pins the two together, so if you change one you must change both.
- **An LLM assertion records as `EvidenceStrength.ASSERTED` and can never make a decision
  admissible** ([ir/evidence.py](agentcoop/ir/evidence.py)). Only `DERIVED`/`OBSERVED`/`STATISTICAL`
  are load-bearing.
- **`Join` is unexpressible without a registered merge** ([merges.py](agentcoop/merges.py)).
  Voting is not a default; `majority_vote` requires ≥3 branches.
- **Repair must fix the symptom *and* regress nothing** ([repair/shadow.py](agentcoop/repair/shadow.py)).
  A component whose `BehaviorContract.shadow_safe` is False is never speculatively re-run.
- **`FaultSpec.admissible_patches` constrains repair.** A contract fault can never be "fixed" by
  `retry_node`; an evaluator fault never patches the generator ([ir/faults.py](agentcoop/ir/faults.py)).
- **Detectors must not trust the producer's self-report.** `detect_empty_output` recomputes
  emptiness from the payload and treats adapter notes as a supplement — a component that lies about
  succeeding is exactly the one that will not annotate its own emptiness. The same reasoning applies
  in `probe/runner.py::_smoke`.
- **Determinism**: no unseeded randomness, monotonic `Trace.step` rather than wall-clock, artifact
  content hashes over type+facets+payload only. Bench adapters default to `zero_clock`.

### Where the non-obvious coupling lives

- `probe/suite.py::_output_contexts` emits **one schema/smoke probe per declared output** when a
  card declares several. A multi-capability component (any generalist) emits one artifact per call,
  so a single probe would fail it for a reason unrelated to quality. The way to elicit each output
  is declared in `card.binding["probe_contexts"]`.
- `execute/engine.py::_gather_inputs` takes `required` from the **subgoal**, not from the card. A
  component certified for several capabilities declares the union of their inputs; treating all of
  them as mandatory would fail a correctly-behaving generalist.
- `bench/harness.py` certifies components **before** installing faults by default
  (`certify_before_injection=True`). Probing an already-broken component makes the compiler refuse
  to bind it, so no run happens and localization becomes unmeasurable. The other ordering is a
  different, also-valid measurement — it is just not the default.
- `bench/harness.py::blame_matches` allows an edge `A->B` to count as naming `A` or `B`, and an
  artifact id `producer::type::hash` to count as naming its producer. Both tolerances are stated
  once, structurally; `blame_exact` reports the strict rate beside it.

## Adding things

- **A new topology production** — add the rule to [compile/grammar.py](agentcoop/compile/grammar.py)
  with an explicit applicability condition, a term to [ir/workflow.py](agentcoop/ir/workflow.py),
  and a case in `lower()`. A production with no stated condition is the thing this rebuild removed.
- **A new component type** — implement the `ComponentAdapter` protocol in
  [components/](agentcoop/components/). Emit facets you actually observed via
  `finalize_outputs(observers=...)`; facets that come from static config are applied to the artifact
  but deliberately excluded from `observed_facets`.
- **A new fault class** — add to `FaultClass` *and* `FAULT_TAXONOMY` with its `admissible_patches`,
  plus a `LIKELIHOOD` row in [diagnose/diagnose.py](agentcoop/diagnose/diagnose.py).
- **A new benchmark task** — add to [bench/suites/](agentcoop/bench/suites/) as pure data. Declare
  its `regime` and, for any injected fault, the ground-truth `expected_blame`.
  `BenchSuite.coverage_defects()` will tell you what your suite cannot support.

## Gotchas

- `bench/behaviors.py` is a **registry populated by decorator**; a behaviour is invisible until its
  module is imported. `agentcoop.bench.behaviors` is the only module that defines them today.
- `bench run` on the synthetic suite is not an experiment and its numbers are not results — it is a
  self-test of the harness. Per the current plan, **do not run experiments**; see
  [docs/experiments/feasibility.md](docs/experiments/feasibility.md) for which external benchmarks
  are even feasible.
- Node ids are `<subgoal_id>__<component>`; artifact ids are `<producer>::<type>::<hash>`. Several
  tests and the blame matcher depend on those shapes.
- `data/`, `runs/`, `external/`, and `docs/agents/` are gitignored. `docs/agents/*.request.yaml` are
  referenced by the legacy docs but exist only in a local working copy.
- `Compiler.compile` refuses a dossier with specification defects unless `allow_defects=True`. An
  empty result from a compile is often this, not a regression — check `result.dossier_defects` first.
- Node failures inside the engine are captured as failed `NodeResult`s rather than raised.
