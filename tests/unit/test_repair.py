"""Repair: admissibility, purity, shadow validation, and tier escalation."""

from __future__ import annotations

import pytest

from agentcoop.compile.grammar import RuleContext
from agentcoop.diagnose.diagnose import DiagnosisOutcome
from agentcoop.diagnose.localize import ArtifactVerdict, Localization
from agentcoop.execute.engine import ExecutionResult
from agentcoop.execute.state import RunState
from agentcoop.execute.trace import Trace
from agentcoop.ir.artifacts import ArtifactType, Converter, TypeRegistry
from agentcoop.ir.capability import (
    BehaviorContract,
    CapabilityCard,
    ComponentKind,
    ComponentLibrary,
    EmpiricalRecord,
    IOContract,
    ProbeOutcome,
)
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckResult, CheckStatus
from agentcoop.ir.dossier import Subgoal, TaskEvidenceDossier
from agentcoop.ir.faults import (
    FAULT_TAXONOMY,
    BlameTarget,
    Diagnosis,
    FaultClass,
    FaultHypothesis,
    RepairTier,
)
from agentcoop.ir.utility import Objective, UtilityVector
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, RepairPolicy, Sequence, atomics
from agentcoop.repair.loop import RepairLoop
from agentcoop.repair.patches import PATCH_FAMILIES, Patch, PatchError, apply
from agentcoop.repair.policy import decide_tier
from agentcoop.repair.propose import propose
from agentcoop.repair.shadow import ShadowValidator
from agentcoop.repair.transaction import RepairLog, RepairTransaction, TransactionOutcome

FULL = ("reachable", "schema", "smoke", "invalid_input", "resource")


def T(name: str, **facets: str) -> ArtifactType:
    return ArtifactType(
        name=name, json_schema={"type": "object"}, required_facets=["namespace"], facets=dict(facets)
    )


def card(
    name: str,
    caps: list[str],
    consumes: list[str],
    produces: list[str],
    *,
    ns: str = "HGNC",
    shadow_safe: bool = True,
) -> CapabilityCard:
    return CapabilityCard(
        name=name,
        kind=ComponentKind.EXTERNAL_REPO,
        functional_capabilities=list(caps),
        io=IOContract(
            consumes=[T(c, namespace=ns) for c in consumes],
            produces=[T(p, namespace=ns) for p in produces],
        ),
        behavior=BehaviorContract(shadow_safe=shadow_safe),
        empirical=EmpiricalRecord(
            probes=[ProbeOutcome(probe_id=f"P-{k}", kind=k, passed=True) for k in FULL]
        ),
    )


