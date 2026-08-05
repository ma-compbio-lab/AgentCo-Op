<div align="center">
  <img src="https://ma-compbio-lab.github.io/AgentCo-Op/assets/icon.png" alt="AgentCo-Op" width="96" />

  <h1>AgentCo-Op</h1>

  <p>
    <b>Retrieval-Based Synthesis of Interoperable Multi-Agent Workflows</b>
  </p>

  <p>
    <a href="https://ma-compbio-lab.github.io/AgentCo-Op">Project page</a>
    &nbsp;·&nbsp;
    <a href="https://ma-compbio-lab.github.io/AgentCo-Op/assets/paper/paper.pdf">Paper (PDF)</a>
    &nbsp;·&nbsp;
    <a href="#coordinating-external-agents">Coordinate external agents</a>
    &nbsp;·&nbsp;
    <a href="#citation">Citation</a>
  </p>

  <p>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg" alt="Python ≥3.11"></a>
    <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  </p>

  <p>
    <b>Shuaike Shen</b><sup>1,*</sup> ·
    <b>Wenduo Cheng</b><sup>1,*</sup> ·
    <b>Shike Wang</b><sup>1</sup> ·
    <b>Mingqian Ma</b><sup>2</sup> ·
    <b>Jian Ma</b><sup>1,†</sup>
  </p>

  <p>
    <sup>1</sup> Ray &amp; Stephanie Lane Computational Biology Department, School of Computer Science, Carnegie Mellon University<br>
    <sup>2</sup> Machine Learning Department, School of Computer Science, Carnegie Mellon University
  </p>

  <p>
    <sup>*</sup> Equal contribution &nbsp;·&nbsp;
    <sup>†</sup> Correspondence: <a href="mailto:jianma@cs.cmu.edu">jianma@cs.cmu.edu</a>
  </p>

  <p>
    <img src="https://ma-compbio-lab.github.io/AgentCo-Op/assets/paper/concept.png" alt="AgentCo-Op framework overview" width="780" />
  </p>
  <sub>
    Figure 1 — AgentCo-Op synthesizes multi-agent workflows through five stages (Planning, Retrieval, Synthesis, Execution, Review). Given a typed task specification it retrieves relevant skills, tools, repositories, and datasets, then synthesizes an executable workflow graph and repairs only the components implicated by execution evidence.
  </sub>
</div>

---

## What this is

Designing multi-agent workflows is hard in open-ended scientific settings, where tasks have no
curated training set, no reliable scalar metric, and no standard interface between the tools and
agents that already exist.

**AgentCo-Op** is a compiler for heterogeneous agent workflows built on one commitment: *every
structural decision must rest on evidence that was produced by execution, not asserted by a model.*
A component's capabilities are established by running probes against it. A node exists because a
subgoal requires it and a certified component can serve it. An edge exists because the producer's
artifact type is compatible with the consumer's — structurally *and* semantically. A repair happens
because a diagnosis localized a fault to a specific artifact, and only after a shadow run showed the
patch fixed the symptom without breaking anything that was working.

Where the evidence does not exist, the system says so and stops. It refuses to compile a defective
specification, refuses to bind an uncertified component, refuses a join with no merge algebra,
refuses to patch on an uncertain diagnosis, and refuses to report a workflow as successful when a
node returned exit code 0 with an empty result.

When several valid workflows remain incomparable after execution, optional
**Evidence-Constrained Preference Search (ECPS)** compares only that objective Pareto front. It
uses anonymous outcome packets, generated rubrics, evidence-citing scorepads, order swapping, and
two judge families. Preference is a noisy selection signal—not design evidence or scientific
truth—so cycles, unavailable judges, and weak coverage return an explicit unresolved front.

The most important thing it can do is **decline to build a multi-agent workflow**. On tasks where a
single component suffices, composing is a measurable mistake, and the benchmark scores it as one.

## The five things this design is answering

