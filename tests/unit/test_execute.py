"""Execution: edge validation, lineage, merges, guards, and budget."""

from __future__ import annotations

import pytest

from agentcoop.components.base import AdapterRegistry, make_artifact
from agentcoop.components.python_fn import PythonFunctionAdapter
from agentcoop.execute.engine import ExecutionEngine, _levels
from agentcoop.execute.state import BudgetLedger, TerminationReason
from agentcoop.execute.trace import EventKind, Trace
from agentcoop.ir.artifacts import Artifact, ArtifactType, TypeRegistry
from agentcoop.ir.capability import (
    CapabilityCard,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    IOContract,
)
from agentcoop.ir.checks import CheckStatus
from agentcoop.ir.dossier import ResourceLimits, TaskEvidenceDossier
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, Fallback, Join, Sequence
from agentcoop.merges import default_merge_registry


def gene_type(name: str = "gene_set", **facets: str) -> ArtifactType:
    return ArtifactType(
        name=name,
        json_schema={
            "type": "object",
            "required": ["genes"],
            "properties": {"genes": {"type": "array"}},
        },
        required_facets=["namespace"],
        facets=dict(facets),
    )


def card(name: str, consumes: list[ArtifactType], produces: list[ArtifactType]) -> CapabilityCard:
    return CapabilityCard(
        name=name,
        kind=ComponentKind.PYTHON_FUNCTION,
        io=IOContract(consumes=consumes, produces=produces),
    )


def emitter(name: str, type_name: str, payload, **facets: str) -> PythonFunctionAdapter:
    return PythonFunctionAdapter(
        name,
        lambda inv: {
            type_name: make_artifact(type_name=type_name, payload=payload, facets=dict(facets))
        },
    )


def dossier(*types: ArtifactType, required: list[str] | None = None) -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="t",
        goal="g",
        artifact_types=list(types),
        required_outputs=required or [],
    )


async def run_pipeline(producer_ns: str | None, consumer_ns: str):
    lib = ComponentLibrary()
    prod_facets = {"namespace": producer_ns} if producer_ns else {}
    lib.add(card("de", [], [gene_type(**prod_facets)]))
    lib.add(
        card(
            "interp",
            [gene_type(namespace=consumer_ns)],
            [gene_type("report", namespace=consumer_ns)],
        )
    )
    adapters = AdapterRegistry()
    adapters.register(emitter("de", "gene_set", {"genes": ["A", "B"]}, **prod_facets))
    adapters.register(emitter("interp", "report", {"genes": ["ok"]}, namespace=consumer_ns))
    wf = CompiledWorkflow(
        workflow_id="w",
        task_id="t",
        term=Sequence(
            children_terms=[
                Atomic(component="de", subgoal_id="s1"),
                Atomic(component="interp", subgoal_id="s2"),
            ]
        ).ensure_ids(),
    )
    engine = ExecutionEngine(adapters, type_registry=TypeRegistry(), library=lib)
    return await engine.run(
        wf, dossier(gene_type(), gene_type("report"), required=["report"]), {}, run_id="r"
    )


class TestEdgeValidation:
    async def test_matching_facets_pass_and_produce_output(self) -> None:
        result = await run_pipeline("HGNC", "HGNC")
        assert result.ok
        assert "report" in result.outputs
        handoffs = [c for c in result.report.results if c.check_id.startswith("handoff")]
        assert all(c.status is CheckStatus.PASS for c in handoffs)

    async def test_namespace_conflict_fails_before_the_consumer_runs(self) -> None:
        """The consumer must never see an artifact it cannot interpret."""
        result = await run_pipeline("ENSEMBL", "HGNC")
        assert not result.ok
        assert "s2__interp" in result.state.failed
        assert "s2__interp" not in result.state.executed
        failure = [c for c in result.report.failures()][0]
        assert "ENSEMBL" in failure.summary and "HGNC" in failure.summary
        assert failure.subject_kind == "edge"

    async def test_undeclared_facet_fails_as_underspecified(self) -> None:
        """Silence about the namespace is not agreement about the namespace."""
        result = await run_pipeline(None, "HGNC")
        assert not result.ok
        failure = result.report.failures()[0]
        assert "underspecified" in failure.summary
        assert "namespace" in failure.summary

    async def test_rejected_handoff_is_recorded_as_an_event(self) -> None:
        result = await run_pipeline("ENSEMBL", "HGNC")
        assert result.trace.events_of(EventKind.HANDOFF_REJECTED)

    async def test_malformed_payload_fails_schema_validation(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", [], [gene_type(namespace="HGNC")]))
        lib.add(card("interp", [gene_type(namespace="HGNC")], [gene_type("report", namespace="HGNC")]))
        adapters = AdapterRegistry()
        # Emits a payload missing the required 'genes' key.
        adapters.register(emitter("de", "gene_set", {"wrong": []}, namespace="HGNC"))
        adapters.register(emitter("interp", "report", {"genes": []}, namespace="HGNC"))
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Sequence(
                children_terms=[
                    Atomic(component="de", subgoal_id="s1"),
                    Atomic(component="interp", subgoal_id="s2"),
                ]
            ).ensure_ids(),
        )
        engine = ExecutionEngine(adapters, type_registry=TypeRegistry(), library=lib)
        result = await engine.run(wf, dossier(gene_type(), gene_type("report")), {}, run_id="r")
        assert not result.ok
        assert any("schema" in c.summary for c in result.report.failures())


