"""Grammar rules, candidate enumeration, static analysis, and the compiler.

These tests protect the properties that answer the objection that workflow
structure was imagined rather than derived.
"""

from __future__ import annotations

import pytest

from agentcoop.compile.candidates import enumerate_candidates
from agentcoop.compile.compiler import Compiler
from agentcoop.compile.grammar import (
    RuleContext,
    can_bind,
    can_fallback,
    can_join,
    can_parallelize,
    can_verify,
)
from agentcoop.compile.justify import justify
from agentcoop.compile.requirements import plan_requirements
from agentcoop.compile.static_analysis import analyze
from agentcoop.ir.artifacts import ArtifactType, TypeRegistry
from agentcoop.ir.capability import (
    BehaviorContract,
    CapabilityCard,
    ComponentKind,
    ComponentLibrary,
    EmpiricalRecord,
    EnvironmentContract,
    FailureSignature,
    IOContract,
    ProbeOutcome,
)
from agentcoop.ir.checks import CheckStatus
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    HumanReviewCondition,
    Subgoal,
    TaskEvidenceDossier,
)
from agentcoop.ir.evidence import DecisionKind
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, Sequence

FULL_PROBES = ("reachable", "schema", "smoke", "invalid_input", "resource")


def T(name: str, **facets: str) -> ArtifactType:
    return ArtifactType(
        name=name,
        json_schema={"type": "object"},
        required_facets=["namespace"],
        facets=dict(facets),
    )


def probed(*kinds: str) -> EmpiricalRecord:
    return EmpiricalRecord(
        probes=[ProbeOutcome(probe_id=f"P-{k}", kind=k, passed=True) for k in kinds]
    )


def card(
    name: str,
    caps: list[str],
    consumes: list[str],
    produces: list[str],
    *,
    ns: str = "HGNC",
    probes: tuple[str, ...] = FULL_PROBES,
    modality: str | None = None,
    side_effects: list[str] | None = None,
    env: EnvironmentContract | None = None,
    silent: bool = False,
) -> CapabilityCard:
    facets = {"namespace": ns}
    if modality:
        facets["modality"] = modality
    return CapabilityCard(
        name=name,
        kind=ComponentKind.EXTERNAL_REPO,
        functional_capabilities=list(caps),
        io=IOContract(
            consumes=[T(c, **facets) for c in consumes],
            produces=[T(p, **facets) for p in produces],
        ),
        environment=env or EnvironmentContract(),
        behavior=BehaviorContract(side_effects=list(side_effects or [])),
        empirical=probed(*probes),
        failure_profile=(
            [FailureSignature(name="empty_on_bad_input", silent=True)] if silent else []
        ),
    )


def two_step_dossier(**kw) -> TaskEvidenceDossier:
    base = dict(
        task_id="t1",
        goal="find markers then interpret them",
        artifact_types=[T("matrix"), T("gene_set"), T("report")],
        provided_inputs=["matrix"],
        required_outputs=["report"],
        subgoals=[
            Subgoal(
                subgoal_id="s1",
                description="differential expression",
                required_capability="differential_expression",
                consumes=["matrix"],
                produces=["gene_set"],
            ),
            Subgoal(
                subgoal_id="s2",
                description="interpret",
                required_capability="gene_set_interpretation",
                consumes=["gene_set"],
                produces=["report"],
            ),
        ],
    )
    base.update(kw)
    return TaskEvidenceDossier(**base)


def ctx_for(dossier: TaskEvidenceDossier, library: ComponentLibrary, **kw) -> RuleContext:
    return RuleContext(dossier=dossier, library=library, types=TypeRegistry(), **kw)


# ---------------------------------------------------------------------------


