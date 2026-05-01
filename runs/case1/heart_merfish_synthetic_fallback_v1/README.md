# Case Study 1 v1 — TissueAgent × GeneAgent (SYNTHETIC-FALLBACK snapshot, superseded)

> **Superseded.** This was the first end-to-end exercise of the
> external-repo collaboration framework using AgentCo-Op's
> deterministic synthetic-MERFISH fixture (the real Dryad h5ad was
> not on disk yet). The current run uses the real Farah MERFISH
> dataset and lives at `runs/case1/heart_merfish/` — see its README
> for the real-data numbers (Welch t = 53 markers, Mann-Whitney U =
> 46 markers exactly matching the manuscript, 6/6 expected example
> markers, status `success`).

Run produced by `agentcoop collaborate --request case_study_1.request.yaml
--workdir runs/case1/heart_merfish --no-docker --model gpt-5
--reasoning-effort medium`.

This case study tests AgentCo-Op's **generalised external-repo
collaboration framework** added in Session 7. The framework:

1. clones the two declared GitHub repositories (`TissueAgent`,
   `GeneAgent`) and emits typed `RepoProfile`s;
2. renders Dockerfiles + `docker-compose.yml` + per-image smoke tests
   via the new `SandboxBuilder` (dry-run when the host has no Docker
   daemon — exactly this host);
3. registers an `AgentCard` per repository;
4. runs the upstream agent (TissueAgent — spatial-transcriptomics
   differential expression) and the downstream agent (GeneAgent —
   gene-set interpretation) in declaration order;
5. brokers the gene-list handoff with `ArtifactBroker`
   (`gene_set` validator);
6. integrates both agents' outputs with an LLM-backed integrator
   (`gpt-5`, `reasoning_effort=medium`);
7. writes a `run_manifest.json` + `compiled_workflow_graph.json` +
   per-step artifacts.

