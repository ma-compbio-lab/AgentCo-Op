"""Compile-time verification.

Everything here runs before a single component is invoked, is fully
deterministic, and names the specific node, edge, or artifact at fault so
blame is attributable. Catching an identifier-namespace mismatch here costs
nothing; catching it after paying for six components and a GPU hour does not.

Two checks carry most of the weight:

``edge_type_compatibility``
    Treats ``UNDERSPECIFIED`` as a **failure**. A producer that never declares
    the namespace its consumer requires has not earned a compatibility verdict.
    Assuming compatibility here is how heterogeneous scientific tools silently
    produce confident nonsense.

``unnecessary_composition``
    Flags a multi-component workflow when one certified component in the
    library covers every subgoal. Without this check a compiler drifts toward
    always adding agents, because adding is never penalised.
"""

from __future__ import annotations

from typing import Optional

from agentcoop.compile.grammar import RuleContext
from agentcoop.ir.artifacts import Compatibility
from agentcoop.ir.capability import CapabilityCard
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.evidence import EvidenceLedger
from agentcoop.ir.workflow import CompiledWorkflow, ExecGraph, ExecNode, atomics


def _result(
    check_id: str,
    status: CheckStatus,
    summary: str,
    *,
    subject: Optional[str] = None,
    subject_kind: str = "workflow",
    blocking: bool = False,
    level: CheckLevel = CheckLevel.HARD,
    **evidence,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        level=level,
        status=status,
        source=SignalSource.DETERMINISTIC,
        subject=subject,
        subject_kind=subject_kind,  # type: ignore[arg-type]
        summary=summary,
        evidence=dict(evidence),
        blocking=blocking,
    )


def analyze(
    workflow: CompiledWorkflow,
    ctx: RuleContext,
    *,
    ledger: Optional[EvidenceLedger] = None,
) -> CheckReport:
    """Run every static check. Order is fixed for reproducible reports."""
    graph = _safe_graph(workflow)
    report = CheckReport()

    report.add(_cycle_freedom(workflow))
    if graph is None:
        return report

    report.extend(_artifact_reachability(workflow, graph, ctx))
    report.extend(_edge_type_compatibility(workflow, graph, ctx))
    report.extend(_join_without_merge(graph))
    report.extend(_environment_conflict(workflow, ctx))
    report.extend(_missing_verifier(workflow, graph, ctx))
    report.extend(_silent_failure_exposure(workflow, graph, ctx))
    report.extend(_shared_state_leak(workflow, ctx))
    report.add(_unnecessary_composition(workflow, ctx))
    report.add(_provenance_completeness(workflow, graph, ctx))
    report.add(_resource_feasibility(workflow, ctx))
    report.extend(_unjustified_decision(ledger or workflow.evidence))
    return report


# ---------------------------------------------------------------------------


def _safe_graph(workflow: CompiledWorkflow) -> Optional[ExecGraph]:
    try:
        graph = workflow.graph()
        graph.topological_order()
        return graph
    except ValueError:
        return None


def _cycle_freedom(workflow: CompiledWorkflow) -> CheckResult:
    if _safe_graph(workflow) is None:
        return _result(
            "cycle_freedom",
            CheckStatus.FAIL,
            "the execution graph contains a cycle; the grammar cannot express one, "
            "so a patch corrupted the workflow",
            blocking=True,
        )
    return _result("cycle_freedom", CheckStatus.PASS, "execution graph is acyclic")


def _artifact_reachability(
    workflow: CompiledWorkflow, graph: ExecGraph, ctx: RuleContext
) -> list[CheckResult]:
    produced: set[str] = set(ctx.dossier.provided_inputs)
    for atom in atomics(workflow.term):
        card = ctx.library.get(atom.component)
        if card is not None:
            produced.update(t.name for t in card.io.produces)

    out: list[CheckResult] = []
    for required in ctx.dossier.required_outputs:
        if required in produced:
            out.append(
                _result(
                    f"artifact_reachability:{required}",
                    CheckStatus.PASS,
                    f"required output '{required}' is produced by the workflow",
                    subject=required,
                    subject_kind="artifact",
                )
            )
        else:
            out.append(
                _result(
                    f"artifact_reachability:{required}",
                    CheckStatus.FAIL,
                    f"required output '{required}' is not produced by any bound component",
                    subject=required,
                    subject_kind="artifact",
                    blocking=True,
                )
            )
    return out


