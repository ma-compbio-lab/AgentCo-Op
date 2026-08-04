"""The execution engine.

Three things happen here that later subsystems depend on absolutely:

**Artifacts are validated at the edge, before the consumer runs.** Every
handoff emits an ARTIFACT-level check naming the edge. A producer that never
declared the identifier namespace its consumer requires is stopped here, not
discovered later as an inexplicably empty result. This is the runtime twin of
the compiler's static edge check.

**Lineage is recorded truthfully.** Each emitted artifact carries the ids of
the artifacts consumed to produce it. Backward slicing in
:mod:`agentcoop.diagnose` is only as good as this.

**Merges may refuse.** A ``Join`` whose branches disagree on their semantic
facets does not quietly intersect two incompatible sets and report an empty
result; it refuses, and the refusal is a diagnosable fault.

Determinism: nodes at the same topological depth may run concurrently, but
events are recorded in sorted node-id order, so two runs of the same
deterministic workflow produce identical traces.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.components.base import (
    AdapterRegistry,
    Invocation,
    InvocationResult,
    error_line,
    failure_result,
)
from agentcoop.execute.state import BudgetLedger, RunState, TerminationReason
from agentcoop.execute.trace import EventKind, Trace
from agentcoop.ir.artifacts import (
    Artifact,
    ArtifactType,
    Compatibility,
    TypeRegistry,
    validate_payload,
)
from agentcoop.ir.capability import CapabilityCard, ComponentLibrary, CostProfile
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import ResourceLimits, TaskEvidenceDossier
from agentcoop.ir.faults import FaultClass
from agentcoop.ir.workflow import CompiledWorkflow, ExecGraph, ExecNode
from agentcoop.merges import MergeRefusal, MergeRegistry, default_merge_registry


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    trace: Trace
    state: RunState
    #: Final artifacts, keyed by artifact type name.
    outputs: dict[str, Artifact] = Field(default_factory=dict)
    cost: CostProfile = Field(default_factory=CostProfile)
    report: CheckReport = Field(default_factory=CheckReport)

    @property
    def terminated_reason(self) -> Optional[str]:
        return (
            self.state.termination_reason.value
            if self.state.termination_reason
            else None
        )


class ExecutionEngine:
    """Runs a compiled workflow, recording everything diagnosis will need."""

    def __init__(
        self,
        adapters: AdapterRegistry,
        *,
        type_registry: Optional[TypeRegistry] = None,
        library: Optional[ComponentLibrary] = None,
        merges: Optional[MergeRegistry] = None,
        check_registry: Any = None,
    ) -> None:
        self.adapters = adapters
        self.types = type_registry or TypeRegistry()
        self.library = library or ComponentLibrary()
        self.merges = merges or default_merge_registry()
        self.checks = check_registry

    # -- public -------------------------------------------------------------

    async def run(
        self,
        workflow: CompiledWorkflow,
        dossier: TaskEvidenceDossier,
        inputs: Mapping[str, Artifact],
        *,
        limits: Optional[ResourceLimits] = None,
        seed: int = 0,
        run_id: str = "run",
        workdir: Optional[Any] = None,
    ) -> ExecutionResult:
        graph = workflow.graph()
        trace = Trace(run_id=run_id)
        state = RunState(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            task_id=workflow.task_id,
            budget=BudgetLedger(limits=limits or dossier.limits),
        )
        report = CheckReport()

        trace.record(
            EventKind.RUN_START,
            detail=workflow.workflow_id,
            components=workflow.components,
            n_nodes=len(graph.nodes),
        )

        # Seed artifacts are the run's inputs; they have no producer.
        available: dict[str, Artifact] = {}
        for type_name, artifact in sorted(inputs.items()):
            stored = trace.record_artifact(
                artifact.model_copy(update={"producer": artifact.producer or "<input>"})
            )
            available[type_name] = stored

        # Per-node outputs, so an edge can find exactly what its source emitted.
        emitted: dict[str, dict[str, Artifact]] = {}

        for level in _levels(graph):
            if state.terminated:
                break
            runnable = [
                nid for nid in level if not self._skip(graph, nid, state, trace)
            ]
            results = await asyncio.gather(
                *(
                    self._run_node(
                        graph.node(nid),
                        graph,
                        emitted,
                        available,
                        trace,
                        state,
                        report,
                        dossier,
                        seed=seed,
                        workdir=workdir,
                    )
                    for nid in runnable
                )
            )
            # Record in sorted node order regardless of completion order.
            for nid, result in sorted(zip(runnable, results), key=lambda p: p[0]):
                trace.record_result(nid, result)
                state.budget.charge(result.cost)
                if result.ok:
                    state.executed.append(nid)
                    for type_name, art in sorted(result.outputs.items()):
                        emitted.setdefault(nid, {})[type_name] = art
                        available[type_name] = art
                else:
                    state.failed.append(nid)
                trace.record(
                    EventKind.NODE_END,
                    node_id=nid,
                    detail="ok" if result.ok else "failed",
                    errors=list(result.errors),
                    outputs=sorted(result.outputs),
                )

            reason = state.budget.exceeded()
            if reason is not None:
                state.terminate(reason, f"stopped after level {sorted(level)}")
                trace.record(EventKind.BUDGET, detail=reason.value)
                break

        outputs = {
            name: available[name]
            for name in dossier.required_outputs
            if name in available
        }
        if not state.terminated:
            state.termination_reason = TerminationReason.COMPLETED

        trace.record(
            EventKind.RUN_END,
            detail=(state.termination_reason.value if state.termination_reason else ""),
            executed=sorted(state.executed),
            failed=sorted(state.failed),
        )
        for result in trace.checks:
            report.add(result)

        return ExecutionResult(
            ok=not state.failed and not state.terminated and report.hard_constraints_satisfied,
            trace=trace,
            state=state,
            outputs=outputs,
            cost=state.budget.spent,
            report=report,
        )

    # -- node execution -----------------------------------------------------

    def _skip(self, graph: ExecGraph, node_id: str, state: RunState, trace: Trace) -> bool:
        """Conditional nodes run only when their guard fired.

        The guard for a ``Fallback`` alternate is ``failed:<primary term id>``.
        A primary that succeeded means the alternate is dead code for this run,
        and recording that explicitly keeps it out of the failure statistics.
        """
        node = graph.node(node_id)
        if node is None or not node.conditional or not node.guard:
            return False
        kind, _, target = node.guard.partition(":")
        if kind != "failed":
            return False
        primary_failed = any(
            nid in state.failed
            for nid in state.failed + state.executed
            if (graph.node(nid) or ExecNode(node_id="", role="")).origin_term == target
            or nid.startswith(target)
        )
        if not primary_failed:
            state.skipped.append(node_id)
            trace.record(
                EventKind.NODE_SKIPPED,
                node_id=node_id,
                detail=f"guard '{node.guard}' did not fire",
            )
            return True
        trace.record(EventKind.GUARD, node_id=node_id, detail=node.guard)
        return False

    async def _run_node(
        self,
        node: Optional[ExecNode],
        graph: ExecGraph,
        emitted: dict[str, dict[str, Artifact]],
        available: dict[str, Artifact],
        trace: Trace,
        state: RunState,
        report: CheckReport,
        dossier: TaskEvidenceDossier,
        *,
        seed: int,
        workdir: Optional[Any],
    ) -> InvocationResult:
        if node is None:
            return failure_result(FaultClass.COORDINATION, "node not present in graph")

        trace.record(EventKind.NODE_START, node_id=node.node_id, detail=node.role)

        if node.role == "merge":
            return self._run_merge(node, graph, emitted, trace)

        card = self.library.get(node.component or "")
        node_inputs, handoff_ok = self._gather_inputs(
            node, graph, emitted, available, card, trace, dossier
        )
        if not handoff_ok:
            return failure_result(
                FaultClass.ARTIFACT_CONTRACT,
                f"input contract violated at an edge into '{node.node_id}'",
            )

        if not self.adapters.has(node.component or ""):
            return failure_result(
                FaultClass.ENVIRONMENT,
                f"no adapter registered for component '{node.component}'",
            )

        inv = Invocation(
            component=node.component or "",
            subgoal_id=node.subgoal_id,
            inputs=node_inputs,
            config={**node.config, "node_id": node.node_id},
            workdir=workdir,
            limits=state.budget.limits,
            seed=seed,
        )
        try:
            result = await self.adapters.get(node.component or "").invoke(inv)
        except Exception as exc:  # adapter contract violation, not a task failure
            return failure_result(
                FaultClass.TOOL_FAILURE,
                f"adapter raised {type(exc).__name__}: {exc}",
            )

        stored: dict[str, Artifact] = {}
        for type_name, art in sorted(result.outputs.items()):
            stored[type_name] = trace.record_artifact(art, node_id=node.node_id)
        return result.model_copy(update={"outputs": stored})

    def _run_merge(
        self,
        node: ExecNode,
        graph: ExecGraph,
        emitted: dict[str, dict[str, Artifact]],
        trace: Trace,
    ) -> InvocationResult:
        """Apply a registered merge, or refuse and say why."""
        merge_name = str(node.config.get("merge", ""))
        branch_artifacts: list[Artifact] = []
        for pred in sorted(graph.predecessors(node.node_id)):
            for _, art in sorted(emitted.get(pred, {}).items()):
                branch_artifacts.append(art)

        ok, reason = self.merges.check_applicable(merge_name, branch_artifacts)
        if not ok:
            trace.record(
                EventKind.MERGE_REFUSED, node_id=node.node_id, detail=reason
            )
            # A facet clash between branches is a contract fault; anything else
            # about the merge itself is a coordination fault. The distinction
            # decides which repairs are admissible.
            fault = (
                FaultClass.ARTIFACT_CONTRACT
                if "facet" in reason
                else FaultClass.COORDINATION
            )
            return failure_result(fault, reason)

        try:
            merged_payload = self.merges.apply(merge_name, branch_artifacts, node.config)
        except MergeRefusal as exc:
            trace.record(EventKind.MERGE_REFUSED, node_id=node.node_id, detail=str(exc))
            return failure_result(FaultClass.COORDINATION, str(exc))

        template = branch_artifacts[0]
        merged = Artifact(
            artifact_id="",
            type_name=template.type_name,
            facets=dict(template.facets),
            payload=merged_payload,
            producer=node.node_id,
            derived_from=[a.artifact_id for a in branch_artifacts],
        )
        stored = trace.record_artifact(merged, node_id=node.node_id)
        trace.record(
            EventKind.MERGE,
            node_id=node.node_id,
            detail=merge_name,
            n_branches=len(branch_artifacts),
        )
        return InvocationResult(ok=True, outputs={stored.type_name: stored})

    # -- handoff validation -------------------------------------------------

    def _gather_inputs(
        self,
        node: ExecNode,
        graph: ExecGraph,
        emitted: dict[str, dict[str, Artifact]],
        available: dict[str, Artifact],
        card: Optional[CapabilityCard],
        trace: Trace,
        dossier: TaskEvidenceDossier,
    ) -> tuple[dict[str, Artifact], bool]:
        """Collect this node's inputs and validate each handoff.

        A rejected handoff fails the node *before* the component runs. Letting
        a component consume an artifact whose namespace it cannot interpret is
        how a workflow produces confident nonsense.
        """
        # The subgoal contract says what must flow into this role; the card
        # says what the component is able to accept. A component certified for
        # several capabilities declares the union of their inputs, so treating
        # every declared input as mandatory here would fail a generalist that
        # is behaving correctly.
        subgoal = dossier.subgoal(node.subgoal_id or "")
        acceptable = {t.name for t in card.io.consumes} if card is not None else set()
        required = [a for a in (subgoal.consumes if subgoal else []) if a in acceptable or not acceptable]
        wanted = required or sorted(acceptable)
        inputs: dict[str, Artifact] = {}
        all_ok = True

        preds = sorted(graph.predecessors(node.node_id))
        upstream: dict[str, Artifact] = {}
        for pred in preds:
            for type_name, art in sorted(emitted.get(pred, {}).items()):
                upstream[type_name] = art

        for type_name in wanted or sorted(upstream):
            art = upstream.get(type_name) or available.get(type_name)
            if art is None:
                # Only a type the *subgoal* declares is genuinely missing; an
                # unsupplied optional input the card merely mentions is not.
                if type_name in required:
                    trace.record_check(
                        CheckResult(
                            check_id=f"handoff:{node.node_id}:{type_name}",
                            level=CheckLevel.ARTIFACT,
                            status=CheckStatus.FAIL,
                            subject=node.node_id,
                            subject_kind="node",
                            summary=(
                                f"'{node.node_id}' requires artifact type "
                                f"'{type_name}' but nothing upstream produced it"
                            ),
                            blocking=True,
                        )
                    )
                    all_ok = False
                continue

            result = self._check_handoff(node, art, card, type_name, dossier)
            trace.record_check(result)
            if result.status is CheckStatus.FAIL:
                all_ok = False
                trace.record(
                    EventKind.HANDOFF_REJECTED,
                    node_id=node.node_id,
                    artifact_id=art.artifact_id,
                    detail=result.summary,
                )
            else:
                trace.record(
                    EventKind.HANDOFF,
                    node_id=node.node_id,
                    artifact_id=art.artifact_id,
                    detail=type_name,
                )
                inputs[type_name] = art

        return inputs, all_ok

    def _check_handoff(
        self,
        node: ExecNode,
        artifact: Artifact,
        card: Optional[CapabilityCard],
        type_name: str,
        dossier: TaskEvidenceDossier,
    ) -> CheckResult:
        edge_id = f"{artifact.producer}->{node.node_id}"
        check_id = f"handoff:{edge_id}:{type_name}"

        declared = dossier.artifact_type(type_name) or self.types.get(type_name)
        consumer_type = card.io.consumed_type(type_name) if card is not None else None

        problems: list[str] = []
        if declared is not None:
            problems.extend(validate_payload(artifact.payload, declared))

        producer_type = ArtifactType(
            name=type_name,
            json_schema=(declared.json_schema if declared else {}),
            required_facets=(consumer_type.required_facets if consumer_type else []),
            facets=dict(artifact.facets),
        )
        verdict = Compatibility.COMPATIBLE
        detail = ""
        if consumer_type is not None:
            compat = self.types.check_compatibility(producer_type, consumer_type)
            verdict, detail = compat.verdict, compat.detail

        if problems:
            return CheckResult(
                check_id=check_id,
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.FAIL,
                source=SignalSource.DETERMINISTIC,
                subject=edge_id,
                subject_kind="edge",
                summary=f"payload violates '{type_name}' schema: " + "; ".join(problems),
                evidence={"problems": problems, "artifact": artifact.artifact_id},
                blocking=True,
            )

        if verdict in (Compatibility.INCOMPATIBLE, Compatibility.UNDERSPECIFIED):
            return CheckResult(
                check_id=check_id,
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.FAIL,
                source=SignalSource.DETERMINISTIC,
                subject=edge_id,
                subject_kind="edge",
                summary=f"{verdict.value}: {detail}",
                evidence={
                    "verdict": verdict.value,
                    "artifact": artifact.artifact_id,
                    "facets": dict(artifact.facets),
                },
                blocking=True,
            )

        if verdict is Compatibility.NEEDS_ADAPTER:
            return CheckResult(
                check_id=check_id,
                level=CheckLevel.ARTIFACT,
                status=CheckStatus.WARN,
                source=SignalSource.DETERMINISTIC,
                subject=edge_id,
                subject_kind="edge",
                summary=f"conversion required: {detail}",
                evidence={"artifact": artifact.artifact_id},
            )

        return CheckResult(
            check_id=check_id,
            level=CheckLevel.ARTIFACT,
            status=CheckStatus.PASS,
            source=SignalSource.DETERMINISTIC,
            subject=edge_id,
            subject_kind="edge",
            summary=f"handoff of '{type_name}' satisfies the consumer contract",
            evidence={"artifact": artifact.artifact_id},
        )


def _levels(graph: ExecGraph) -> list[list[str]]:
    """Topological depth groups. Nodes within a group have no dependency."""
    order = graph.topological_order()
    depth: dict[str, int] = {}
    for node_id in order:
        preds = graph.predecessors(node_id)
        depth[node_id] = (max((depth[p] for p in preds), default=-1) + 1) if preds else 0
    groups: dict[int, list[str]] = {}
    for node_id, d in depth.items():
        groups.setdefault(d, []).append(node_id)
    return [sorted(groups[d]) for d in sorted(groups)]


__all__ = ["ExecutionEngine", "ExecutionResult"]
