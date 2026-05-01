# Case Study 1 — TissueAgent × GeneAgent on real Farah MERFISH

Run produced by

```bash
agentcoop collaborate \
  --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

The CLI **handles all preparatory work internally**: it clones each
GitHub repo, profiles it, renders Dockerfiles + smoke tests +
`docker-compose.yml`, registers per-repo AgentCards, **auto-installs
any declared Python packages via the EnvManager** (records every
install/already-present in `manifests/env_manifest.json`), runs both
agents in upstream/downstream order, brokers the typed handoff,
integrates outputs with `gpt-5`, and writes structured reports.

Real-data inputs: `data/heart_merfish/overall_merfish.h5ad`
(Farah et al. 2024 developing-human-heart MERFISH AnnData,
228 635 cells × 238 genes).

## Where to look first

| File | What's in it |
|---|---|
| **`final_report.md`** | **Standalone Markdown report.** Embeds the topology PNG, env table, per-stage table, GeneAgent biology report (verbatim), integrator hypothesis (verbatim), every artifact path, and a reproduce block. Open this first. |
| **`topology.png`** | Multi-agent topology, hierarchical layout, color-by-kind. |
| **`topology.dot`** | Graphviz source for the same diagram (`dot -Tpng topology.dot -o topology.png`). |
| `collaboration_log.md` | Per-stage narrative (env / profile / sandbox / registry / upstream / broker / downstream / integrator) with status, timing, key artifacts. |
| `run_manifest.json` | Top-level outcome JSON; `structured_outputs` block points back to the four files above. |

## Top-line result on the real Farah MERFISH

| Metric | Value | `case_study_1.md` §13 target |
|---|---|---|
| Status | **`success`** (real data; not synthetic_fallback) | — |
| Wall time | ≈ 99 s | — |
| n_target / n_control cells | **576 / 5 685** | non-zero each |
| Marker count, Welch t (adj_p<0.05, log2fc>0) | **53** | 46 strict; 40–60 acceptable |
| Marker count, Mann-Whitney U | **46** ← exact manuscript match | 46 strict |
| Expected example marker overlap | **6 / 6** (DES, IGFBP5, NELL2, HAND2, MYH7, MYH6) | ≥ 5/6 strict |
| GeneAgent process label | "AV Ring Fibroblast Developmental Program" | "Cardiac Development and Remodeling" or eq. |
| GeneAgent subprocesses | 5 | ≥ 4 / 5 |
| Final integrator model | `gpt-5` (`reasoning_effort=medium`) | — |
| Total LLM tokens (GeneAgent + integrator) | 5 547 | — |

`de_sensitivity_summary.csv` records both Welch t (53) and
Mann-Whitney U (46); the latter exactly reproduces the
TissueAgent paper's reported 46-marker AVN/AV ring panel.

## Path conventions

- **Raw outputs root** (everything AgentCo-Op produced): `runs/case1/heart_merfish/`
- **Standalone processed report**: `runs/case1/heart_merfish/final_report.md`
- **Per-stage narrative**: `runs/case1/heart_merfish/collaboration_log.md`
- **Topology diagram (PNG / DOT)**: `runs/case1/heart_merfish/topology.png`, `topology.dot`

## Generated artifact tree

```
runs/case1/heart_merfish/
  run_manifest.json                            — top-level outcome (status: success)
  final_report.md                              — standalone, embeds topology + reports
  collaboration_log.md                         — per-stage narrative
  topology.png                                 — matplotlib hierarchical render
  topology.dot                                 — Graphviz source
  compiled_workflow_graph.json                 — L7 external_repo_collaboration nodes/edges
  agent_registry.json                          — AgentCards (auto-built from RepoProfile)
  manifests/
    repo_profile_TissueAgent.json
    repo_profile_GeneAgent.json
    docker_build_report.json                   — Dockerfiles written; daemon was down
    env_manifest.json                          — auto-resolved Python pkgs (9 packages)
    broker.jsonl                               — typed handoff audit log
  docker/
    Dockerfile.TissueAgent                     — generated from RepoProfile + SandboxSpec
    Dockerfile.GeneAgent
    docker-compose.yml
    smoke_TissueAgent.py
    smoke_GeneAgent.py
  artifacts/
    tissueagent_run/
      response.json
      dataset_schema_report.{json,md}
      group_definition_report.json
      group_counts.csv
      de_results.csv                           — 238 genes × Welch t / log2FC / adj-p
      de_sensitivity_summary.csv               — Welch t (53) vs Mann-Whitney (46)
      avn_avring_marker_genes.{csv,json}       — 53 markers ranked
      volcano_afibro_avn_avring_vs_atria.png   — volcano with expected markers labeled
      tissueagent_coding_report.md             — methodology + counts
      de_config.json
    geneagent_input.json                       — broker-validated handoff payload
    geneagent_run/
      response.json
      geneagent_report.md                      — gpt-5 gene-set interpretation
      geneagent_report.json
      geneagent_raw.txt                        — raw LLM JSON (audit)
    integration/
      final_hypothesis_report.md               — gpt-5 integrator output
      final_hypothesis_report.json
      final_summary.md
  traces/
    execution_trace.jsonl                      — per-stage event log
```

## Reproduce

```bash
# 1. Place the Dryad download.
mkdir -p data/heart_merfish
# (Manual download of overall_merfish.h5ad ~370 MB into data/heart_merfish/.)

# 2. AgentCo-Op handles everything else internally — including pip installs.
export OPENAI_API_KEY="..."
agentcoop collaborate \
  --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish \
  --no-docker \
  --model gpt-5 \
  --reasoning-effort medium
```

The `--no-docker` mode runs each adapter in the host Python env (the
EnvManager auto-installs any declared package). Once the Docker
daemon is up, swap to `--docker` and the same request runs in
isolated containers via the rendered Dockerfiles + compose.

## Generalisation

The CLI runs **any** two-agent external collaboration. Write a
`request.yaml` matching `case_study_1.md` §3.3, register a local
adapter per agent in `agentcoop/wrappers/<agent>/__init__.py`, and
invoke `agentcoop collaborate --request <yaml>`. Nothing in the
framework code mentions TissueAgent or GeneAgent — see
`docs/external_agent_collaboration.md` for the design + mechanism
walkthrough.
