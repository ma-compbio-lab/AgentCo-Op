# CS1 — Heart MERFISH (Path B: Docker, Python sandbox)

**Session 9, 2026-05-03.** First Docker-mode rerun of CS1 after the
user's local colima setup. The orchestrator now actually drives
`docker run` instead of falling back to in-process Python — verified by
`run_manifest.json:no_docker = false` and the two `agentcoop-runtime:case-study`
containers logged during the run.

| Field | Value |
|---|---|
| Run dir | `runs/case1/heart_merfish_docker_b/` |
| Topology | `linear_handoff` (TissueAgent → broker → GeneAgent → integrator) |
| Docker mode | **on** (`agentcoop-runtime:case-study` Python image) |
| Wall time | 2:46 |
| Status | `success` |
| LLM model | `gpt-5` (reasoning_effort=medium) |
| Integrator tokens | 4 785 |

## Numbers vs the Session-7 baseline (`runs/case1/heart_merfish/`)

| Metric | Baseline (no_docker) | Path B (docker) |
|---|---|---|
| n_target (aFibro AVN/AV ring) | 576 | **576** ✓ |
| n_control (aFibro Left + Right Atria) | 5 685 | **5 685** ✓ |
| Markers (adj_p < 0.05, log2fc > 0) | 53 | **53** ✓ |
| Expected-example overlap | 6 / 6 | **6 / 6** ✓ |
| Expected genes hit | DES, HAND2, IGFBP5, MYH6, MYH7, NELL2 | identical |

Identical numbers — the Python adapter inside the container produces
bit-identical outputs to the in-process run. The downstream GeneAgent
(LLM-driven) differs in narrative phrasing only ("AV Canal/Node-
Associated Fibroblast Program — 6 subprocesses" vs "AV Ring Fibroblast
Developmental Program — 5 subprocesses") because gpt-5 sampling is
non-deterministic.

## What this proves

1. **Docker path actually works**, not just on paper. The
   orchestrator's `_run_adapter_in_docker` builds the runtime image
   once (`agentcoop-runtime:case-study`, 463 MB), bind-mounts the repo
   root + dataset paths into the container at the same absolute path,
   and `docker run --rm`s the same `python -m agentcoop.wrappers
   <agent> <invoke.json>` entrypoint for every adapter.
2. **Adapter bit-identity is preserved.** Same Python code, same
   inputs, same numbers — the container is just an isolation boundary.
3. **No regressions on the rest of the codebase.** All 144 unit tests
   still pass (was 144 before Session 9 too).

## Reproducing
```bash
colima start --runtime docker --cpu 6 --memory 10 --disk 120
set -a; source .secrets/api-key; set +a
docker build -f docker/agentcoop-runtime.Dockerfile -t agentcoop-runtime:case-study .
python -m agentcoop.cli collaborate \
  --request case_study_1.request.yaml \
  --workdir runs/case1/heart_merfish_docker_b \
  --docker
```

## Path C plan (separate run dir)
A real-R-Seurat + R-Signac::GeneActivity rerun lands at
`runs/case1/heart_merfish_docker_c/` only if a downstream R-based
TissueAgent wrapper is added — not in scope for this case study, which
the user mandated CS2 instead. CS1 stays on Python adapters.