def dossier() -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="t",
        goal="g",
        artifact_types=[T("matrix"), T("gene_set"), T("report")],
        provided_inputs=["matrix"],
        required_outputs=["report"],
        subgoals=[
            Subgoal(
                subgoal_id="s1",
                description="de",
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


def library(**kw) -> ComponentLibrary:
    lib = ComponentLibrary()
    lib.add(card("de", ["differential_expression"], ["matrix"], ["gene_set"], **kw))
    lib.add(card("de_alt", ["differential_expression"], ["matrix"], ["gene_set"], **kw))
    lib.add(card("interp", ["gene_set_interpretation"], ["gene_set"], ["report"], **kw))
    return lib


def workflow() -> CompiledWorkflow:
    return CompiledWorkflow(
        workflow_id="w",
        task_id="t",
        term=Sequence(
            children_terms=[
                Atomic(component="de", subgoal_id="s1"),
                Atomic(component="interp", subgoal_id="s2"),
            ]
        ).ensure_ids(),
    )


def ctx(lib: ComponentLibrary | None = None, types: TypeRegistry | None = None) -> RuleContext:
    return RuleContext(dossier=dossier(), library=lib or library(), types=types or TypeRegistry())


def hypothesis(fault: FaultClass, subject: str = "s1__de", p: float = 0.9) -> FaultHypothesis:
    return FaultHypothesis(
        fault_class=fault,
        probability=p,
        blame_target=BlameTarget.ARTIFACT if fault is FaultClass.ARTIFACT_CONTRACT else BlameTarget.NODE,
        subject=subject,
    )


def outcome_for(fault: FaultClass, subject: str = "s1__de::gene_set::abc") -> DiagnosisOutcome:
    blame = (
        BlameTarget.ARTIFACT if fault is FaultClass.ARTIFACT_CONTRACT else BlameTarget.NODE
    )
    return DiagnosisOutcome(
        diagnosis=Diagnosis(
            hypotheses=[
                FaultHypothesis(
                    fault_class=fault, probability=1.0, blame_target=blame, subject=subject
                )
            ],
            localized_to=subject,
            localized_kind=blame,
        ),
        localization=Localization(subject=subject, subject_kind=blame),
    )


# ---------------------------------------------------------------------------


class TestAdmissibility:
    def test_contract_fault_never_gets_a_prompt_rewrite(self) -> None:
        """The headline constraint: a namespace mismatch is not fixed by
        re-rolling a prompt or retrying the node."""
        types = TypeRegistry()
        types.register_converter(
            Converter(
                name="ensembl_to_hgnc",
                type_name="gene_set",
                from_facets={"namespace": "HGNC"},
                to_facets={"namespace": "HGNC"},
            )
        )
        proposal = propose(
            outcome_for(FaultClass.ARTIFACT_CONTRACT), workflow(), ctx(types=types)
        )
        families = {p.family for p in proposal.patches}
        assert families
        assert not families & {"retry_node", "rewrite_prompt", "retune_config", "add_specialist"}
        assert families <= set(FAULT_TAXONOMY[FaultClass.ARTIFACT_CONTRACT].admissible_patches)

    def test_configuration_fault_never_gets_structural_surgery(self) -> None:
        proposal = propose(outcome_for(FaultClass.CONFIGURATION, "s1__de"), workflow(), ctx())
        families = {p.family for p in proposal.patches}
        assert families
        assert not families & {"insert_adapter", "parallelize", "define_merge", "pin_environment"}

    def test_evaluator_fault_never_patches_the_generator(self) -> None:
        """If the judge is broken, changing what it judges optimizes against a
        broken oracle."""
        d = dossier()
        d.evaluators = {}
        rule_ctx = RuleContext(dossier=d, library=library(), types=TypeRegistry())
        proposal = propose(
            outcome_for(FaultClass.EVALUATOR_FAILURE, "s1__de"), workflow(), rule_ctx
        )
        families = {p.family for p in proposal.patches}
        assert not families & {"retry_node", "replace_component", "rewrite_prompt"}

    def test_every_proposed_family_is_admissible_for_its_hypothesis(self) -> None:
        for fault in FaultClass:
            proposal = propose(outcome_for(fault, "s1__de"), workflow(), ctx())
            for patch in proposal.patches:
                assert patch.hypothesis.admits(patch.family), (
                    f"{patch.family} is not admissible for {fault.value}"
                )

    def test_a_diffuse_diagnosis_produces_no_patches(self) -> None:
        outcome = outcome_for(FaultClass.CONFIGURATION)
        outcome.needs_more_evidence = True
        proposal = propose(outcome, workflow(), ctx())
        assert proposal.patches == []
        assert "gathering evidence" in proposal.skipped["*"]

    def test_insert_adapter_is_skipped_when_no_converter_exists(self) -> None:
        """A repair cannot invent a converter that does not exist."""
        proposal = propose(outcome_for(FaultClass.ARTIFACT_CONTRACT), workflow(), ctx())
        assert "insert_adapter" in proposal.skipped
        assert "cannot invent one" in proposal.skipped["insert_adapter"]


class TestBlameMapping:
    def test_patch_targets_the_producer_not_the_failing_consumer(self) -> None:
        """The payoff of localization: when blame lands on an artifact, the
        term patched is the one that produced it."""
        outcome = outcome_for(FaultClass.ARTIFACT_CONTRACT, "s1__de::gene_set::abc123")
        proposal = propose(outcome, workflow(), ctx())
        assert proposal.patches or proposal.skipped
        targets = {p.target for p in proposal.patches}
        assert targets == {"s1__de"} or not targets


class TestPatchApplication:
    def _patch(self, family: str, target: str = "s1__de", **params) -> Patch:
        spec = PATCH_FAMILIES[family]
        return Patch(
            patch_id=f"test:{family}",
            family=family,
            tier=spec.tier,
            target=target,
            description=spec.description,
            rationale="test",
            hypothesis=hypothesis(FaultClass.TOOL_FAILURE),
            params=params,
        )

    def test_application_is_pure(self) -> None:
        original = workflow()
        before = original.structural_key()
        patched = apply(self._patch("replace_component", component="de_alt"), original)
        assert original.structural_key() == before
        assert patched.structural_key() != before
        assert {a.component for a in atomics(patched.term)} == {"de_alt", "interp"}

    def test_insert_adapter_puts_the_converter_upstream(self) -> None:
        patched = apply(
            self._patch("insert_adapter", target="s2__interp", converter="ensembl_to_hgnc"),
            workflow(),
        )
        components = [a.component for a in atomics(patched.term)]
        assert components.index("ensembl_to_hgnc") < components.index("interp")

    def test_missing_required_parameter_is_refused(self) -> None:
        with pytest.raises(PatchError, match="requires parameter"):
            apply(self._patch("replace_component"), workflow())

    def test_unknown_target_is_refused(self) -> None:
        with pytest.raises(PatchError, match="not in the workflow"):
            apply(self._patch("replace_component", target="ghost", component="de_alt"), workflow())

    def test_terminal_families_produce_no_workflow(self) -> None:
        with pytest.raises(PatchError, match="terminal"):
            apply(self._patch("report_uncertainty"), workflow())

    def test_config_patch_leaves_structure_untouched(self) -> None:
        original = workflow()
        patched = apply(self._patch("retry_node", attempts=2), original)
        assert patched.structural_key() == original.structural_key()
        target = next(a for a in atomics(patched.term) if a.component == "de")
        assert target.config["max_retries"] == 2

    def test_serialize_branches_requires_a_parallel(self) -> None:
        with pytest.raises(PatchError, match="targets a parallel"):
            apply(self._patch("serialize_branches"), workflow())


class TestShadowValidation:
    def _result(self, checks: list[CheckResult]) -> ExecutionResult:
        return ExecutionResult(
            ok=not any(c.status is CheckStatus.FAIL for c in checks),
            trace=Trace(run_id="r"),
            state=RunState(run_id="r", workflow_id="w", task_id="t"),
            report=CheckReport(results=checks),
        )

    def _check(self, cid: str, status: CheckStatus) -> CheckResult:
        return CheckResult(check_id=cid, level=CheckLevel.HARD, status=status, summary=cid)

    async def test_accepts_a_patch_that_fixes_without_breaking(self) -> None:
        baseline = self._result(
            [self._check("a", CheckStatus.FAIL), self._check("b", CheckStatus.PASS)]
        )
        trial = self._result(
            [self._check("a", CheckStatus.PASS), self._check("b", CheckStatus.PASS)]
        )
        validator = ShadowValidator(library())
        result = await validator.validate(
            self._patch(), workflow(), workflow(), baseline, lambda w: _async(trial)
        )
        assert result.ok and result.fixed_original_failure
        assert result.collateral_regressions == []

    async def test_rejects_a_patch_that_fixes_but_regresses(self) -> None:
        """The specific failure v1 could not see: symptom gone, something else
        newly broken."""
        baseline = self._result(
            [self._check("a", CheckStatus.FAIL), self._check("b", CheckStatus.PASS)]
        )
        trial = self._result(
            [self._check("a", CheckStatus.PASS), self._check("b", CheckStatus.FAIL)]
        )
        validator = ShadowValidator(library())
        result = await validator.validate(
            self._patch(), workflow(), workflow(), baseline, lambda w: _async(trial)
        )
        assert not result.ok
        assert result.collateral_regressions == ["b"]
        assert "collateral regression" in result.summary

    async def test_rejects_a_patch_that_does_not_fix_the_failure(self) -> None:
        baseline = self._result([self._check("a", CheckStatus.FAIL)])
        trial = self._result([self._check("a", CheckStatus.FAIL)])
        result = await ShadowValidator(library()).validate(
            self._patch(), workflow(), workflow(), baseline, lambda w: _async(trial)
        )
        assert not result.ok and not result.fixed_original_failure

    async def test_rejects_a_patch_dominated_on_utility(self) -> None:
        """Fixed the check, made the result worse overall."""
        baseline = self._result([self._check("a", CheckStatus.FAIL)])
        trial = self._result([self._check("a", CheckStatus.PASS)])

        def utility(result: ExecutionResult) -> UtilityVector:
            failed = any(c.status is CheckStatus.FAIL for c in result.report.results)
            return (
                UtilityVector.of(validity=0.9, evidence=0.9, cost=0.1)
                if failed
                else UtilityVector.of(validity=0.2, evidence=0.2, cost=0.9)
            )

        validator = ShadowValidator(library(), utility_fn=utility)
        result = await validator.validate(
            self._patch(), workflow(), workflow(), baseline, lambda w: _async(trial)
        )
        assert not result.ok and result.dominated_by_original
        assert any("Pareto-dominated" in n for n in result.notes)

    async def test_refuses_to_shadow_run_unsafe_components(self) -> None:
        """Re-running something with irreversible side effects to test a guess
        is not a validation strategy."""
        baseline = self._result([self._check("a", CheckStatus.FAIL)])
        validator = ShadowValidator(library(shadow_safe=False))
        result = await validator.validate(
            self._patch(), workflow(), workflow(), baseline, lambda w: _async(baseline)
        )
        assert not result.ok
        assert any("not shadow-safe" in n for n in result.notes)

    async def test_a_raising_harness_is_survivable(self) -> None:
        baseline = self._result([self._check("a", CheckStatus.FAIL)])

        async def boom(_):
            raise RuntimeError("sandbox died")

        result = await ShadowValidator(library()).validate(
            self._patch(), workflow(), workflow(), baseline, boom
        )
        assert not result.ok and "sandbox died" in result.notes[0]

    def _patch(self) -> Patch:
        return Patch(
            patch_id="p",
            family="retry_node",
            tier=RepairTier.CONTRACT_REPAIR,
            target="s1__de",
            description="retry",
            rationale="test",
            hypothesis=hypothesis(FaultClass.TOOL_FAILURE),
        )


async def _async(value):
    return value


class TestTierEscalation:
    def _tx(self, fault: FaultClass, tier: RepairTier, committed: bool) -> RepairTransaction:
        return RepairTransaction(
            transaction_id=f"tx{fault.value}",
            pre_state_key="k",
            patch=Patch(
                patch_id="p",
                family="retry_node",
                tier=tier,
                target="s1__de",
                description="d",
                rationale="r",
                hypothesis=hypothesis(fault),
            ),
            outcome=TransactionOutcome.COMMITTED if committed else TransactionOutcome.ROLLED_BACK,
        )

    def test_natural_tier_is_used_first(self) -> None:
        decision = decide_tier(
            FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, RepairLog(), RepairPolicy()
        )
        assert decision.allowed and decision.tier is RepairTier.CONTRACT_REPAIR
        assert decision.escalated_from is None

    def test_recurrence_escalates_the_tier(self) -> None:
        """The same cause returning means the repairs addressed a symptom."""
        log = RepairLog()
        for _ in range(2):
            log.append(self._tx(FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, True))
        decision = decide_tier(
            FaultClass.TOOL_FAILURE,
            RepairTier.CONTRACT_REPAIR,
            log,
            RepairPolicy(escalate_after_repeats=2),
        )
        assert decision.tier is RepairTier.LOCAL_OPTIMIZATION
        assert decision.escalated_from is RepairTier.CONTRACT_REPAIR
        assert "addressing a symptom" in decision.reason

    def test_exhausted_budget_stops_repair(self) -> None:
        log = RepairLog()
        policy = RepairPolicy(max_contract_repairs=1, escalate_after_repeats=99)
        log.append(self._tx(FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, True))
        decision = decide_tier(
            FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, log, policy
        )
        assert not decision.allowed and "budget exhausted" in decision.reason

    def test_log_computes_reportable_metrics(self) -> None:
        log = RepairLog()
        log.append(self._tx(FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, False))
        committed = self._tx(FaultClass.TOOL_FAILURE, RepairTier.CONTRACT_REPAIR, True)
        committed.attempt = 2
        log.append(committed)
        assert log.time_to_recovery() == 2
        assert log.fault_recurrences(FaultClass.TOOL_FAILURE) == 2
        assert log.collateral_regression_rate() is None  # no shadow results


class TestRepairLoop:
    async def test_stops_and_asks_for_evidence_when_uncertain(self) -> None:
        trace = Trace(run_id="r")
        trace.record_result(
            "s1__de",
            __import__("agentcoop.components.base", fromlist=["InvocationResult"]).InvocationResult(
                ok=False, errors=["something went wrong"]
            ),
        )
        execution = ExecutionResult(
            ok=False,
            trace=trace,
            state=RunState(run_id="r", workflow_id="w", task_id="t"),
            report=CheckReport(),
        )
        loop = RepairLoop(ctx(), library(), policy=RepairPolicy(max_diagnosis_entropy_bits=0.1))
        outcome = await loop.run(workflow(), execution, lambda w: _async(execution))
        assert not outcome.repaired
        assert "entropy" in outcome.terminal_reason

    async def test_escalating_fault_classes_are_not_auto_repaired(self) -> None:
        """Irreducible uncertainty and specification defects go to a human."""
        assert FAULT_TAXONOMY[FaultClass.IRREDUCIBLE_UNCERTAINTY].escalate
        assert FAULT_TAXONOMY[FaultClass.TASK_SPECIFICATION].escalate