| Objection | Mechanism |
|---|---|
| Node selection, roles, and topology are just an LLM's imagination | [`ir/evidence.py`](agentcoop/ir/evidence.py) — every decision carries a `DesignEvidenceRecord`; an LLM proposal records as `ASSERTED` and **can never** make a decision admissible. [`compile/grammar.py`](agentcoop/compile/grammar.py) — each production has a stated applicability condition that must be discharged. |
| Repair is unclear and entirely LLM-decided | [`ir/faults.py`](agentcoop/ir/faults.py) — a 10-class fault taxonomy where each class constrains which patch families are admissible. [`diagnose/localize.py`](agentcoop/diagnose/localize.py) — backward slicing over artifact lineage. [`repair/shadow.py`](agentcoop/repair/shadow.py) — a patch commits only if it fixed the symptom **and** regressed nothing. |
| There is no optimization, only "make it run" | [`ir/utility.py`](agentcoop/ir/utility.py) — Pareto selection over seven objectives with no scalarization; unmeasured dimensions earn no credit. [`optimize/`](agentcoop/optimize/) — matched execution, objective gating, order-swapped rubric/scorepad judgments, criterion-wise preference inference, and probe-authorized config search. Preference never becomes evidence, and unresolved is a valid result. |
| Why not just use Codex / Claude Code? | [`components/coding_agent.py`](agentcoop/components/coding_agent.py) — a coding agent is a *node type*, not a rival. [`bench/baselines.py`](agentcoop/bench/baselines.py) — four comparison arms, including the coding agent alone and AgentCo-Op using it as executor. |
| Benchmarks are too easy and don't show multi-agent advantage | [`bench/`](agentcoop/bench/) — three regimes (`single_sufficient`, `multi_necessary`, `multi_harmful`), ground-truth blame targets for injected faults, and silent faults that no exit-code-reading system can find. See [feasibility review](docs/experiments/feasibility.md) for why the public benchmarks were rejected. |

## Install

```bash
git clone https://github.com/ma-compbio-lab/AgentCo-Op.git
cd AgentCo-Op
pip install -e .[dev]                  # everything, plus pytest
```

Requires Python ≥ 3.11. **Every core mechanism runs offline with no API key.** The compiler,
diagnoser, and repairer are deterministic; LLM components are one adapter among several, never a
dependency of the method. The dependency list is four packages and includes no numerical or ML
stack — that is a property of the design, not an omission.

## Quick start

```bash
agentcoop bench list                       # tasks, regimes, injected faults
agentcoop probe   --task syn-multi         # certify components by executing probes
agentcoop compile --task syn-multi         # candidates, evidence, Pareto front, choice
agentcoop run     --task syn-silent-empty --out runs/demo
agentcoop diagnose runs/demo               # detect -> localize -> diagnose
agentcoop repair  runs/demo                # bounded, shadow-validated repair
agentcoop bench run                        # every system on every task
```

`agentcoop probe` prints what a component actually earned, and why it stopped there:

```
mapper: CERTIFIED
  [ok  ] DECLARED  declares an input or output contract
  [ok  ] REACHABLE was executed and its entrypoint resolved
  [ok  ] PROBED    emits its declared outputs, structurally valid and non-vacuous
  [ok  ] CERTIFIED refuses malformed input and honours a stated budget
  [no  ] TRUSTED   has a reliability record over real runs of this task family
              - only 0 real-run observations recorded; 5 are required
                (probes do not count — reliability is earned in deployment)
```

`agentcoop run` distinguishes "the workflow completed" from "the result is good":

```
outputs  : report
run completed, but 1 blocking signal(s) say the result should not be trusted:
  [critical] empty_output: 'finding_set' from 'analyze__analyst' is empty
             at results while the component reported success
```

## Architecture

```
dossier ──▶ probe ──▶ compile ──▶ execute ──▶ diagnose ──▶ repair
   │          │          │           │           │           │
lint for   earn a    enumerate,   typed      detect,    propose,
spec       certifi-  justify,     handoffs   localize,  shadow-
defects    cation    analyse,     with       rank       validate,
           by        Pareto-      lineage    hypotheses commit or
           running   select                             roll back
```

After matched candidate executions, `optimize/` may branch from `execute/` to resolve the observed
Pareto front; it returns either a stable candidate or an audited unresolved set.