class TestBindRule:
    def test_binds_a_certified_matching_component(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        assert can_bind("de", d.subgoals[0], ctx_for(d, lib)).allowed

    def test_refuses_a_component_lacking_the_capability(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["something_else"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        verdict = can_bind("de", d.subgoals[0], ctx_for(d, lib))
        assert not verdict.allowed and "does not claim capability" in verdict.reason

    def test_refuses_an_uncertified_component(self) -> None:
        """A README is not evidence, so an unprobed component cannot be bound
        to a subgoal that demands certification."""
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"], probes=()))
        d = two_step_dossier()
        verdict = can_bind("de", d.subgoals[0], ctx_for(d, lib))
        assert not verdict.allowed
        assert "certified at DECLARED" in verdict.reason and "probe it" in verdict.reason

    def test_refuses_when_the_required_output_facet_is_wrong(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"], ns="ENSEMBL"))
        d = two_step_dossier()
        d.subgoals[0].required_output_facets = {"gene_set": {"namespace": "HGNC"}}
        verdict = can_bind("de", d.subgoals[0], ctx_for(d, lib))
        assert not verdict.allowed and "ENSEMBL" in verdict.reason

    def test_generalist_is_bindable_despite_declaring_extra_inputs(self) -> None:
        """A component certified for several capabilities declares the union of
        their inputs. Refusing it for that would make generalists unbindable
        and bias the compiler toward composition."""
        lib = ComponentLibrary()
        lib.add(
            card(
                "omni",
                ["differential_expression", "gene_set_interpretation"],
                ["matrix", "gene_set"],
                ["gene_set", "report"],
            )
        )
        d = two_step_dossier()
        assert can_bind("omni", d.subgoals[0], ctx_for(d, lib)).allowed

    def test_capability_evidence_is_observed_only_when_probed(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        verdict = can_bind("de", d.subgoals[0], ctx_for(d, lib))
        capability = [e for e in verdict.evidence if e.kind.value == "capability"][0]
        assert capability.load_bearing
        assert capability.source_ref.startswith("probe:de:P-")


class TestParallelRule:
    def _branches(self, lib: ComponentLibrary) -> list[Atomic]:
        return [
            Atomic(component="rna", subgoal_id="s1").ensure_ids(),
            Atomic(component="atac", subgoal_id="s2").ensure_ids(),
        ]

    def test_refuses_redundant_branches(self) -> None:
        """Two components doing the same thing on the same modality is cost
        without evidence. Task difficulty is not a reason to fan out."""
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"], modality="rna"))
        lib.add(card("atac", ["x"], ["matrix"], ["gene_set"], modality="rna"))
        verdict = can_parallelize(self._branches(lib), ctx_for(two_step_dossier(), lib))
        assert not verdict.allowed
        assert "redundant" in verdict.reason

    def test_allows_distinct_modalities(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"], modality="rna"))
        lib.add(card("atac", ["x"], ["matrix"], ["gene_set"], modality="atac"))
        verdict = can_parallelize(self._branches(lib), ctx_for(two_step_dossier(), lib))
        assert verdict.allowed
        assert verdict.evidence[0].kind.value == "coordination"

    def test_allows_complementary_artifact_types(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"]))
        lib.add(card("atac", ["x"], ["matrix"], ["peaks"]))
        assert can_parallelize(self._branches(lib), ctx_for(two_step_dossier(), lib)).allowed

    def test_refuses_when_one_branch_feeds_another(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"]))
        lib.add(card("atac", ["x"], ["gene_set"], ["report"]))
        verdict = can_parallelize(self._branches(lib), ctx_for(two_step_dossier(), lib))
        assert not verdict.allowed and "must be sequenced" in verdict.reason

    def test_refuses_on_shared_side_effects(self) -> None:
        lib = ComponentLibrary()
        lib.add(
            card("rna", ["x"], ["matrix"], ["gene_set"], modality="rna", side_effects=["cache"])
        )
        lib.add(
            card("atac", ["x"], ["matrix"], ["peaks"], modality="atac", side_effects=["cache"])
        )
        verdict = can_parallelize(self._branches(lib), ctx_for(two_step_dossier(), lib))
        assert not verdict.allowed and "stale-state" in verdict.reason

    def test_single_branch_is_not_a_parallel(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"]))
        verdict = can_parallelize(
            [Atomic(component="rna", subgoal_id="s1").ensure_ids()],
            ctx_for(two_step_dossier(), lib),
        )
        assert not verdict.allowed


class TestJoinRule:
    def _setup(self, left_ns: str = "HGNC", right_ns: str = "HGNC"):
        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"], ns=left_ns, modality="rna"))
        lib.add(card("atac", ["x"], ["matrix"], ["gene_set"], ns=right_ns, modality="atac"))
        branches = [
            Atomic(component="rna", subgoal_id="s1").ensure_ids(),
            Atomic(component="atac", subgoal_id="s2").ensure_ids(),
        ]
        return branches, ctx_for(two_step_dossier(), lib)

    def test_allows_a_registered_merge(self) -> None:
        branches, ctx = self._setup()
        verdict = can_join(branches, "intersect", ctx)
        assert verdict.allowed
        assert "semantics" in verdict.evidence[0].data

    def test_refuses_an_unregistered_merge(self) -> None:
        branches, ctx = self._setup()
        verdict = can_join(branches, "vibes", ctx)
        assert not verdict.allowed and "no merge algebra named" in verdict.reason

    def test_refuses_a_join_with_no_named_merge(self) -> None:
        """Combining branch outputs without saying what the combination means
        is not expressible in this grammar."""
        branches, ctx = self._setup()
        verdict = can_join(branches, "", ctx)
        assert not verdict.allowed and "must name a merge algebra" in verdict.reason

    def test_refuses_across_a_namespace_mismatch(self) -> None:
        branches, ctx = self._setup(right_ns="ENSEMBL")
        verdict = can_join(branches, "intersect", ctx)
        assert not verdict.allowed and "disagree" in verdict.reason

    def test_majority_vote_needs_three_branches(self) -> None:
        branches, ctx = self._setup()
        verdict = can_join(branches, "majority_vote", ctx)
        assert not verdict.allowed and "at least 3" in verdict.reason


class TestVerifyAndGates:
    def test_refuses_a_verifier_whose_level_is_unavailable(self) -> None:
        """Attaching a verifier that can never return a verdict manufactures
        the appearance of checking."""
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        d.subgoals[0].requires_verification = True
        d.evaluators = {}
        body = Atomic(component="de", subgoal_id="s1").ensure_ids()
        verdict = can_verify(body, "claim_supported", ctx_for(d, lib))
        assert not verdict.allowed and "unavailable" in verdict.reason

    def test_refuses_a_verifier_no_subgoal_asked_for(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        from agentcoop.ir.checks import CheckLevel

        d.evaluators = {CheckLevel.HARD: EvaluatorAvailability.DETERMINISTIC}
        body = Atomic(component="de", subgoal_id="s1").ensure_ids()
        verdict = can_verify(body, "hard_outputs", ctx_for(d, lib))
        assert not verdict.allowed and "requires_verification" in verdict.reason

    def test_human_gate_requires_a_declared_condition(self) -> None:
        from agentcoop.compile.grammar import can_human_gate

        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        body = Atomic(component="de", subgoal_id="s1").ensure_ids()
        assert not can_human_gate(body, "high_risk", ctx_for(d, lib)).allowed

        d.human_review = [
            HumanReviewCondition(
                condition_id="high_risk", description="clinical", trigger="risk"
            )
        ]
        assert can_human_gate(body, "high_risk", ctx_for(d, lib)).allowed

    def test_fallback_needs_recorded_failure_evidence(self) -> None:
        """Adding a fallback arm 'just in case' is unjustified cost."""
        lib = ComponentLibrary()
        lib.add(card("a", ["x"], ["matrix"], ["gene_set"]))
        lib.add(card("b", ["x"], ["matrix"], ["gene_set"]))
        primary = Atomic(component="a", subgoal_id="s1").ensure_ids()
        alternate = Atomic(component="b", subgoal_id="s1").ensure_ids()
        verdict = can_fallback(primary, alternate, ctx_for(two_step_dossier(), lib))
        assert not verdict.allowed and "no run statistics" in verdict.reason


class TestRequirementPlan:
    def test_orders_subgoals_by_artifact_flow(self) -> None:
        plan = plan_requirements(two_step_dossier())
        assert plan.order == ["s1", "s2"]
        assert plan.layers == [["s1"], ["s2"]]
        assert plan.ok

    def test_independent_subgoals_share_a_layer(self) -> None:
        d = two_step_dossier(
            artifact_types=[T("matrix"), T("gene_set"), T("peaks"), T("report")],
            required_outputs=["gene_set", "peaks"],
            subgoals=[
                Subgoal(
                    subgoal_id="rna",
                    description="rna",
                    required_capability="x",
                    consumes=["matrix"],
                    produces=["gene_set"],
                ),
                Subgoal(
                    subgoal_id="atac",
                    description="atac",
                    required_capability="x",
                    consumes=["matrix"],
                    produces=["peaks"],
                ),
            ],
        )
        plan = plan_requirements(d)
        assert plan.independent_groups() == [["atac", "rna"]]


class TestEnumeration:
    def test_single_component_candidate_is_always_enumerated(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        lib.add(
            card(
                "omni",
                ["differential_expression", "gene_set_interpretation"],
                ["matrix", "gene_set"],
                ["gene_set", "report"],
            )
        )
        d = two_step_dossier()
        result = enumerate_candidates(d, ctx_for(d, lib))
        singles = [c for c in result.candidates if c.single_component]
        assert len(singles) == 1
        assert singles[0].candidate_id == "single::omni"

    def test_decline_structure_is_recorded_with_an_alternative(self) -> None:
        lib = ComponentLibrary()
        lib.add(
            card(
                "omni",
                ["differential_expression", "gene_set_interpretation"],
                ["matrix", "gene_set"],
                ["gene_set", "report"],
            )
        )
        d = two_step_dossier()
        result = enumerate_candidates(d, ctx_for(d, lib))
        single = result.candidates[0]
        record = single.ledger.records["decline_structure"]
        assert record.decision_kind is DecisionKind.DECLINE_STRUCTURE
        assert record.admissible
        assert record.alternatives[0].rejection_reason

    def test_unbindable_subgoal_yields_no_candidates_and_says_why(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier()
        result = enumerate_candidates(d, ctx_for(d, lib))
        assert result.candidates == []
        assert "s2" in result.unbindable
        assert any("gene_set_interpretation" in r for r in result.unbindable["s2"])


class TestJustification:
    def test_unprobed_binding_makes_a_candidate_inadmissible(self) -> None:
        """The end-to-end enforcement: no probe, no observed capability
        evidence, no admissible bind, no candidate."""
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"], probes=()))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"], probes=()))
        d = two_step_dossier()
        for s in d.subgoals:
            s.min_certification = "DECLARED"
        result = enumerate_candidates(d, ctx_for(d, lib))
        assert result.candidates
        judgement = justify(result.candidates[0], ctx_for(d, lib))
        assert not judgement.admissible
        assert any("capability" in r for r in judgement.rejections)


class TestStaticAnalysis:
    def _workflow(self, lib: ComponentLibrary, d: TaskEvidenceDossier) -> CompiledWorkflow:
        result = enumerate_candidates(d, ctx_for(d, lib))
        candidate = result.candidates[0]
        return CompiledWorkflow(
            workflow_id="w",
            task_id=d.task_id,
            term=candidate.term,
            evidence=candidate.ledger,
        )

    def test_underspecified_edge_is_a_blocking_failure(self) -> None:
        lib = ComponentLibrary()
        producer = card("de", ["differential_expression"], ["matrix"], ["gene_set"])
        producer.io.produces = [ArtifactType(name="gene_set", json_schema={"type": "object"})]
        lib.add(producer)
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        d = two_step_dossier()
        report = analyze(self._workflow(lib, d), ctx_for(d, lib))
        edge_failures = [
            c
            for c in report.failures(blocking_only=True)
            if c.check_id.startswith("edge_type_compatibility")
        ]
        assert edge_failures
        assert "underspecified" in edge_failures[0].summary

    def test_missing_required_output_is_caught(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["summary"]))
        d = two_step_dossier()
        d.subgoals[1].produces = ["summary"]
        d.artifact_types.append(T("summary"))
        d.required_outputs = ["report"]
        # 'report' is now produced by nobody.
        result = enumerate_candidates(d, ctx_for(d, lib))
        if not result.candidates:
            pytest.skip("no candidate to analyse")
        wf = CompiledWorkflow(
            workflow_id="w",
            task_id="t",
            term=result.candidates[0].term,
            evidence=result.candidates[0].ledger,
        )
        report = analyze(wf, ctx_for(d, lib))
        assert any(
            c.check_id.startswith("artifact_reachability") for c in report.failures()
        )

    def test_implicit_join_without_a_merge_is_caught(self) -> None:
        """A sequence after a parallel would otherwise sneak in an undeclared
        combination through the back door."""
        from agentcoop.ir.workflow import Parallel

        lib = ComponentLibrary()
        lib.add(card("rna", ["x"], ["matrix"], ["gene_set"], modality="rna"))
        lib.add(card("atac", ["x"], ["matrix"], ["peaks"], modality="atac"))
        lib.add(card("gi", ["y"], ["gene_set", "peaks"], ["report"]))
        d = two_step_dossier()
        term = Sequence(
            children_terms=[
                Parallel(
                    branches=[
                        Atomic(component="rna", subgoal_id="s1"),
                        Atomic(component="atac", subgoal_id="s2"),
                    ]
                ),
                Atomic(component="gi", subgoal_id="s3"),
            ]
        ).ensure_ids()
        wf = CompiledWorkflow(workflow_id="w", task_id="t", term=term)
        report = analyze(wf, ctx_for(d, lib))
        failures = [c for c in report.failures() if c.check_id.startswith("join_without_merge")]
        assert failures and "no declared merge algebra" in failures[0].summary

    def test_environment_conflict_is_blocking_unless_containerised(self) -> None:
        lib = ComponentLibrary()
        lib.add(
            card(
                "de",
                ["differential_expression"],
                ["matrix"],
                ["gene_set"],
                env=EnvironmentContract(python="3.9"),
            )
        )
        lib.add(
            card(
                "gi",
                ["gene_set_interpretation"],
                ["gene_set"],
                ["report"],
                env=EnvironmentContract(python="3.11"),
            )
        )
        d = two_step_dossier()
        report = analyze(self._workflow(lib, d), ctx_for(d, lib))
        conflicts = [c for c in report.results if c.check_id.startswith("environment_conflict:")]
        assert conflicts and conflicts[0].status is CheckStatus.FAIL

        lib2 = ComponentLibrary()
        lib2.add(
            card(
                "de",
                ["differential_expression"],
                ["matrix"],
                ["gene_set"],
                env=EnvironmentContract(python="3.9", container_image="de:1"),
            )
        )
        lib2.add(
            card(
                "gi",
                ["gene_set_interpretation"],
                ["gene_set"],
                ["report"],
                env=EnvironmentContract(python="3.11", container_image="gi:1"),
            )
        )
        report2 = analyze(self._workflow(lib2, d), ctx_for(d, lib2))
        conflicts2 = [c for c in report2.results if c.check_id.startswith("environment_conflict:")]
        assert conflicts2 and conflicts2[0].status is CheckStatus.WARN

    def test_silent_failure_without_a_verifier_is_flagged(self) -> None:
        lib = ComponentLibrary()
        lib.add(
            card("de", ["differential_expression"], ["matrix"], ["gene_set"], silent=True)
        )
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        d = two_step_dossier()
        report = analyze(self._workflow(lib, d), ctx_for(d, lib))
        warnings = [
            c for c in report.results if c.check_id.startswith("silent_failure_exposure:")
        ]
        assert warnings and warnings[0].status is CheckStatus.WARN

    def test_no_declared_limits_makes_feasibility_unavailable_not_passing(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        d = two_step_dossier()
        report = analyze(self._workflow(lib, d), ctx_for(d, lib))
        feasibility = [c for c in report.results if c.check_id == "resource_feasibility"][0]
        assert feasibility.status is CheckStatus.UNAVAILABLE


class TestCompiler:
    def test_compiles_a_two_specialist_workflow(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        result = Compiler().compile(two_step_dossier(), lib)
        assert result.ok
        assert result.workflow.components == ["de", "gi"]
        assert result.workflow.evidence.fully_justified

    def test_prefers_the_single_component_when_one_suffices(self) -> None:
        """The measurable form of 'you do not need a multi-agent workflow'."""
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        lib.add(
            card(
                "omni",
                ["differential_expression", "gene_set_interpretation"],
                ["matrix", "gene_set"],
                ["gene_set", "report"],
            )
        )
        result = Compiler().compile(two_step_dossier(), lib)
        assert result.ok
        assert result.workflow.components == ["omni"]
        assert "decline_structure" in result.workflow.evidence.records

    def test_over_composition_shows_up_as_risk_not_invalidity(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        lib.add(
            card(
                "omni",
                ["differential_expression", "gene_set_interpretation"],
                ["matrix", "gene_set"],
                ["gene_set", "report"],
            )
        )
        result = Compiler().compile(two_step_dossier(), lib)
        from agentcoop.ir.utility import Objective

        single = result.estimates["single::omni"].vector
        composed = next(
            e.vector for cid, e in result.estimates.items() if cid.startswith("composed")
        )
        assert single.get(Objective.VALIDITY) == composed.get(Objective.VALIDITY) == 1.0
        assert single.get(Objective.RISK) < composed.get(Objective.RISK)

    def test_refuses_to_compile_a_defective_dossier(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        d = two_step_dossier(required_outputs=["report", "figure"])
        d.artifact_types.append(T("figure"))
        result = Compiler().compile(d, lib)
        assert not result.ok
        assert any("figure" in defect for defect in result.dossier_defects)
        assert result.candidates == []

    def test_pre_execution_utility_admits_what_it_cannot_know(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        result = Compiler().compile(two_step_dossier(), lib)
        from agentcoop.ir.utility import Objective

        estimate = next(iter(result.estimates.values()))
        assert Objective.ROBUSTNESS in estimate.vector.unavailable
        assert Objective.SCIENTIFIC_UTILITY in estimate.vector.unavailable
        assert "nothing has been run" in estimate.basis["robustness"]

    def test_explain_names_the_chosen_candidate_and_its_evidence(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        text = Compiler().compile(two_step_dossier(), lib).explain()
        assert "SELECTED" in text and "DESIGN EVIDENCE" in text
        assert "bind::s1" in text

    def test_compilation_is_deterministic(self) -> None:
        lib = ComponentLibrary()
        lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"]))
        lib.add(card("gi", ["gene_set_interpretation"], ["gene_set"], ["report"]))
        first = Compiler().compile(two_step_dossier(), lib)
        second = Compiler().compile(two_step_dossier(), lib)
        assert first.workflow.structural_key() == second.workflow.structural_key()
        assert first.selection.front == second.selection.front