def _edge_type_compatibility(
    workflow: CompiledWorkflow, graph: ExecGraph, ctx: RuleContext
) -> list[CheckResult]:
    out: list[CheckResult] = []
    for edge in sorted(graph.edges, key=lambda e: (e.source, e.target)):
        source = graph.node(edge.source)
        target = graph.node(edge.target)
        if source is None or target is None:
            continue
        if target.role == "merge" or source.role != "component" or target.role != "component":
            continue
        producer = ctx.library.get(source.component or "")
        consumer = ctx.library.get(target.component or "")
        if producer is None or consumer is None:
            continue

        shared = sorted(
            {t.name for t in producer.io.produces} & {t.name for t in consumer.io.consumes}
        )
        if not shared:
            out.append(
                _result(
                    f"edge_type_compatibility:{edge.edge_id}",
                    CheckStatus.WARN,
                    f"edge {edge.edge_id} carries no artifact the consumer declares; "
                    "it imposes ordering without a data dependency",
                    subject=edge.edge_id,
                    subject_kind="edge",
                    level=CheckLevel.ARTIFACT,
                )
            )
            continue

        for type_name in shared:
            produced_type = producer.output_type(type_name)
            consumed_type = consumer.io.consumed_type(type_name)
            if produced_type is None or consumed_type is None:
                continue
            compat = ctx.types.check_compatibility(produced_type, consumed_type)
            check_id = f"edge_type_compatibility:{edge.edge_id}:{type_name}"
            if compat.verdict is Compatibility.COMPATIBLE:
                status, blocking, summary = (
                    CheckStatus.PASS,
                    False,
                    f"'{type_name}' contract matches across {edge.edge_id}",
                )
            elif compat.verdict is Compatibility.NEEDS_ADAPTER:
                status, blocking, summary = (
                    CheckStatus.WARN,
                    False,
                    f"'{type_name}' needs converter '{compat.adapter}': {compat.detail}",
                )
            else:
                status, blocking, summary = (
                    CheckStatus.FAIL,
                    True,
                    f"{compat.verdict.value}: {compat.detail}",
                )
            out.append(
                _result(
                    check_id,
                    status,
                    summary,
                    subject=edge.edge_id,
                    subject_kind="edge",
                    blocking=blocking,
                    level=CheckLevel.ARTIFACT,
                    verdict=compat.verdict.value,
                    missing_facets=compat.missing_facets,
                )
            )
    return out


def _join_without_merge(graph: ExecGraph) -> list[CheckResult]:
    """Catch *implicit* joins: a node fed by several producers with no merge.

    The grammar forbids an explicit Join without a merge algebra, but a
    sequence following a parallel would otherwise sneak the same thing in
    through the back door, leaving the consumer to combine branch outputs by
    unstated convention.
    """
    out: list[CheckResult] = []
    for node in sorted(graph.nodes, key=lambda n: n.node_id):
        if node.role == "merge":
            continue
        data_preds = [
            e.source
            for e in graph.edges
            if e.target == node.node_id and e.edge_kind == "data"
        ]
        if len(data_preds) > 1:
            out.append(
                _result(
                    f"join_without_merge:{node.node_id}",
                    CheckStatus.FAIL,
                    f"'{node.node_id}' consumes from {len(data_preds)} producers "
                    f"({', '.join(sorted(data_preds))}) with no declared merge algebra; "
                    "how their outputs combine is unstated",
                    subject=node.node_id,
                    subject_kind="node",
                    blocking=True,
                )
            )
    if not out:
        out.append(
            _result(
                "join_without_merge",
                CheckStatus.PASS,
                "every multi-producer handoff goes through a declared merge",
            )
        )
    return out


def _environment_conflict(
    workflow: CompiledWorkflow, ctx: RuleContext
) -> list[CheckResult]:
    cards = [
        c
        for c in (ctx.library.get(n) for n in workflow.components)
        if c is not None
    ]
    out: list[CheckResult] = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            conflicts = cards[i].environment.conflicts_with(cards[j].environment)
            if not conflicts:
                continue
            isolated = bool(
                cards[i].environment.container_image
                and cards[j].environment.container_image
                and cards[i].environment.container_image
                != cards[j].environment.container_image
            )
            out.append(
                _result(
                    f"environment_conflict:{cards[i].name}+{cards[j].name}",
                    CheckStatus.WARN if isolated else CheckStatus.FAIL,
                    (
                        f"'{cards[i].name}' and '{cards[j].name}' conflict "
                        f"({'; '.join(conflicts)})"
                    )
                    + (
                        "; separate container images isolate them"
                        if isolated
                        else "; they cannot share one environment"
                    ),
                    subject=cards[i].name,
                    subject_kind="component",
                    blocking=not isolated,
                    conflicts=conflicts,
                )
            )
    if not out:
        out.append(
            _result(
                "environment_conflict",
                CheckStatus.PASS,
                "no pairwise environment conflicts among bound components",
            )
        )
    return out


