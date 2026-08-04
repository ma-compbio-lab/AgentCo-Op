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

The most important thing it can do is **decline to build a multi-agent workflow**. On tasks where a
single component suffices, composing is a measurable mistake, and the benchmark scores it as one.

## The five things this design is answering

| Objection | Mechanism |
|---|---|
| Node selection, roles, and topology are just an LLM's imagination | [`ir/evidence.py`](agentcoop/ir/evidence.py) — every decision carries a `DesignEvidenceRecord`; an LLM proposal records as `ASSERTED` and **can never** make a decision admissible. [`compile/grammar.py`](agentcoop/compile/grammar.py) — each production has a stated applicability condition that must be discharged. |
| Repair is unclear and entirely LLM-decided | [`ir/faults.py`](agentcoop/ir/faults.py) — a 10-class fault taxonomy where each class constrains which patch families are admissible. [`diagnose/localize.py`](agentcoop/diagnose/localize.py) — backward slicing over artifact lineage. [`repair/shadow.py`](agentcoop/repair/shadow.py) — a patch commits only if it fixed the symptom **and** regressed nothing. |
| There is no optimization, only "make it run" | [`ir/utility.py`](agentcoop/ir/utility.py) — Pareto selection over seven objectives with no scalarization. Unmeasured dimensions earn no credit. [`evaluate/`](agentcoop/evaluate/) — a six-level contract stack where `UNAVAILABLE` is never `PASS`, so open-ended tasks with no oracle are handled rather than faked. |
| Why not just use Codex / Claude Code? | [`components/coding_agent.py`](agentcoop/components/coding_agent.py) — a coding agent is a *node type*, not a rival. [`bench/baselines.py`](agentcoop/bench/baselines.py) — four comparison arms, including the coding agent alone and AgentCo-Op using it as executor. |
| Benchmarks are too easy and don't show multi-agent advantage | [`bench/`](agentcoop/bench/) — three regimes (`single_sufficient`, `multi_necessary`, `multi_harmful`), ground-truth blame targets for injected faults, and silent faults that no exit-code-reading system can find. See [feasibility review](docs/experiments/feasibility.md) for why the public benchmarks were rejected. |

## Install

```bash
git clone https://github.com/ma-compbio-lab/AgentCo-Op.git
cd AgentCo-Op
pip install -e .[dev]                  # core + pytest
pip install -e .[dev,bench,repo]       # + datasets/pandas + docker/gitpython
```

Requires Python ≥ 3.11. **Every core mechanism runs offline with no API key.** The compiler,
diagnoser, and repairer are deterministic; LLM components are one adapter among several, never a
dependency of the method.

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

| Package | What it owns |
|---|---|
| [`ir/`](agentcoop/ir/) | The typed IR everything compiles against: artifacts with semantic facets, capability cards, design evidence, the workflow grammar, the fault taxonomy, Pareto utility. No I/O, no LLMs. |
| [`probe/`](agentcoop/probe/) | Executable certification. Six probe kinds; `invalid_input` is the one that matters — a component that returns success on garbage records a silent `FailureSignature` and can never reach `CERTIFIED`. |
| [`compile/`](agentcoop/compile/) | Grammar-constrained synthesis. Enumerate candidates (always including the single-component one), justify each production, run 12 static checks, estimate utility, take the Pareto front, select. |
| [`execute/`](agentcoop/execute/) | Typed execution with full artifact lineage. Every handoff is validated at the edge, before the consumer runs. |
| [`diagnose/`](agentcoop/diagnose/) | Detect signals → backward-slice to the earliest bad artifact → rank fault hypotheses with an entropy that says when *not* to act. |
| [`repair/`](agentcoop/repair/) | Three tiers (contract repair / local optimization / global redesign), 25 patch families constrained by fault class, transactional commit with rollback. |
| [`evaluate/`](agentcoop/evaluate/) | The six-level contract stack (hard / artifact / process / claim / preference / resource). Levels are never collapsed into a scalar. |
| [`components/`](agentcoop/components/) | The invocation boundary: LLM, container, subprocess, Python function, converter, evaluator, human, coding agent. |
| [`bench/`](agentcoop/bench/) | Task suites, fault injection with ground-truth blame, and the four baseline arms. |

Two design documents carry the full argument:
[`docs/architecture/v2_method.md`](docs/architecture/v2_method.md) maps each objection to its
mechanism; [`docs/architecture/v2_interfaces.md`](docs/architecture/v2_interfaces.md) is the
interface contract the implementation was written against.

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
pytest                                     # ~535 tests, ~1s, fully offline
pytest tests/unit/test_probe.py -v
pytest tests/integration -q                # the cross-subsystem properties
pytest -k "silent or regime"
```

`asyncio_mode = "auto"`, so async tests need no marker.

## Relationship to the published version

The version of AgentCo-Op described in the paper below — task profiling, skill retrieval, gate-based
local repair, and the external-repo `collaborate` pipeline — is preserved unchanged under
[`legacy/`](legacy/), because it is the reproducibility record for the numbers already reported. The
benchmark table on the [project page](https://ma-compbio-lab.github.io/AgentCo-Op#benchmarks) and
the two genomics case studies were produced by that code, not by this one.

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
