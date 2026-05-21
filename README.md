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

## Abstract

Designing multi-agent workflows is especially difficult in open-ended scientific settings where tasks lack curated training sets, reliable scalar evaluation metrics, and standardized interfaces between existing tools and agents.

We propose **AgentCo-Op**, a retrieval-based synthesis framework that composes reusable skills, tools, and external agents into executable workflows through typed artifact handoffs, then applies bounded self-guided local repair to implicated components when execution evidence indicates failure. In two open-world genomics case studies, AgentCo-Op composes independently developed scientific agents and external tool repositories into auditable workflows without redesigning them or running global topology search.

It coordinates specialized agents for spatial transcriptomics and gene-set interpretation to enable collaborative discovery from spatial transcriptomics data, and builds a parallel workflow for cross-modality marker analysis on single-cell multiome data. AgentCo-Op can also import a searched workflow as a structural prior and improve it by grounding nodes with retrieved components and applying local repair, showing that synthesis and search are complementary. On six coding, math, and question-answering benchmarks, AgentCo-Op achieves the best result on four benchmarks and the best average score under a unified backbone setting, while consistently reducing per-task cost relative to multi-agent baselines.

## At a Glance

| | |
|---|---|
| **4 / 6** | best on benchmarks (matched backbone) |
| **80.6%** | best average score (GPT-4o-mini, matched backbone) |
| **87.1%** | MBPP pass@1 (best across all reported methods) |
| **3** | open-world case studies in independently developed scientific repos |

