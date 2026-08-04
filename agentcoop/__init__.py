"""AgentCo-Op — an evidence-driven compiler for heterogeneous agent workflows.

The system certifies external components through executable capability
probes, compiles typed workflows whose every node and edge carries explicit
design evidence, diagnoses failures by artifact-level causal tracing, and
improves workflows through transactional repair and Pareto optimization when
no scalar reward exists.

Layout::

    ir/         typed IR: dossier, capability cards, evidence, grammar, utility
    probe/      executable capability certification
    compile/    grammar-constrained synthesis, static analysis, Pareto selection
    execute/    typed execution with artifact lineage
    diagnose/   detect -> localize -> diagnose
    repair/     propose -> shadow validate -> commit/rollback
    components/ the invocation boundary and its concrete adapters
    bench/      task suites, fault injection, and baseline harnesses

The six evaluation levels live in ``ir.checks.CheckLevel`` and are reported
per level by ``bench.harness.contract_summary``; there is deliberately no
separate evaluation package re-deriving what the harness already computes.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