The same CLI runs **any** two-agent external collaboration — see the
[generalisation](#generalisation) section below.

## Run summary

| Metric | Value |
|---|---|
| Status | `synthetic_fallback` (Farah `overall_merfish.h5ad` not on disk) |
| Wall time | ~2 minutes (the LLM nodes dominate) |
| Marker count (AVN/AV ring vs Atria) | **51** (target 46) |
| Example marker overlap | **6 / 6** (DES, IGFBP5, NELL2, HAND2, MYH7, MYH6) |
| GeneAgent process label | `AV canal/AV node–biased developmental program in AV ring atrial fibroblasts` |
| GeneAgent subprocesses | 5 (AV-canal/conduction TFs · Cushion ECM/EMT · Myofibroblast contractile · AV-junction adhesion · Paracrine remodeling) |
| Final integrator model | `gpt-5` (`reasoning_effort=medium`) |
| Total LLM tokens (GeneAgent + integrator) | 5 048 |

The run **passes** the case-study targets in `case_study_1.md`
§13: marker count ≥ 40, ≥ 5/6 expected example markers recovered, the
expected GeneAgent theme is found, all five expected subprocess
categories are covered, and the final synthesis is evidence-linked.

## Why "synthetic_fallback"?

The driver looks for `data/farah_human_heart_merfish/overall_merfish.h5ad`
(per the request YAML). When the file is absent OR `anndata` /
`scanpy` are not installed locally, the TissueAgent local adapter
generates a deterministic synthetic MERFISH-shaped AnnData fixture
(seed=42; 3 000 cells × 240 genes; same `populations` /
`communities` / `sample_id` columns). This lets the entire AgentCo-Op
collaboration framework be exercised on a host that does not yet have
the 370 MB Dryad download or the upstream conda envs installed. The
synthetic fixture is engineered so a Welch t-test recovers the expected
6/6 example markers — the methodology check passes; the *biological*
result on real Farah data should be re-confirmed once the dataset is
downloaded.

To run with the real dataset:

```bash
mkdir -p data/farah_human_heart_merfish
# Manual: download overall_merfish.h5ad from
# https://datadryad.org/dataset/doi:10.5061/dryad.w0vt4b8vp into the
# directory above (~370 MB).
pip install anndata scanpy   # if not already installed
agentcoop collaborate --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish --no-docker \
  --model gpt-5 --reasoning-effort medium
```

When Docker is up, drop `--no-docker` and the orchestrator runs each
wrapper inside its container — the Dockerfiles and `docker-compose.yml`
are already written under `docker/` (next section).

## Generated artifacts

```
runs/case1/heart_merfish/
  run_manifest.json                         — top-level outcome + paths
  compiled_workflow_graph.json              — L7 external_repo_collaboration nodes/edges
  agent_registry.json                       — AgentCards (auto-built from RepoProfile)
  manifests/
    repo_profile_TissueAgent.json
    repo_profile_GeneAgent.json
    docker_build_report.json                — dry-run report (Docker daemon down)
    broker.jsonl                            — typed handoff audit log
  docker/
    Dockerfile.TissueAgent                  — generated from RepoProfile + SandboxSpec
    Dockerfile.GeneAgent                    — generated from RepoProfile + SandboxSpec
    docker-compose.yml                      — both services + volumes
    smoke_TissueAgent.py                    — per-image smoke test
    smoke_GeneAgent.py
  artifacts/
    tissueagent_run/
      response.json
      dataset_schema_report.{json,md}
      group_definition_report.json
      group_counts.csv
      de_results.csv                         — full per-gene Welch t-test table
      de_sensitivity_summary.csv             — Welch vs Mann-Whitney comparison
      avn_avring_marker_genes.{csv,json}     — marker list (rank/log2fc/adj_p)
      volcano_afibro_avn_avring_vs_atria.png — volcano plot, expected markers labeled
      tissueagent_coding_report.md           — methodology + counts
      de_config.json                         — exact preprocessing + test choices
    geneagent_input.json                     — broker-validated handoff payload
    geneagent_run/
      response.json
      geneagent_report.md                    — LLM (gpt-5) gene-set interpretation
      geneagent_report.json
      geneagent_raw.txt                      — raw LLM JSON (audit)
    integration/
      final_hypothesis_report.md             — LLM (gpt-5) integrator output
      final_hypothesis_report.json
      final_summary.md
  traces/
    execution_trace.jsonl                    — per-stage events
```

## Generalisation

The case study is the **inaugural fixture** for the generic external-repo
collaboration framework, not a one-off. Anything in this directory that
would change for a different repo pair lives under
`agentcoop/wrappers/<agent>/` and the user's `request.yaml`. The
framework code (`agentcoop/core/repo_profile.py`,
`agentcoop/core/sandbox_build.py`, `agentcoop/core/agent_card.py`,
`agentcoop/core/artifact_broker.py`,
`agentcoop/core/repo_collaboration.py`,
`agentcoop/skills/meta/external_repo_collaboration.md`,
`agentcoop/cli.py::collaborate`) does not mention TissueAgent or
GeneAgent.

To collaborate two arbitrary GitHub agents on a different downstream
task:

```yaml
# my_request.yaml
case_id: my_collab
mode: autonomous_repo_wrapping
repositories:
  - {name: AgentA, url: https://github.com/<owner>/AgentA, role_hint: ...}
  - {name: AgentB, url: https://github.com/<owner>/AgentB, role_hint: ...}
task:
  description: "..."
dataset:
  local_cache_dir: data/my_dataset
required_outputs: [...]
success_targets: {...}
```

```bash
agentcoop collaborate --request my_request.yaml --workdir runs/my_run \
  --no-docker --model gpt-5 --reasoning-effort medium
```

Register a local-Python adapter for each agent in
`agentcoop/wrappers/<agent>/__init__.py` via
`from agentcoop.core.repo_collaboration import register_local_adapter`,
or use the Docker mode by setting `--docker` once the daemon is up.
