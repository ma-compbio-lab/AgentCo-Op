# `legacy/` — frozen AgentCo-Op v1

This is the v1 implementation exactly as it stood at commit `94c3750`, the
state that produced the benchmark numbers and case-study runs currently cited
in the paper.

It is kept for **reproducibility only**. Do not extend it. The v2 rewrite
lives in `agentcoop/` and shares no code with it.

* `agentcoop_v1/` — the v1 package (compiler, runtime, gates, benchmarks,
  and the external-repo wrappers for TissueAgent, GeneAgent, Seurat, Signac,
  and the CellMarker evaluator).
* `tests_v1/` — the v1 test suite.

To run anything here, install the v1 package separately; it is deliberately
no longer wired into `pyproject.toml`.

The domain logic in `agentcoop_v1/wrappers/` is still scientifically valid and
is the intended source material when porting those components onto the v2
`CapabilityCard` / `ComponentAdapter` interfaces.