class TestLineage:
    async def test_output_traces_back_to_its_input(self) -> None:
        """Diagnosis is only as good as this."""
        result = await run_pipeline("HGNC", "HGNC")
        report = result.outputs["report"]
        chain = result.trace.lineage(report.artifact_id)
        assert len(chain) == 2
        assert chain[0].startswith("report::")
        assert chain[1].startswith("gene_set::")

    async def test_descendants_size_the_blast_radius(self) -> None:
        result = await run_pipeline("HGNC", "HGNC")
        gene_set = [
            a for a in result.trace.artifacts.values() if a.type_name == "gene_set"
        ][0]
        assert len(result.trace.descendants_of(gene_set.artifact_id)) == 1

    async def test_producer_is_the_node_not_the_component(self) -> None:
        """Two nodes running the same component must stay distinguishable."""
        result = await run_pipeline("HGNC", "HGNC")
        report = result.outputs["report"]
        assert result.trace.producer_of(report.artifact_id) == "s2__interp"


class TestMerge:
    def _two_branch_workflow(self, merge: str) -> CompiledWorkflow:
        return CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Join(
                branches=[
                    Atomic(component="rna", subgoal_id="s1"),
                    Atomic(component="atac", subgoal_id="s2"),
                ],
                merge=merge,
            ).ensure_ids(),
        )

    async def _run_merge(self, merge: str, left_ns: str, right_ns: str):
        lib = ComponentLibrary()
        lib.add(card("rna", [], [gene_type(namespace=left_ns, modality="rna")]))
        lib.add(card("atac", [], [gene_type(namespace=right_ns, modality="atac")]))
        adapters = AdapterRegistry()
        adapters.register(
            emitter("rna", "gene_set", {"genes": ["A", "B", "C"]}, namespace=left_ns, modality="rna")
        )
        adapters.register(
            emitter("atac", "gene_set", {"genes": ["B", "C", "D"]}, namespace=right_ns, modality="atac")
        )
        engine = ExecutionEngine(
            adapters,
            type_registry=TypeRegistry(),
            library=lib,
            merges=default_merge_registry(),
        )
        return await engine.run(
            self._two_branch_workflow(merge), dossier(gene_type()), {}, run_id="r"
        )

    async def test_intersect_across_modalities_is_allowed(self) -> None:
        """Modality is the axis being combined over, so it is facet-exempt."""
        result = await self._run_merge("intersect", "HGNC", "HGNC")
        assert result.ok
        merged = [a for a in result.trace.artifacts.values() if a.producer.endswith("__merge")][0]
        assert merged.payload["genes"] == ["B", "C"]

    async def test_merge_refuses_across_a_namespace_mismatch(self) -> None:
        """Intersecting HGNC with Ensembl yields an empty set that reads as a
        scientific finding. Refusing is the only safe behaviour."""
        result = await self._run_merge("intersect", "HGNC", "ENSEMBL")
        assert not result.ok
        assert result.trace.events_of(EventKind.MERGE_REFUSED)

    async def test_unregistered_merge_is_refused(self) -> None:
        result = await self._run_merge("vibes", "HGNC", "HGNC")
        assert not result.ok
        refusal = result.trace.events_of(EventKind.MERGE_REFUSED)[0]
        assert "no merge algebra named 'vibes'" in refusal.detail

    async def test_majority_vote_refuses_with_only_two_branches(self) -> None:
        """A two-way vote is a tie, not a decision procedure."""
        result = await self._run_merge("majority_vote", "HGNC", "HGNC")
        assert not result.ok
        assert "requires 3" in result.trace.events_of(EventKind.MERGE_REFUSED)[0].detail

    async def test_merged_artifact_records_both_parents(self) -> None:
        result = await self._run_merge("union", "HGNC", "HGNC")
        merged = [a for a in result.trace.artifacts.values() if a.producer.endswith("__merge")][0]
        assert len(merged.derived_from) == 2