def _verified_nodes(graph: ExecGraph) -> set[str]:
    """Nodes with a verifier anywhere downstream."""
    verified: set[str] = set()
    verifiers = [n.node_id for n in graph.nodes if n.role == "verifier"]
    for verifier in verifiers:
        frontier = list(graph.predecessors(verifier))
        while frontier:
            current = frontier.pop()
            if current in verified:
                continue
            verified.add(current)
            frontier.extend(graph.predecessors(current))
    return verified


def _missing_verifier(
    workflow: CompiledWorkflow, graph: ExecGraph, ctx: RuleContext
) -> list[CheckResult]:
    verified = _verified_nodes(graph)
    out: list[CheckResult] = []
    for atom in atomics(workflow.term):
        subgoal = ctx.dossier.subgoal(atom.subgoal_id)
        if subgoal is None or not subgoal.requires_verification:
            continue
        if atom.term_id in verified:
            out.append(
                _result(
                    f"missing_verifier:{atom.subgoal_id}",
                    CheckStatus.PASS,
                    f"subgoal '{atom.subgoal_id}' is covered by a downstream verifier",
                    subject=atom.term_id,
                    subject_kind="node",
                    level=CheckLevel.PROCESS,
                )
            )
        else:
            out.append(
                _result(
                    f"missing_verifier:{atom.subgoal_id}",
                    CheckStatus.FAIL,
                    f"subgoal '{atom.subgoal_id}' declares requires_verification but "
                    "no verifier consumes its output",
                    subject=atom.term_id,
                    subject_kind="node",
                    blocking=True,
                    level=CheckLevel.PROCESS,
                )
            )
    return out


def _silent_failure_exposure(
    workflow: CompiledWorkflow, graph: ExecGraph, ctx: RuleContext
) -> list[CheckResult]:
    """A component that fails silently and is never checked is unbounded risk."""
    verified = _verified_nodes(graph)
    out: list[CheckResult] = []
    for atom in atomics(workflow.term):
        card = ctx.library.get(atom.component)
        if card is None or not card.has_silent_failure_mode():
            continue
        silent = [s.name for s in card.failure_profile if s.silent]
        if atom.term_id in verified:
            continue
        out.append(
            _result(
                f"silent_failure_exposure:{atom.term_id}",
                CheckStatus.WARN,
                f"'{atom.component}' has silent failure mode(s) "
                f"({', '.join(sorted(silent))}) and no downstream verifier; a bad "
                "result would look like a good one",
                subject=atom.term_id,
                subject_kind="node",
                level=CheckLevel.PROCESS,
                silent_modes=sorted(silent),
            )
        )
    if not out:
        out.append(
            _result(
                "silent_failure_exposure",
                CheckStatus.PASS,
                "no unverified component carries a known silent failure mode",
                level=CheckLevel.PROCESS,
            )
        )
    return out


def _shared_state_leak(
    workflow: CompiledWorkflow, ctx: RuleContext
) -> list[CheckResult]:
    from agentcoop.ir.workflow import Parallel, iter_terms

    out: list[CheckResult] = []
    for term in iter_terms(workflow.term):
        if not isinstance(term, Parallel):
            continue
        effects: list[tuple[str, set[str]]] = []
        for branch in term.branches:
            collected: set[str] = set()
            for atom in atomics(branch):
                card = ctx.library.get(atom.component)
                if card is not None:
                    collected.update(card.behavior.side_effects)
            effects.append((branch.term_id, collected))
        for i in range(len(effects)):
            for j in range(i + 1, len(effects)):
                clash = effects[i][1] & effects[j][1]
                if clash:
                    out.append(
                        _result(
                            f"shared_state_leak:{term.term_id}",
                            CheckStatus.FAIL,
                            f"concurrent branches '{effects[i][0]}' and "
                            f"'{effects[j][0]}' share side effect(s) "
                            f"{', '.join(sorted(clash))}",
                            subject=term.term_id,
                            subject_kind="node",
                            blocking=True,
                        )
                    )
    if not out:
        out.append(
            _result(
                "shared_state_leak",
                CheckStatus.PASS,
                "no concurrent branches share mutable state",
            )
        )
    return out