Per-task cost is lower than ReConcile on all six benchmarks and lower than LLM-Debate on five of six — AgentCo-Op separates one-time synthesis from bounded instance-level repair, avoiding both training-time search and per-instance multi-agent round-trips. See the [project page](https://ma-compbio-lab.github.io/AgentCo-Op#benchmarks) for full tables.

## Method

AgentCo-Op reframes automated multi-agent workflow design as **retrieval-based synthesis**: rather than searching over candidate topologies against a scalar reward, it composes reusable components into a task-specific workflow, coordinates them through typed artifact handoffs, and repairs implicated components from execution evidence. The pipeline runs in five stages:

1. **Planning** — parse the typed task specification `x = (g, c, r, Ω)` (goal, context, resources, constraints) and formulate a retrieval plan.
2. **Retrieval** — pull task-relevant artifacts from curated libraries and user-provided repositories: skills (procedural knowledge), tools (callable operations), GitHub repos, and reference materials.
3. **Synthesis** — build the executable workflow graph `G = (V, E)`: initial topology, Docker / executor wrapping for external repos, node grounding with retrieved skills and tools, typed message and artifact schemas.
4. **Execution** — run the synthesized workflow while a reviewer monitors execution evidence — logs, intermediate outputs, validation signals, tool errors, interface checks, and cost.
5. **Review & Bounded Local Repair** — on failure or uncertainty, revise *only* the implicated nodes, attached skills/tools, or communication edges, producing a patched graph `G' = (V', E')` instead of restarting synthesis.

## Install

```bash
git clone https://github.com/ma-compbio-lab/AgentCo-Op.git
cd AgentCo-Op
pip install -e .[dev]                  # core + dev
pip install -e .[dev,bench,repo]       # add benchmarks + Docker / external-repo extras
```

Requires Python ≥ 3.11. The `[repo]` extra enables real external-agent coordination (clones GitHub repos and optionally builds Docker images); without it the `collaborate` command still works in `--no-docker` mode.

## Quick start (framework basics)

```bash
# Profile a task and compile the minimum sufficient workflow.
agentcoop compile --task "What is 13 * 17?" --out runs/demo/blueprint.json

# Execute the blueprint with the mock backend (no API keys needed).
agentcoop run --blueprint runs/demo/blueprint.json --out runs/demo
```

Set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` to run the same blueprint against a real LLM through `agentcoop run-benchmark`.

## Coordinating external agents

AgentCo-Op's defining feature is treating *whole GitHub repositories* — TissueAgent, GeneAgent, Seurat, Signac, GEARS, scGPT, ... — as typed workflow nodes. You declare which repos and what task you want; the framework profiles each repo, builds an isolated sandbox, registers it as a node, synthesizes a collaboration graph between them, and runs the graph end-to-end with typed handoffs.

### One command

```bash
agentcoop collaborate \
  --request docs/agents/case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish \
  --no-docker            # use --docker on a host with a running daemon
```

This drives the entire pipeline:

1. **Repo retrieval & profiling** — clones each repository under `external/`, reads its README / pyproject / requirements, infers entry points and runtime image, and writes a `manifests/repo_profile_<name>.json`.
2. **Sandbox synthesis** — generates a Dockerfile and `docker-compose.yml` per repo (or uses the local Python env when `--no-docker`).
3. **Node registration & graph synthesis** — registers each repo as an agent backend, then composes a workflow graph from the request's `topology` (default: broker-mediated handoff; `parallel_then_join` is built in for two-modality fan-out + integrator patterns).
4. **Execution with typed handoffs** — runs each node, validating inputs / outputs against typed schemas, and threading artifacts through the blackboard.
5. **Review & bounded local repair** — when a node fails or a gate fires, repairs only that node, its skill bindings, or the edge that produced the offending artifact.

A run produces `compiled_workflow_graph.json`, `agent_registry.json`, per-agent artifacts under `artifacts/`, a `traces/execution_trace.jsonl`, and a `run_manifest.json` for reproducibility.

### Anatomy of a request YAML

You bring (a) GitHub URLs of the repos you want to coordinate and (b) a task description. AgentCo-Op derives the rest.

```yaml
# docs/agents/<your-task>.request.yaml
case_id: tissueagent_geneagent_external_collaboration
mode: autonomous_repo_wrapping
model: gpt-5
reasoning_effort: medium

repositories:
  - name: TissueAgent
    url: https://github.com/ma-compbio/TissueAgent
    role_hint: Spatial transcriptomics differential-expression specialist
  - name: GeneAgent
    url: https://github.com/ncbi-nlp/GeneAgent
    role_hint: Gene-set interpretation + self-verified annotation specialist

dataset:
  name: developing_human_heart_merfish_farah_2024
  local_cache_dir: data/heart_merfish
  preferred_files:
    - overall_merfish.h5ad
    - README.md

task:
  title: AVN/AV ring aFibro developmental-program test
  description: >
    Test whether atrial fibroblasts (aFibro) in the AVN/AV ring exhibit a
    distinct program compared with aFibro in Left/Right Atria. Use
    TissueAgent for spatial-transcriptomics differential expression and
    GeneAgent for gene-set interpretation.

required_outputs:
  - artifacts/tissueagent_run/de_results.csv
  - artifacts/tissueagent_run/avn_avring_marker_genes.json
  - artifacts/geneagent_run/geneagent_report.md
  - artifacts/integration/final_hypothesis_report.md
```

For two-modality cross-evaluation (Case Study 2), add `topology: parallel_then_join` and a `join_agent:` block that names the integrator — see [`docs/agents/case_study_2.request.yaml`](docs/agents/case_study_2.request.yaml).

### Shipped examples

| Request file | What it does |
|---|---|
| [`docs/agents/case_study_1.request.yaml`](docs/agents/case_study_1.request.yaml) | TissueAgent × GeneAgent on developing-heart MERFISH (broker-mediated handoff). |
| [`docs/agents/case_study_2.request.yaml`](docs/agents/case_study_2.request.yaml) | Seurat × Signac on SHARE-seq mouse skin multiome (parallel_then_join with a CellMarker / PanglaoDB integrator). |
| [`docs/agents/case_study_2_human_heart.request.yaml`](docs/agents/case_study_2_human_heart.request.yaml) | Same topology, 10x human-heart multiome. |
| [`docs/agents/case_study_2_lite.request.yaml`](docs/agents/case_study_2_lite.request.yaml) | Lightweight CS2 fixture for offline runs. |

### Wrap a single external repository

When you just want a typed adapter without orchestration, scaffold a wrapper manifest:

```bash
agentcoop repo wrap \
  --repo  https://github.com/ncbi-nlp/GeneAgent \
  --commit a1b2c3d \
  --name   geneagent \
  --out    agentcoop/wrappers/geneagent/manifest.yaml
```

The resulting `manifest.yaml` is consumed by the `sandbox_repo` backend and can be referenced as a skill in any compiled workflow.

## Case studies & benchmarks

- **Case Study 1 — Coordinating Domain Agents** · TissueAgent × GeneAgent. See [`docs/experiments/case_study_1.md`](docs/experiments/case_study_1.md).
- **Case Study 2 — Composing Domain Workflows** · Seurat × Signac parallel cross-modal marker discovery. See [`docs/experiments/case_study_2.md`](docs/experiments/case_study_2.md).
- **Case Study 3 — Reusing Existing Agent Graphs** · AFlow → AgentCo-Op hybrid (MBPP 87.5). See [`docs/experiments/case_study.md`](docs/experiments/case_study.md) and `scripts/case3_aflow_dynamic.py`.
- **Standard benchmarks** · HotpotQA, DROP, HumanEval, MBPP, GSM8K, MATH. Run with:
  ```bash
  agentcoop run-benchmark --dataset mbpp --limit 10 -v AC-Gated
  ```

Headline matched-backbone numbers (GPT-4o-mini):

| Method | HotpotQA | DROP | HumanEval | MBPP | GSM8K | MATH | Avg |
|---|---:|---:|---:|---:|---:|---:|---:|
| AFlow (GPT-4o-mini) | 71.4 | 68.9 | 89.3 | 78.2 | 86.8 | 53.1 | 74.3 |
| LLM-Debate | 71.8 | 81.4 | **91.4** | 70.7 | 92.4 | 50.0 | 76.3 |
| ReConcile | 73.8 | **82.1** | 89.3 | 70.3 | 93.7 | 44.1 | 75.6 |
| **AgentCo-Op (GPT-4o-mini)** | **76.5** | 77.2 | 90.2 | **87.1** | **94.4** | **58.2** | **80.6** |

See [the project page](https://ma-compbio-lab.github.io/AgentCo-Op#benchmarks) for the full table and per-dataset cost breakdown.

## Repository layout

```
agentcoop/
  core/        schemas, compiler, runtime, gates, tracing, reviewer, integrator
  skills/      meta-skill markdown + agent-skill YAML cards
  backends/    llm, mcp, python_sandbox, repo_sandbox, human_review
  memory/      blackboard, artifact_store, trace_store, skill_memory
  wrappers/    per-repo docker/adapter bundles (GeneAgent, Seurat, Signac, GEARS, ...)
  benchmarks/  graders + the matched-backbone benchmark runner
configs/       benchmark / case-study / gate / skill configs
docs/
  agents/      external-agent collaboration request YAMLs (the inputs to `agentcoop collaborate`)
  experiments/ case-study walkthroughs
scripts/       driver scripts (case3_aflow_dynamic.py, etc.)
tests/         unit + integration tests
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