| Package | What it owns |
|---|---|
| [`ir/`](agentcoop/ir/) | The typed IR everything compiles against: artifacts with semantic facets, capability cards, design evidence, the workflow grammar, the fault taxonomy, Pareto utility. No I/O, no LLMs. |
| [`probe/`](agentcoop/probe/) | Executable certification. Six probe kinds; `invalid_input` is the one that matters — a component that returns success on garbage records a silent `FailureSignature` and can never reach `CERTIFIED`. |
| [`compile/`](agentcoop/compile/) | Grammar-constrained synthesis. Enumerate candidates (always including the single-component one), justify each production, run 12 static checks, estimate utility, take the Pareto front, select. |
| [`execute/`](agentcoop/execute/) | Typed execution with full artifact lineage. Every handoff is validated at the edge, before the consumer runs. |
| [`optimize/`](agentcoop/optimize/) | Optional non-gradient preference search over an execution-admissible Pareto front. The judge is dependency-injected; there is no default live provider. |
| [`diagnose/`](agentcoop/diagnose/) | Detect signals → backward-slice to the earliest bad artifact → rank fault hypotheses with an entropy that says when *not* to act. |
| [`repair/`](agentcoop/repair/) | Three tiers (contract repair / local optimization / global redesign), 25 patch families constrained by fault class, transactional commit with rollback. |
| [`components/`](agentcoop/components/) | The invocation boundary, and the four adapters that cross it: Python function, subprocess, container, and headless coding agent. |
| [`bench/`](agentcoop/bench/) | Task suites, fault injection with ground-truth blame, and the four baseline arms. Also where the six check levels are reported per level, by `contract_summary`. |

Two design documents carry the full argument:
[`docs/architecture/v2_method.md`](docs/architecture/v2_method.md) maps each objection to its
mechanism; [`docs/architecture/v2_interfaces.md`](docs/architecture/v2_interfaces.md) is the
interface contract the implementation was written against. The precise ECPS IR, state machine,
budgets, and adversarial controls are in
[`docs/architecture/ecps_preference_search.md`](docs/architecture/ecps_preference_search.md).

## What the benchmark measures

A benchmark on which "build a multi-agent workflow" is always right cannot measure whether a system
knows *when* to build one. So every task declares a regime:

| Regime | Correct behaviour | What it rules out |
|---|---|---|
| `single_sufficient` | Use one component | A system that composes reflexively |
| `multi_necessary` | Compose — no single component can produce the answer | A system that only ever does the simple thing |
| `multi_harmful` | Decline to compose; composing loses data | A system whose "knows when to stop" is a claim rather than a result |

Task success and the composition decision are scored **separately**. A system that gets the right
answer by composing four components where one would do has succeeded at the task and failed at the
decision, and both appear in the table.

Faults are injected with the blame target written down in advance, so localization accuracy is
measurable rather than asserted. Half the mechanisms are *silent* — namespace corruption, empty
output with exit code 0, stale cache, a grader that passes everything — because loud faults only
measure whether a system reads exit codes.

An arm that cannot run in the current environment reports `n/a` with the reason. It is never
scored zero, and it is never quietly dropped from the table.

> **Note.** No experimental results are reported here yet. The harness is built; which external
> benchmarks are even feasible is reviewed in
> [`docs/experiments/feasibility.md`](docs/experiments/feasibility.md), which documents why no
> existing public benchmark structurally requires heterogeneous composition.

## Testing

```bash
pytest                                     # complete, fully offline suite
pytest tests/unit/test_probe.py -v
pytest tests/integration -q                # the cross-subsystem properties
pytest -k "silent or regime"
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin \
  tests/integration/test_preference_optimization.py -q
```

`asyncio_mode = "auto"`, so async tests need no marker. The last command runs the ECPS integration
contract with only the repository's asyncio plugin enabled.

## Relationship to the published version

The version of AgentCo-Op described in the paper below — task profiling, skill retrieval, gate-based
local repair, and the external-repo `collaborate` pipeline — is a different system from this one.
The benchmark table on the [project page](https://ma-compbio-lab.github.io/AgentCo-Op#benchmarks)
and the two genomics case studies were produced by that code, and none of those results carry over.

That code is no longer in the working tree. It remains in git history at commit `94c3750` on this
branch, which is the reproducibility record for the published numbers:

```bash
git show 94c3750:README.md          # the v1 README
git checkout 94c3750 -- agentcoop   # restore v1 into the working tree
```

## Citation

If AgentCo-Op is useful in your work, please cite:

```bibtex
@article{shen2026agentcoop,
      title={AgentCo-op: Retrieval-Based Synthesis of Interoperable Multi-Agent Workflows}, 
      author={Shuaike Shen and Wenduo Cheng and Shike Wang and Mingqian Ma and Jian Ma},
      year={2026},
      eprint={2605.20425},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.20425}, 
}
```

## License

Apache-2.0 (declared in [`pyproject.toml`](pyproject.toml)).

## Acknowledgements

AgentCo-Op composes independently developed scientific agents and tool repositories without redesigning them. We thank the maintainers of TissueAgent, GeneAgent, Seurat, Signac, GEARS, scGPT, scFoundation, Geneformer, and AFlow whose open code makes this kind of synthesis possible.