def _unnecessary_composition(
    workflow: CompiledWorkflow, ctx: RuleContext
) -> CheckResult:
    from agentcoop.compile.grammar import can_bind

    # Reported at PROCESS level, not HARD: composing unnecessarily is not
    # *incorrect*, it is wasteful and adds failure surface. It therefore feeds
    # the risk and cost coordinates rather than validity, so that "this
    # workflow is wrong" and "this workflow is more than you needed" stay
    # distinguishable in the utility vector.
    if workflow.n_distinct_components <= 1:
        return _result(
            "unnecessary_composition",
            CheckStatus.PASS,
            "workflow uses a single component; no composition to justify",
            level=CheckLevel.PROCESS,
        )

    for name in ctx.library.names():
        if all(
            can_bind(name, subgoal, ctx).allowed for subgoal in ctx.dossier.subgoals
        ):
            return _result(
                "unnecessary_composition",
                CheckStatus.FAIL,
                f"'{name}' is certified for every subgoal, so this "
                f"{workflow.n_distinct_components}-component workflow adds handoff "
                "surface, cost, and failure modes without serving a subgoal "
                f"'{name}' cannot",
                subject=name,
                subject_kind="component",
                blocking=False,
                level=CheckLevel.PROCESS,
                sufficient_component=name,
            )
    return _result(
        "unnecessary_composition",
        CheckStatus.PASS,
        "no single component covers every subgoal; composition is warranted",
        level=CheckLevel.PROCESS,
    )


def _provenance_completeness(
    workflow: CompiledWorkflow, graph: ExecGraph, ctx: RuleContext
) -> CheckResult:
    """Can each required output be traced back to a declared input?"""
    entries = set(graph.entries)
    unreachable: list[str] = []
    for atom in atomics(workflow.term):
        card = ctx.library.get(atom.component)
        if card is None:
            continue
        if not any(t.name in ctx.dossier.required_outputs for t in card.io.produces):
            continue
        seen, frontier = set(), [atom.term_id]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(graph.predecessors(current))
        if not (seen & entries):
            unreachable.append(atom.term_id)

    if unreachable:
        return _result(
            "provenance_completeness",
            CheckStatus.FAIL,
            "output-producing node(s) with no path from a workflow entry: "
            + ", ".join(sorted(unreachable)),
            blocking=True,
            level=CheckLevel.ARTIFACT,
        )
    return _result(
        "provenance_completeness",
        CheckStatus.PASS,
        "every output-producing node is reachable from a workflow entry",
        level=CheckLevel.ARTIFACT,
    )


def _resource_feasibility(
    workflow: CompiledWorkflow, ctx: RuleContext
) -> CheckResult:
    limits = ctx.dossier.limits
    total_usd = 0.0
    total_time = 0.0
    for atom in atomics(workflow.term):
        card = ctx.library.get(atom.component)
        if card is None:
            continue
        cost = card.expected_cost()
        total_usd += cost.usd
        total_time += cost.latency_s

    problems: list[str] = []
    if limits.max_usd is not None and total_usd > limits.max_usd:
        problems.append(f"estimated ${total_usd:.2f} exceeds the ${limits.max_usd:.2f} budget")
    if limits.max_wall_time_s is not None and total_time > limits.max_wall_time_s:
        problems.append(
            f"estimated {total_time:.0f}s exceeds the {limits.max_wall_time_s:.0f}s limit"
        )

    if problems:
        return _result(
            "resource_feasibility",
            CheckStatus.FAIL,
            "; ".join(problems),
            blocking=True,
            level=CheckLevel.RESOURCE,
            estimated_usd=total_usd,
            estimated_s=total_time,
        )
    if limits.max_usd is None and limits.max_wall_time_s is None:
        return _result(
            "resource_feasibility",
            CheckStatus.UNAVAILABLE,
            "no resource limits are declared, so feasibility cannot be verified",
            level=CheckLevel.RESOURCE,
        )
    return _result(
        "resource_feasibility",
        CheckStatus.PASS,
        f"estimated ${total_usd:.2f} / {total_time:.0f}s fits the declared limits",
        level=CheckLevel.RESOURCE,
        estimated_usd=total_usd,
        estimated_s=total_time,
    )


def _unjustified_decision(ledger: EvidenceLedger) -> list[CheckResult]:
    inadmissible = ledger.inadmissible()
    if not inadmissible:
        return [
            _result(
                "unjustified_decision",
                CheckStatus.PASS,
                f"all {len(ledger.records)} design decision(s) cite load-bearing evidence",
            )
        ]
    return [
        _result(
            f"unjustified_decision:{record.decision_id}",
            CheckStatus.FAIL,
            record.rejection_reason() or "decision lacks required evidence",
            subject=record.target,
            subject_kind="node",
            blocking=True,
        )
        for record in sorted(inadmissible, key=lambda r: r.decision_id)
    ]


__all__ = ["analyze"]