class TestGuards:
    async def test_fallback_alternate_is_skipped_when_primary_succeeds(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("primary", [], [gene_type(namespace="HGNC")]))
        lib.add(card("alternate", [], [gene_type(namespace="HGNC")]))
        adapters = AdapterRegistry()
        adapters.register(emitter("primary", "gene_set", {"genes": ["A"]}, namespace="HGNC"))
        adapters.register(emitter("alternate", "gene_set", {"genes": ["Z"]}, namespace="HGNC"))
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Fallback(
                primary=Atomic(component="primary", subgoal_id="s1"),
                alternate=Atomic(component="alternate", subgoal_id="s1"),
            ).ensure_ids(),
        )
        engine = ExecutionEngine(adapters, type_registry=TypeRegistry(), library=lib)
        result = await engine.run(wf, dossier(gene_type()), {}, run_id="r")
        assert result.ok
        assert any("alternate" in n for n in result.state.skipped)
        assert result.trace.events_of(EventKind.NODE_SKIPPED)


class TestBudget:
    def test_ledger_names_which_limit_was_hit(self) -> None:
        ledger = BudgetLedger(limits=ResourceLimits(max_usd=1.0, max_tokens=1000))
        ledger.charge(CostProfile(usd=0.5, tokens=100))
        assert ledger.exceeded() is None
        ledger.charge(CostProfile(usd=0.6))
        assert ledger.exceeded() is TerminationReason.BUDGET_USD

    def test_shadow_budget_uses_remaining_not_original(self) -> None:
        """A run that already spent most of its money cannot fund an expensive
        speculative validation."""
        ledger = BudgetLedger(
            limits=ResourceLimits(max_usd=10.0, shadow_budget_fraction=0.25)
        )
        ledger.charge(CostProfile(usd=8.0))
        shadow = ledger.scaled_for_shadow()
        assert shadow.limits.max_usd == pytest.approx(0.5)

    def test_shadow_ledger_cannot_itself_spawn_shadows(self) -> None:
        ledger = BudgetLedger(limits=ResourceLimits(max_usd=10.0))
        assert ledger.scaled_for_shadow().limits.shadow_budget_fraction == 0.0

    def test_headroom_reports_the_tightest_constraint(self) -> None:
        ledger = BudgetLedger(limits=ResourceLimits(max_usd=10.0, max_tokens=100))
        ledger.charge(CostProfile(usd=1.0, tokens=90))
        assert ledger.headroom_fraction() == pytest.approx(0.1)

    def test_no_declared_limits_means_no_headroom_claim(self) -> None:
        assert BudgetLedger().headroom_fraction() is None


class TestMissingAdapter:
    async def test_unregistered_component_is_an_environment_fault(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("ghost", [], [gene_type(namespace="HGNC")]))
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Atomic(component="ghost", subgoal_id="s1").ensure_ids(),
        )
        engine = ExecutionEngine(AdapterRegistry(), type_registry=TypeRegistry(), library=lib)
        result = await engine.run(wf, dossier(gene_type()), {}, run_id="r")
        assert not result.ok
        assert "no adapter registered" in " ".join(
            result.trace.node_results["s1__ghost"].errors
        )


class TestDeterminism:
    async def test_two_runs_produce_identical_traces(self) -> None:
        first = await run_pipeline("HGNC", "HGNC")
        second = await run_pipeline("HGNC", "HGNC")
        assert [(e.step, e.kind, e.node_id) for e in first.trace.events] == [
            (e.step, e.kind, e.node_id) for e in second.trace.events
        ]
        assert sorted(first.trace.artifacts) == sorted(second.trace.artifacts)

    def test_levels_group_independent_nodes(self) -> None:
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=Join(
                branches=[
                    Atomic(component="a", subgoal_id="s1"),
                    Atomic(component="b", subgoal_id="s2"),
                ],
                merge="union",
            ).ensure_ids(),
        )
        levels = _levels(wf.graph())
        assert len(levels[0]) == 2, "independent branches share a level"
        assert len(levels[1]) == 1


class TestTrace:
    def test_snapshot_is_independent(self) -> None:
        trace = Trace(run_id="r")
        trace.record(EventKind.RUN_START)
        snap = trace.snapshot()
        trace.record(EventKind.RUN_END)
        assert len(snap.events) == 1 and len(trace.events) == 2

    def test_steps_are_monotonic_and_carry_no_wall_clock(self) -> None:
        trace = Trace(run_id="r")
        events = [trace.record(EventKind.NODE_START, node_id=f"n{i}") for i in range(3)]
        assert [e.step for e in events] == [1, 2, 3]
        # No wall-clock field exists at all, so ordering cannot accidentally
        # depend on one and two traces of the same run diff cleanly.
        from agentcoop.execute.trace import TraceEvent

        assert not [f for f in TraceEvent.model_fields if "time" in f or "clock" in f]

    def test_artifacts_of_is_sorted_and_scoped_to_the_node(self) -> None:
        trace = Trace(run_id="r")
        trace.record_artifact(
            Artifact(artifact_id="z", type_name="t", producer="n1", payload=1)
        )
        trace.record_artifact(
            Artifact(artifact_id="a", type_name="t", producer="n1", payload=2)
        )
        trace.record_artifact(
            Artifact(artifact_id="b", type_name="t", producer="n2", payload=3)
        )
        assert [a.payload for a in trace.artifacts_of("n1")] == [2, 1]
