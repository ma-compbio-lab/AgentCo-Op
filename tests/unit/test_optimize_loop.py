"""Typed ECPS state, objective gating, and optimization-loop behavior."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from agentcoop.compile.compiler import CompilationResult, Compiler
from agentcoop.compile.grammar import RuleContext
from agentcoop.components.base import InvocationResult
from agentcoop.execute.engine import ExecutionResult
from agentcoop.execute.state import RunState, TerminationReason
from agentcoop.execute.trace import EventKind, Trace
from agentcoop.ir.artifacts import Artifact, ArtifactType
from agentcoop.ir.capability import (
    CapabilityCard,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    EmpiricalRecord,
    IOContract,
    ProbeOutcome,
)
from agentcoop.ir.checks import (
    CheckLevel,
    CheckReport,
    CheckResult,
    CheckStatus,
    SignalSource,
)
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    Preference,
    ResourceLimits,
    Subgoal,
    TaskEvidenceDossier,
)
from agentcoop.ir.evidence import DecisionKind, EvidenceLedger
from agentcoop.ir.preference import VerbosityMode, VerbosityPolicy
from agentcoop.ir.preference import (
    OrderedPairJudgment,
    PairRubric,
    PairwiseVerdict,
    RubricCriterion,
    Scorepad,
    ScorepadEntry,
)
from agentcoop.ir.utility import Objective, UtilityVector
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, atomics
from agentcoop.optimize.loop import (
    OptimizationLoop,
    compatible_observed_front,
    derive_observed_utility,
    determine_front_cost_unit,
    execution_admission_reasons,
    front_latency_available,
    objective_execution_snapshot,
    validate_cost_profile,
)
from agentcoop.optimize.judge import JudgePanel, JudgeRequest, JudgeResponse
from agentcoop.optimize.mutations import (
    CertifiedParameterDomain,
    ConfigGridMutationSource,
    ConfigMutation,
    MutationProposal,
    workflow_fingerprint,
)
from agentcoop.optimize.state import (
    CandidateEvaluation,
    CaseExecution,
    CostUnit,
    EvaluationCase,
    OptimizationBudget,
    OptimizationEvent,
    OptimizationLedger,
    OptimizationOutcome,
    OptimizationPolicy,
    OptimizationCandidate,
    OptimizationState,
    OptimizationStopReason,
    MutationRecord,
)
from agentcoop.probe import parameter_domain_suite


def evaluation_case(case_id: str = "case-0", *, seed: int = 7) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        input_fingerprint="input::abc",
        seed=seed,
        limits=ResourceLimits(max_usd=2.0, max_tokens=1000),
        evaluator_ids=("hard-schema", "silent-empty"),
        tool_snapshot_id="tools::v1",
    )


def policy() -> OptimizationPolicy:
    return OptimizationPolicy(
        budget=OptimizationBudget(
            max_candidate_executions=20,
            max_judge_calls=40,
            max_candidates=8,
            max_generations=2,
        ),
        ridge=1.0,
        delta=0.1,
        epsilon={"clarity": 0.0},
        max_iterations=100,
        tolerance=1e-8,
        min_judge_families=2,
        max_payload_chars=50_000,
        verbosity=VerbosityPolicy(mode=VerbosityMode.NONE),
    )


def stopped_event(index: int = 0) -> OptimizationEvent:
    return OptimizationEvent(
        index=index,
        state=OptimizationState.STOPPED,
        detail="terminal",
    )


def objective_fixture() -> tuple[
    TaskEvidenceDossier,
    ComponentLibrary,
    RuleContext,
    str,
    CompiledWorkflow,
    OptimizationCandidate,
]:
    report_type = ArtifactType(
        name="report",
        json_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    )
    dossier = TaskEvidenceDossier(
        task_id="objective-task",
        goal="Write a useful report.",
        artifact_types=[report_type],
        required_outputs=["report"],
        subgoals=[
            Subgoal(
                subgoal_id="write",
                description="write",
                required_capability="write_report",
                produces=["report"],
            )
        ],
        preferences=[
            Preference(
                preference_id="clarity",
                description="Prefer clarity.",
                dimension="clarity",
            )
        ],
        evaluators={CheckLevel.PREFERENCE: EvaluatorAvailability.RUBRIC},
    )
    certification_probes = [
        ProbeOutcome(probe_id=f"probe-{kind}", kind=kind, passed=True)
        for kind in ("reachable", "schema", "smoke", "invalid_input", "resource")
    ]
    card = CapabilityCard(
        name="writer",
        kind=ComponentKind.PYTHON_FUNCTION,
        functional_capabilities=["write_report"],
        io=IOContract(
            produces=[report_type],
            parameters={"style": {"type": "string", "default": "default"}},
        ),
        empirical=EmpiricalRecord(probes=certification_probes),
    )
    domain_specs = parameter_domain_suite(
        card,
        parameter="style",
        values=("default", "concise"),
    )
    domain_probes = [
        ProbeOutcome(
            probe_id=spec.probe_id,
            kind=spec.kind,
            passed=True,
            evidence={
                **spec.expectations,
                "contract_preserving": True,
            },
        )
        for spec in domain_specs
    ]
    card = card.model_copy(
        update={
            "empirical": EmpiricalRecord(
                probes=[*certification_probes, *domain_probes]
            )
        }
    )
    library = ComponentLibrary()
    library.add(card)
    compiler = Compiler()
    compilation = compiler.compile(dossier, library)
    assert compilation.workflow is not None
    assert compilation.selection is not None
    assert compilation.selection.chosen is not None
    candidate_id = compilation.selection.chosen
    workflow = compilation.workflows[candidate_id]
    candidate = OptimizationCandidate(
        candidate_id=candidate_id,
        workflow=workflow,
        parent_id=None,
        mutation_id=None,
        generation=0,
        static_estimate=compilation.estimates[candidate_id],
    )
    return (
        dossier,
        library,
        compiler.context(dossier, library),
        candidate_id,
        workflow,
        candidate,
    )


def candidate_as(
    candidate: OptimizationCandidate, candidate_id: str
) -> OptimizationCandidate:
    return candidate.model_copy(
        update={
            "candidate_id": candidate_id,
            "static_estimate": candidate.static_estimate.model_copy(
                update={"candidate_id": candidate_id}
            ),
        }
    )


def case_execution(
    workflow: CompiledWorkflow,
    case: EvaluationCase,
    *,
    checks: tuple[CheckResult, ...] = (),
    payload: object = None,
    cost: CostProfile | None = None,
    cost_unit: CostUnit | None = CostUnit.USD,
    latency_measured: bool = True,
    raw_ok: bool | None = None,
    case_fingerprint: str | None = None,
) -> CaseExecution:
    node_id = atomics(workflow.term)[0].term_id
    value = {"text": "substantive"} if payload is None else payload
    artifact = Artifact(
        artifact_id="report-artifact",
        type_name="report",
        payload=value,
        producer=node_id,
    ).finalize()
    trace = Trace(run_id="run")
    trace.record(EventKind.RUN_START, detail=workflow.workflow_id)
    trace.record_result(
        node_id,
        InvocationResult(ok=True, outputs={"report": artifact}),
    )
    stored = trace.record_artifact(artifact, node_id=node_id)
    for check in checks:
        trace.record_check(check)
    trace.record(EventKind.RUN_END, detail="completed")
    state = RunState(
        run_id="run",
        workflow_id=workflow.workflow_id,
        task_id=workflow.task_id,
        executed=[node_id],
        termination_reason=TerminationReason.COMPLETED,
    )
    report = CheckReport(results=list(checks))
    result = ExecutionResult(
        ok=(state.ok and report.hard_constraints_satisfied)
        if raw_ok is None
        else raw_ok,
        trace=trace,
        state=state,
        outputs={"report": stored},
        cost=cost or CostProfile(usd=0.1, tokens=10, latency_s=0.5),
        report=report,
    )
    return CaseExecution(
        case_fingerprint=case_fingerprint or case.fingerprint(),
        result=result,
        cost_unit=cost_unit,
        latency_measured=latency_measured,
    )


def compilation_archive(
    candidate_ids: tuple[str, ...] = ("a", "b"),
) -> tuple[RuleContext, CompilationResult]:
    _, _, ctx, _, workflow, candidate = objective_fixture()
    workflows: dict[str, CompiledWorkflow] = {}
    estimates = {}
    for candidate_id in candidate_ids:
        workflows[candidate_id] = workflow.model_copy(
            deep=True,
            update={"workflow_id": f"loop::{candidate_id}"},
        )
        estimates[candidate_id] = candidate.static_estimate.model_copy(
            deep=True,
            update={"candidate_id": candidate_id},
        )
    return ctx, CompilationResult(
        workflow=workflows[candidate_ids[0]] if candidate_ids else None,
        workflows=workflows,
        estimates=estimates,
        rejected={"compiler-rejected": "static refusal"},
    )


def authorized_mutation_source(
    ctx: RuleContext,
    compilation: CompilationResult,
) -> ConfigGridMutationSource:
    parent = compilation.workflows[sorted(compilation.workflows)[0]]
    target = atomics(parent.term)[0]
    card = ctx.library.require(target.component)
    specs = parameter_domain_suite(
        card,
        parameter="style",
        values=("default", "concise"),
    )
    domain = CertifiedParameterDomain(
        domain_id="domain::style",
        component=target.component,
        key="style",
        values=("default", "concise"),
        probe_ids=tuple(spec.probe_id for spec in specs),
    )
    mutation = ConfigMutation(
        mutation_id="mutation::style",
        target=target.term_id,
        key="style",
        value="concise",
        domain_id=domain.domain_id,
        rationale="probe-certified concise style",
    )
    return ConfigGridMutationSource(
        ctx.library,
        domains=(domain,),
        mutations=(mutation,),
    )


class RecordingHarness:
    def __init__(
        self,
        *,
        payloads: dict[str, object] | None = None,
        costs: dict[str, CostProfile] | None = None,
        units: dict[str, CostUnit | None] | None = None,
        latency_measured: dict[str, bool] | None = None,
    ) -> None:
        self.payloads = payloads or {}
        self.costs = costs or {}
        self.units = units or {}
        self.latency_measured = latency_measured or {}
        self.calls: list[tuple[str, str, int]] = []

    async def __call__(
        self, workflow: CompiledWorkflow, case: EvaluationCase
    ) -> CaseExecution:
        candidate_id = (
            "child"
            if any(term.config.get("style") == "concise" for term in atomics(workflow.term))
            else workflow.workflow_id.rsplit("::", 1)[-1]
        )
        self.calls.append((candidate_id, case.case_id, case.seed))
        return case_execution(
            workflow,
            case,
            payload=self.payloads.get(candidate_id, {"text": candidate_id}),
            cost=self.costs.get(
                candidate_id,
                CostProfile(usd=0.1, tokens=10, latency_s=0.5),
            ),
            cost_unit=self.units.get(candidate_id, CostUnit.USD),
            latency_measured=self.latency_measured.get(candidate_id, True),
        )


class ContentJudge:
    source = SignalSource.LLM_JUDGE

    def __init__(
        self,
        family: str,
        *,
        preferred_text: str | None = None,
        preferred_by_pair: dict[frozenset[str], str] | None = None,
        always_left: bool = False,
        tie: bool = False,
        unavailable: bool = False,
        cost: CostProfile | None = None,
    ) -> None:
        self.judge_id = f"judge::{family}"
        self.family = family
        self.preferred_text = preferred_text
        self.preferred_by_pair = preferred_by_pair or {}
        self.always_left = always_left
        self.tie = tie
        self.unavailable = unavailable
        self.cost = cost or CostProfile(usd=0.01, tokens=1)
        self.requests: list[JudgeRequest] = []

    @staticmethod
    def _text(request: JudgeRequest, *, left: bool) -> str:
        view = request.left if left else request.right
        snapshot = view.outputs[0].snapshot
        if isinstance(snapshot, dict):
            return str(snapshot.get("text", ""))
        return str(snapshot)

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        self.requests.append(request)
        if self.unavailable:
            raise RuntimeError("judge unavailable")
        rubric = request.frozen_rubric or PairRubric(
            rubric_id=f"rubric::{self.family}",
            version=request.meta_rubric.version,
            meta_rubric=request.meta_rubric.ref(),
            criteria=tuple(
                RubricCriterion(
                    criterion_id=f"criterion::{criterion.preference_id}",
                    preference_id=criterion.preference_id,
                    description=f"Compare {criterion.preference_id} from packet evidence.",
                    direction=criterion.direction,
                )
                for criterion in request.meta_rubric.criteria
            ),
        )
        if self.tie:
            verdict = PairwiseVerdict.TIE
        elif self.always_left:
            verdict = PairwiseVerdict.LEFT
        else:
            left_text = self._text(request, left=True)
            right_text = self._text(request, left=False)
            preferred = self.preferred_by_pair.get(
                frozenset((left_text, right_text)),
                self.preferred_text,
            )
            verdict = (
                PairwiseVerdict.LEFT
                if left_text == preferred
                else PairwiseVerdict.RIGHT
            )
        directional = verdict in {PairwiseVerdict.LEFT, PairwiseVerdict.RIGHT}
        preferred = request.left if verdict is PairwiseVerdict.LEFT else request.right
        entries = tuple(
            ScorepadEntry(
                preference_id=criterion.preference_id,
                criterion_id=criterion.criterion_id,
                verdict=verdict,
                left_score=8.0 if directional else 7.0,
                right_score=6.0 if directional else 7.0,
                evidence_refs=(preferred.outputs[0].reference_id,) if directional else (),
                confidence=0.8,
                rationale="Preferred by packet evidence." if directional else "",
            )
            for criterion in rubric.criteria
        )
        return JudgeResponse(
            judgment=OrderedPairJudgment(
                judgment_id="untrusted-judgment",
                pair_id="untrusted-pair",
                case_id="untrusted-case",
                left_packet_id="untrusted-left",
                right_packet_id="untrusted-right",
                judge_id="untrusted-judge",
                judge_family="untrusted-family",
                source=SignalSource.LLM_JUDGE,
                rubric=rubric,
                scorepad=Scorepad(entries=entries),
            ),
            cost=self.cost,
        )


class EmptyMutationSource:
    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        return ()


class BrokenMutationSource:
    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        raise RuntimeError("domain enumeration failed")


class MalformedMutationSource:
    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        return ("not-a-typed-proposal",)  # type: ignore[return-value]


class ExplodingPanel:
    def __init__(self) -> None:
        self.judges = (
            ContentJudge("family-1"),
            ContentJudge("family-2"),
        )

    async def compare(self, **kwargs: object) -> object:
        raise RuntimeError("panel failed after transport orchestration")


class ForgedMutationSource:
    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        parent = sorted(parents, key=lambda item: item.candidate_id)[0]
        if parent.generation > 0:
            return ()
        original = atomics(parent.workflow.term)[0]
        replacement = original.model_copy(
            deep=True,
            update={"config": {**original.config, "style": "concise"}},
        )
        child_workflow = parent.workflow.with_term(replacement).model_copy(
            deep=True,
            update={"workflow_id": "loop::child"},
        )
        fingerprint = workflow_fingerprint(child_workflow)
        child_id = f"child::{fingerprint[:12]}"
        child = OptimizationCandidate(
            candidate_id=child_id,
            workflow=child_workflow,
            parent_id=parent.candidate_id,
            mutation_id="mutation::style",
            generation=parent.generation + 1,
            static_estimate=parent.static_estimate.model_copy(
                deep=True,
                update={"candidate_id": child_id},
            ),
        )
        record = MutationRecord(
            mutation_id="mutation::style",
            parent_id=parent.candidate_id,
            child_id=child_id,
            target=original.term_id,
            key="style",
            value="concise",
            domain_id="domain::style",
            domain_component=original.component,
            domain_values=("default", "concise"),
            probe_ids=("probe::style",),
            rationale="probe-certified concise style",
            generation=child.generation,
        )
        return (MutationProposal(candidate=child, record=record),)


class MixedMutationSource:
    def __init__(self, valid: ConfigGridMutationSource) -> None:
        self.valid = valid

    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        return (
            *self.valid.propose(
                parents,
                seen_fingerprints=seen_fingerprints,
            ),
            *ForgedMutationSource().propose(
                parents,
                seen_fingerprints=seen_fingerprints,
            ),
        )


class ExistingIdForgedMutationSource:
    def propose(
        self,
        parents: tuple[OptimizationCandidate, ...],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        parent = sorted(parents, key=lambda item: item.candidate_id)[0]
        forged = ForgedMutationSource().propose(
            parents,
            seen_fingerprints=seen_fingerprints,
        )[0]
        candidate = forged.candidate.model_copy(
            update={
                "candidate_id": parent.candidate_id,
                "static_estimate": forged.candidate.static_estimate.model_copy(
                    update={"candidate_id": parent.candidate_id}
                ),
            }
        )
        record = forged.record.model_copy(
            update={"child_id": parent.candidate_id}
        )
        return (MutationProposal(candidate=candidate, record=record),)


class TestOptimizationStateRecords:
    def test_case_fingerprint_is_canonical_and_sensitive_to_contract(self) -> None:
        first = evaluation_case()
        reordered = first.model_copy(
            update={"evaluator_ids": tuple(reversed(first.evaluator_ids))}
        )
        changed_seed = evaluation_case(seed=8)

        assert first.fingerprint() == evaluation_case().fingerprint()
        assert first.fingerprint() == reordered.fingerprint()
        assert first.fingerprint().startswith("case::")
        assert first.fingerprint() != changed_seed.fingerprint()

    def test_case_rejects_extra_fields_and_invalid_limits(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationCase.model_validate(
                {**evaluation_case().model_dump(), "unexpected": True}
            )
        with pytest.raises(ValidationError, match="limits"):
            EvaluationCase.model_validate(
                {
                    **evaluation_case().model_dump(),
                    "limits": ResourceLimits(max_usd=-1.0),
                }
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_candidate_executions", -1),
            ("max_judge_calls", True),
            ("max_candidates", -1),
            ("max_generations", -1),
            ("soft_max_usd", math.nan),
            ("soft_max_usd", True),
            ("soft_max_tokens", -1),
            ("soft_max_execution_latency_s", math.inf),
        ],
    )
    def test_budget_rejects_invalid_values(self, field: str, value: object) -> None:
        values = OptimizationBudget().model_dump()
        values[field] = value
        with pytest.raises(ValidationError):
            OptimizationBudget.model_validate(values)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("ridge", 0.0),
            ("ridge", math.inf),
            ("ridge", True),
            ("delta", 0.0),
            ("delta", 1.0),
            ("max_iterations", 0),
            ("tolerance", math.nan),
            ("tolerance", True),
            ("min_judge_families", 1),
            ("min_judge_families", 3),
            ("max_payload_chars", 0),
        ],
    )
    def test_policy_rejects_invalid_values(self, field: str, value: object) -> None:
        values = policy().model_dump()
        values[field] = value
        with pytest.raises(ValidationError):
            OptimizationPolicy.model_validate(values)

    def test_policy_epsilon_is_finite_non_negative(self) -> None:
        for epsilon in ({"clarity": -0.1}, {"clarity": math.nan}, {"": 0.0}):
            with pytest.raises(ValidationError, match="epsilon"):
                OptimizationPolicy.model_validate(
                    {**policy().model_dump(), "epsilon": epsilon}
                )

    def test_ledger_accounts_execution_and_judge_resources(self) -> None:
        ledger = OptimizationLedger(
            candidate_executions=3,
            judge_calls=4,
            execution_cost=CostProfile(
                usd=1.25, tokens=100, latency_s=3.0, cpu_seconds=2.0
            ),
            judge_cost=CostProfile(usd=0.75, tokens=50, latency_s=1.0),
            cost_accounting_complete=True,
            soft_limits_exceeded=("soft_max_usd",),
        )

        assert ledger.total_cost().usd == pytest.approx(2.0)
        assert ledger.total_cost().tokens == 150
        assert ledger.execution_latency_s == pytest.approx(3.0)
        assert ledger.soft_limits_exceeded == ("soft_max_usd",)

    def test_invalid_cost_requires_incomplete_accounting_stamp(self) -> None:
        with pytest.raises(ValidationError, match="accounting"):
            OptimizationLedger(
                execution_cost=CostProfile(usd=-1.0),
                cost_accounting_complete=True,
            )

        incomplete = OptimizationLedger(
            execution_cost=CostProfile(usd=-1.0),
            cost_accounting_complete=False,
        )
        assert incomplete.cost_accounting_complete is False

    def test_outcome_rejects_duplicate_cases_and_non_contiguous_events(self) -> None:
        base = dict(
            dossier_fingerprint="dossier::abc",
            cases=(evaluation_case("duplicate"), evaluation_case("duplicate")),
            policy=policy(),
            events=(stopped_event(),),
            ledger=OptimizationLedger(),
            state=OptimizationState.STOPPED,
            stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
        )
        with pytest.raises(ValidationError, match="duplicate case"):
            OptimizationOutcome(**base)

        with pytest.raises(ValidationError, match="event indices"):
            OptimizationOutcome(
                **{
                    **base,
                    "cases": (evaluation_case(),),
                    "events": (stopped_event(index=1),),
                }
            )

    def test_outcome_round_trips_schema_and_algorithm_versions(self) -> None:
        outcome = OptimizationOutcome(
            schema_version="schema-test-v3",
            algorithm_version="algorithm-test-v7",
            dossier_fingerprint="dossier::abc",
            cases=(evaluation_case(),),
            policy=policy(),
            events=(stopped_event(),),
            ledger=OptimizationLedger(),
            state=OptimizationState.STOPPED,
            stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
        )

        replayed = OptimizationOutcome.model_validate_json(outcome.model_dump_json())

        assert replayed == outcome
        assert replayed.schema_version == "schema-test-v3"
        assert replayed.algorithm_version == "algorithm-test-v7"

    def test_mutation_definition_can_be_reused_across_distinct_lineages(self) -> None:
        _, _, _, _, _, base = objective_fixture()
        parent_a = candidate_as(base, "parent-a")
        parent_b = candidate_as(base, "parent-b")

        def child(parent: OptimizationCandidate, child_id: str) -> OptimizationCandidate:
            return parent.model_copy(
                deep=True,
                update={
                    "candidate_id": child_id,
                    "parent_id": parent.candidate_id,
                    "mutation_id": "shared-mutation-definition",
                    "generation": 1,
                    "static_estimate": parent.static_estimate.model_copy(
                        update={"candidate_id": child_id}
                    ),
                },
            )

        child_a = child(parent_a, "child-a")
        child_b = child(parent_b, "child-b")

        def record(parent_id: str, child_id: str) -> MutationRecord:
            return MutationRecord(
                mutation_id="shared-mutation-definition",
                parent_id=parent_id,
                child_id=child_id,
                target="write__writer",
                key="style",
                value="concise",
                domain_id="domain::style",
                domain_component="writer",
                domain_values=("default", "concise"),
                probe_ids=("probe::style",),
                rationale="same authorized operation on another parent",
                generation=1,
            )

        evaluations = tuple(
            CandidateEvaluation(
                candidate=candidate,
                feasible=False,
                rejection_reasons=("not executed",),
            )
            for candidate in (parent_a, parent_b, child_a, child_b)
        )
        records = (
            record("parent-a", "child-a"),
            record("parent-b", "child-b"),
        )

        outcome = OptimizationOutcome(
            dossier_fingerprint="dossier::abc",
            cases=(evaluation_case(),),
            policy=policy(),
            candidates=evaluations,
            mutation_records=records,
            events=(stopped_event(),),
            ledger=OptimizationLedger(),
            state=OptimizationState.STOPPED,
            stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
        )

        assert outcome.mutation_records == records
        with pytest.raises(ValidationError, match="duplicate mutation record"):
            OptimizationOutcome(
                dossier_fingerprint="dossier::abc",
                cases=(evaluation_case(),),
                policy=policy(),
                candidates=evaluations,
                mutation_records=(records[0], records[0]),
                events=(stopped_event(),),
                ledger=OptimizationLedger(),
                state=OptimizationState.STOPPED,
                stop_reason=OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES,
            )

    def test_all_states_and_stop_reasons_are_typed_and_stable(self) -> None:
        assert {state.value for state in OptimizationState} == {
            "initializing",
            "executing",
            "objective_gating",
            "comparing",
            "modeling",
            "mutating",
            "stopped",
        }
        assert {reason.value for reason in OptimizationStopReason} == {
            "objective_singleton",
            "confident_preference",
            "verbosity_policy_singleton",
            "no_admissible_candidates",
            "no_preferences",
            "no_cases",
            "ineligible_evaluator",
            "no_judges",
            "insufficient_judge_diversity",
            "budget_exhausted",
            "resource_accounting_invalid",
            "evaluator_unstable",
            "mutation_source_invalid",
            "verbosity_baseline_unavailable",
            "no_mutations",
            "front_stable_unresolved",
        }
        assert CostUnit("usd") is CostUnit.USD
        assert CostUnit("tokens") is CostUnit.TOKENS


class TestObjectiveExecutionBoundary:
    def test_objective_snapshot_removes_subjective_checks_without_resequencing(self) -> None:
        _, _, _, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        checks = (
            CheckResult(
                check_id="duplicate|id=fail",
                level=CheckLevel.PREFERENCE,
                status=CheckStatus.FAIL,
                source=SignalSource.DETERMINISTIC,
                summary="subjective failure",
                blocking=True,
            ),
            CheckResult(
                check_id="duplicate|id=fail",
                level=CheckLevel.HARD,
                status=CheckStatus.UNAVAILABLE,
                source=SignalSource.HUMAN,
                summary="human signal with a duplicate identifier",
                blocking=True,
            ),
        )
        execution = case_execution(
            workflow,
            case,
            checks=checks,
            raw_ok=False,
        )

        snapshot = objective_execution_snapshot(execution.result)

        assert snapshot.ok is True
        assert snapshot.report.results == []
        assert snapshot.trace.checks == []
        assert all(event.kind is not EventKind.CHECK for event in snapshot.trace.events)
        assert [event.step for event in snapshot.trace.events] == [1, 2, 5]
        assert snapshot.trace.step == execution.result.trace.step

    @pytest.mark.parametrize(
        ("level", "source", "status"),
        [
            (CheckLevel.PREFERENCE, SignalSource.DETERMINISTIC, status)
            for status in (
                CheckStatus.PASS,
                CheckStatus.FAIL,
                CheckStatus.UNAVAILABLE,
            )
        ]
        + [
            (CheckLevel.HARD, source, status)
            for source in (SignalSource.LLM_JUDGE, SignalSource.HUMAN)
            for status in (
                CheckStatus.PASS,
                CheckStatus.FAIL,
                CheckStatus.UNAVAILABLE,
            )
        ],
    )
    def test_subjective_signals_have_zero_objective_effect(
        self,
        level: CheckLevel,
        source: SignalSource,
        status: CheckStatus,
    ) -> None:
        _, _, ctx, _, workflow, candidate = objective_fixture()
        case = evaluation_case()
        clean = case_execution(workflow, case)
        subjective = CheckResult(
            check_id="adversarial:subjective",
            level=level,
            status=status,
            source=source,
            summary="rubric keywords and confident prose",
            blocking=True,
        )
        tainted = case_execution(
            workflow,
            case,
            checks=(subjective,),
            raw_ok=False if status is CheckStatus.FAIL else None,
        )

        assert execution_admission_reasons(workflow, case, clean, ctx) == ()
        assert execution_admission_reasons(workflow, case, tainted, ctx) == ()
        clean_utility = derive_observed_utility(
            candidate,
            (clean,),
            front_cost_unit=CostUnit.USD,
            latency_available=True,
        )
        tainted_utility = derive_observed_utility(
            candidate,
            (tainted,),
            front_cost_unit=CostUnit.USD,
            latency_available=True,
        )
        assert tainted_utility == clean_utility

    @pytest.mark.parametrize(
        "cost",
        [
            CostProfile(usd=-0.1),
            CostProfile(latency_s=math.nan),
            CostProfile(cpu_seconds=math.inf),
            CostProfile.model_construct(tokens=True),
        ],
    )
    def test_cost_validation_rejects_invalid_authoritative_values(
        self, cost: CostProfile
    ) -> None:
        assert validate_cost_profile(cost)

    def test_cost_validation_accepts_explicit_zero(self) -> None:
        assert validate_cost_profile(CostProfile()) == ()

    def test_admission_accepts_a_complete_objective_execution(self) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()

        assert execution_admission_reasons(
            workflow, case, case_execution(workflow, case), ctx
        ) == ()

    def test_admission_rejects_specification_and_design_evidence_defects(self) -> None:
        dossier, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        execution = case_execution(workflow, case)
        defective_ctx = ctx.model_copy(
            update={
                "dossier": dossier.model_copy(
                    update={"required_outputs": ["report", "unproduced"]}
                )
            }
        )
        empty_evidence = workflow.model_copy(update={"evidence": EvidenceLedger()})
        without_bind = EvidenceLedger(
            records={
                key: record
                for key, record in workflow.evidence.records.items()
                if record.decision_kind is not DecisionKind.BIND_COMPONENT
            }
        )
        missing_binding = workflow.model_copy(update={"evidence": without_bind})

        assert "dossier has specification defects" in execution_admission_reasons(
            workflow, case, execution, defective_ctx
        )
        assert "workflow evidence ledger is empty" in execution_admission_reasons(
            empty_evidence,
            case,
            case_execution(empty_evidence, case),
            ctx,
        )
        assert any(
            reason.startswith("atomic lacks binding evidence:")
            for reason in execution_admission_reasons(
                missing_binding,
                case,
                case_execution(missing_binding, case),
                ctx,
            )
        )

    def test_admission_rejects_fresh_static_failure(self) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        broken = workflow.model_copy(
            update={
                "term": Atomic(
                    component="missing-component",
                    subgoal_id="write",
                ).ensure_ids()
            }
        )

        reasons = execution_admission_reasons(
            broken, case, case_execution(broken, case), ctx
        )

        assert any(reason.startswith("blocking static check:") for reason in reasons)

    def test_admission_rejects_case_execution_and_output_failures(self) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        baseline = case_execution(workflow, case)
        failed_state = baseline.result.state.model_copy(update={"failed": ["node"]})
        failed = baseline.model_copy(
            update={"result": baseline.result.model_copy(update={"state": failed_state})}
        )
        missing_output = baseline.model_copy(
            update={
                "result": baseline.result.model_copy(update={"outputs": {}})
            }
        )

        assert "case fingerprint mismatch" in execution_admission_reasons(
            workflow,
            case,
            baseline.model_copy(update={"case_fingerprint": "case::wrong"}),
            ctx,
        )
        assert "objective execution is not ok" in execution_admission_reasons(
            workflow, case, failed, ctx
        )
        assert "missing required output: report" in execution_admission_reasons(
            workflow, case, missing_output, ctx
        )

    @pytest.mark.parametrize(
        "status",
        [CheckStatus.FAIL, CheckStatus.UNAVAILABLE, CheckStatus.INCONCLUSIVE],
    )
    def test_admission_rejects_every_blocking_objective_non_pass(
        self, status: CheckStatus
    ) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        check = CheckResult(
            check_id="objective-blocker",
            level=CheckLevel.HARD,
            status=status,
            source=SignalSource.DETERMINISTIC,
            blocking=True,
        )

        reasons = execution_admission_reasons(
            workflow,
            case,
            case_execution(workflow, case, checks=(check,)),
            ctx,
        )

        assert "blocking objective check: objective-blocker" in reasons

    def test_trace_only_objective_blocker_cannot_bypass_admission(self) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        execution = case_execution(workflow, case)
        blocker = CheckResult(
            check_id="trace-only-blocker",
            level=CheckLevel.HARD,
            status=CheckStatus.UNAVAILABLE,
            source=SignalSource.DETERMINISTIC,
            blocking=True,
        )
        trace = execution.result.trace.model_copy(
            deep=True,
            update={"checks": [blocker]},
        )
        malformed = execution.model_copy(
            update={
                "result": execution.result.model_copy(update={"trace": trace})
            }
        )

        snapshot = objective_execution_snapshot(malformed.result)

        assert snapshot.ok is False
        assert "blocking objective check: trace-only-blocker" in (
            execution_admission_reasons(workflow, case, malformed, ctx)
        )

    def test_admission_rejects_silent_empty_output_and_case_limits(self) -> None:
        _, _, ctx, _, workflow, _ = objective_fixture()
        case = evaluation_case()
        empty = case_execution(workflow, case, payload="")
        constrained = case.model_copy(
            update={
                "limits": ResourceLimits(
                    max_usd=0.05,
                    max_tokens=5,
                    max_wall_time_s=0.25,
                    max_component_calls=0,
                )
            }
        )
        over_budget = case_execution(workflow, constrained)

        empty_reasons = execution_admission_reasons(workflow, case, empty, ctx)
        budget_reasons = execution_admission_reasons(
            workflow, constrained, over_budget, ctx
        )

        assert any(reason.startswith("blocking detector signal:") for reason in empty_reasons)
        assert {
            "case USD limit exceeded",
            "case token limit exceeded",
            "case latency limit exceeded",
            "case component-call limit exceeded",
        }.issubset(set(budget_reasons))


class TestObservedObjectiveFront:
    def _evaluation(
        self,
        candidate: OptimizationCandidate,
        executions: tuple[CaseExecution, ...],
        utility: UtilityVector | None = None,
    ) -> CandidateEvaluation:
        return CandidateEvaluation(
            candidate=candidate,
            executions=executions,
            feasible=True,
            utility=utility or UtilityVector(),
        )

    def test_front_cost_unit_requires_one_explicit_basis_everywhere(self) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        first = evaluation_case("first")
        second = evaluation_case("second")
        usd = self._evaluation(
            candidate,
            (
                case_execution(workflow, first, cost_unit=CostUnit.USD),
                case_execution(workflow, second, cost_unit=CostUnit.USD),
            ),
        )
        token_candidate = candidate_as(candidate, "candidate-token")
        tokens = self._evaluation(
            token_candidate,
            (
                case_execution(workflow, first, cost_unit=CostUnit.TOKENS),
                case_execution(workflow, second, cost_unit=CostUnit.TOKENS),
            ),
        )
        unknown_candidate = candidate_as(candidate, "candidate-unknown")
        unknown = self._evaluation(
            unknown_candidate,
            (case_execution(workflow, first, cost_unit=None),),
        )

        assert determine_front_cost_unit((usd,)) is CostUnit.USD
        assert determine_front_cost_unit((tokens,)) is CostUnit.TOKENS
        assert determine_front_cost_unit((usd, tokens)) is None
        assert determine_front_cost_unit((unknown,)) is None

    @pytest.mark.parametrize("unit", [CostUnit.USD, CostUnit.TOKENS])
    def test_explicitly_stamped_true_zero_is_measured(self, unit: CostUnit) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        case = evaluation_case()
        evaluation = self._evaluation(
            candidate,
            (
                case_execution(
                    workflow,
                    case,
                    cost=CostProfile(),
                    cost_unit=unit,
                ),
            ),
        )

        assert determine_front_cost_unit((evaluation,)) is unit

    def test_latency_requires_a_stamp_on_every_feasible_execution(self) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        first = evaluation_case("first")
        second = evaluation_case("second")
        complete = self._evaluation(
            candidate,
            (
                case_execution(workflow, first, latency_measured=True),
                case_execution(workflow, second, latency_measured=True),
            ),
        )
        partial = self._evaluation(
            candidate,
            (
                case_execution(workflow, first, latency_measured=True),
                case_execution(workflow, second, latency_measured=False),
            ),
        )

        assert front_latency_available((complete,)) is True
        assert front_latency_available((partial,)) is False

    def test_front_measurements_require_the_same_nonempty_matched_case_set(self) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        first = evaluation_case("first")
        second = evaluation_case("second")
        complete = self._evaluation(
            candidate,
            (
                case_execution(workflow, first),
                case_execution(workflow, second),
            ),
        )
        missing_case = self._evaluation(
            candidate_as(candidate, "missing-case"),
            (case_execution(workflow, first),),
        )
        no_cases = self._evaluation(candidate_as(candidate, "no-cases"), ())

        assert determine_front_cost_unit((complete, missing_case)) is None
        assert front_latency_available((complete, missing_case)) is False
        assert determine_front_cost_unit((complete, no_cases)) is None
        assert front_latency_available((complete, no_cases)) is False

    def test_utility_uses_total_cost_mean_latency_and_compile_time_risk(self) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        executions = (
            case_execution(
                workflow,
                evaluation_case("first"),
                cost=CostProfile(usd=0.2, tokens=20, latency_s=1.0),
            ),
            case_execution(
                workflow,
                evaluation_case("second"),
                cost=CostProfile(usd=0.3, tokens=30, latency_s=3.0),
            ),
        )

        utility, basis = derive_observed_utility(
            candidate,
            executions,
            front_cost_unit=CostUnit.USD,
            latency_available=True,
        )

        assert utility.get(Objective.VALIDITY) == 1.0
        assert utility.get(Objective.EVIDENCE) == 1.0
        assert utility.get(Objective.COST) == pytest.approx(0.5)
        assert utility.get(Objective.LATENCY) == pytest.approx(2.0)
        assert utility.get(Objective.RISK) == candidate.static_estimate.vector.get(
            Objective.RISK
        )
        assert Objective.ROBUSTNESS in utility.unavailable
        assert Objective.SCIENTIFIC_UTILITY in utility.unavailable
        assert set(basis) == {"validity", "evidence", "cost", "latency", "risk"}

    def test_cost_and_latency_are_front_wide_unavailable(self) -> None:
        _, _, _, _, workflow, candidate = objective_fixture()
        execution = case_execution(workflow, evaluation_case())

        utility, basis = derive_observed_utility(
            candidate,
            (execution,),
            front_cost_unit=None,
            latency_available=False,
        )

        assert Objective.COST in utility.unavailable
        assert Objective.LATENCY in utility.unavailable
        assert "unavailable" in basis["cost"]
        assert "unavailable" in basis["latency"]

    def test_front_dominance_requires_identical_measured_masks(self) -> None:
        _, _, _, _, _, candidate = objective_fixture()
        dominant = UtilityVector.of(validity=1.0, evidence=1.0, cost=1.0)
        dominated = UtilityVector.of(validity=1.0, evidence=1.0, cost=2.0)
        different_mask = UtilityVector.of(validity=0.5, evidence=0.5)
        evaluations = (
            self._evaluation(candidate_as(candidate, "a"), (), dominant),
            self._evaluation(candidate_as(candidate, "b"), (), dominated),
            self._evaluation(candidate_as(candidate, "c"), (), different_mask),
        )

        assert compatible_observed_front(evaluations) == ("a", "c")


class TestOptimizationLoopTransitions:
    async def test_initialization_stop_precedence_is_deterministic(self) -> None:
        ctx, compilation = compilation_archive(("a",))
        harness = RecordingHarness()
        no_preferences = ctx.model_copy(
            update={
                "dossier": ctx.dossier.model_copy(update={"preferences": []})
            }
        )
        ineligible = ctx.model_copy(
            update={
                "dossier": ctx.dossier.model_copy(
                    update={
                        "evaluators": {
                            CheckLevel.PREFERENCE: EvaluatorAvailability.UNAVAILABLE
                        }
                    }
                )
            }
        )

        no_cases = await OptimizationLoop(no_preferences).run(
            compilation, (), harness
        )
        no_prefs = await OptimizationLoop(no_preferences).run(
            compilation, (evaluation_case(),), harness
        )
        bad_evaluator = await OptimizationLoop(ineligible).run(
            compilation, (evaluation_case(),), harness
        )

        assert no_cases.stop_reason is OptimizationStopReason.NO_CASES
        assert no_prefs.stop_reason is OptimizationStopReason.NO_PREFERENCES
        assert bad_evaluator.stop_reason is OptimizationStopReason.INELIGIBLE_EVALUATOR
        assert harness.calls == []

    async def test_empty_compiler_archive_stops_without_execution(self) -> None:
        ctx, _ = compilation_archive(("a",))
        harness = RecordingHarness()

        outcome = await OptimizationLoop(ctx).run(
            CompilationResult(rejected={"x": "not executable"}),
            (evaluation_case(),),
            harness,
        )

        assert outcome.stop_reason is OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES
        assert outcome.compiler_rejections == {"x": "not executable"}
        assert harness.calls == []

    async def test_default_epsilon_is_frozen_for_every_dossier_preference(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))

        outcome = await OptimizationLoop(ctx).run(
            compilation,
            (evaluation_case(),),
            RecordingHarness(),
        )

        assert outcome.policy.epsilon == {"clarity": 0.0}

    @pytest.mark.parametrize(
        "configured",
        [
            {"other": 0.0},
            {"clarity": 0.0, "other": 0.0},
        ],
    )
    async def test_epsilon_keys_cannot_mismatch_dossier_preferences(
        self, configured: dict[str, float]
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        configured_policy = policy().model_copy(
            update={"epsilon": configured}
        )
        harness = RecordingHarness()

        with pytest.raises(
            ValueError,
            match="epsilon keys must exactly match dossier preference IDs",
        ):
            await OptimizationLoop(ctx, policy=configured_policy).run(
                compilation,
                (evaluation_case(),),
                harness,
            )

        assert harness.calls == []

    async def test_outcome_rejects_missing_packets_and_impossible_selection(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation,
            (evaluation_case(),),
            RecordingHarness(),
        )
        assert outcome.stop_reason is OptimizationStopReason.NO_JUDGES

        without_packets = outcome.model_dump()
        without_packets["packet_archive"] = []
        with pytest.raises(ValidationError, match="unknown packet"):
            OptimizationOutcome.model_validate(without_packets)

        impossible_selection = outcome.model_dump()
        impossible_selection["selected_candidate_id"] = "a"
        with pytest.raises(ValidationError, match="stop reason"):
            OptimizationOutcome.model_validate(impossible_selection)

    async def test_outcome_rejects_archive_records_outside_frozen_cases(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        verbosity = VerbosityPolicy(
            mode=VerbosityMode.AUTO_LOSS,
            baseline_candidate_id="a",
            sigma=1.1,
        )
        configured = policy().model_copy(update={"verbosity": verbosity})
        outcome = await OptimizationLoop(ctx, policy=configured).run(
            compilation,
            (evaluation_case(),),
            RecordingHarness(
                payloads={"a": {"text": "short"}, "b": {"text": "long " * 50}}
            ),
        )
        observation = outcome.archive.policy_observations[0].model_copy(
            update={"case_id": "unknown-case"}
        )
        invalid = outcome.model_dump()
        invalid["archive"]["policy_observations"] = [observation.model_dump()]

        with pytest.raises(ValidationError, match="unknown case"):
            OptimizationOutcome.model_validate(invalid)

    async def test_incomplete_compiler_archive_cannot_select_a_biased_subset(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        compilation = compilation.model_copy(
            deep=True,
            update={"estimates": {"a": compilation.estimates["a"]}},
        )
        harness = RecordingHarness()

        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation, (evaluation_case(),), harness
        )

        assert outcome.stop_reason is OptimizationStopReason.NO_ADMISSIBLE_CANDIDATES
        assert outcome.selected_candidate_id is None
        assert outcome.compiler_rejections["b"] == (
            "compiler archive missing static estimate"
        )
        assert harness.calls == []

    @pytest.mark.parametrize(
        "budget",
        [
            OptimizationBudget(
                max_candidate_executions=1,
                max_candidates=2,
                max_judge_calls=40,
            ),
            OptimizationBudget(
                max_candidate_executions=2,
                max_candidates=1,
                max_judge_calls=40,
            ),
        ],
    )
    async def test_initial_archive_is_reserved_as_a_complete_matched_set(
        self, budget: OptimizationBudget
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        harness = RecordingHarness()
        constrained = policy().model_copy(update={"budget": budget})

        outcome = await OptimizationLoop(ctx, policy=constrained).run(
            compilation, (evaluation_case(),), harness
        )

        assert outcome.stop_reason is OptimizationStopReason.BUDGET_EXHAUSTED
        assert outcome.selected_candidate_id is None
        assert harness.calls == []

    async def test_objective_singleton_needs_no_panel(self) -> None:
        ctx, compilation = compilation_archive(("a",))
        harness = RecordingHarness()

        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation, (evaluation_case(),), harness
        )

        assert outcome.stop_reason is OptimizationStopReason.OBJECTIVE_SINGLETON
        assert outcome.selected_candidate_id == "a"
        assert outcome.objective_front == ("a",)
        assert [event.state for event in outcome.events] == [
            OptimizationState.INITIALIZING,
            OptimizationState.EXECUTING,
            OptimizationState.OBJECTIVE_GATING,
            OptimizationState.STOPPED,
        ]

    async def test_objectively_dominated_candidate_never_reaches_judges(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        harness = RecordingHarness(
            costs={
                "a": CostProfile(usd=0.1, tokens=10, latency_s=0.5),
                "b": CostProfile(usd=0.2, tokens=10, latency_s=0.5),
            }
        )
        first = ContentJudge("family-1", preferred_text="b")
        second = ContentJudge("family-2", preferred_text="b")

        outcome = await OptimizationLoop(
            ctx,
            panel=JudgePanel((first, second)),
            policy=policy(),
        ).run(compilation, (evaluation_case(),), harness)

        assert outcome.stop_reason is OptimizationStopReason.OBJECTIVE_SINGLETON
        assert outcome.selected_candidate_id == "a"
        assert outcome.objective_front == ("a",)
        assert first.requests == second.requests == []

    async def test_multi_candidate_front_distinguishes_missing_and_thin_panels(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        cases = (evaluation_case(),)

        missing = await OptimizationLoop(ctx, policy=policy()).run(
            compilation, cases, RecordingHarness()
        )
        only = ContentJudge("only-family", preferred_text="a")
        thin = await OptimizationLoop(
            ctx,
            panel=JudgePanel((only,)),
            policy=policy(),
        ).run(compilation, cases, RecordingHarness())

        assert missing.stop_reason is OptimizationStopReason.NO_JUDGES
        assert thin.stop_reason is OptimizationStopReason.INSUFFICIENT_JUDGE_DIVERSITY
        assert only.requests == []

    async def test_two_agreeing_families_select_after_active_case_acquisition(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        cases = tuple(evaluation_case(f"case-{index}") for index in range(3))
        first = ContentJudge("family-1", preferred_text="good")
        second = ContentJudge("family-2", preferred_text="good")
        harness = RecordingHarness(
            payloads={"a": {"text": "good"}, "b": {"text": "bad"}}
        )

        outcome = await OptimizationLoop(
            ctx,
            panel=JudgePanel((first, second)),
            policy=policy(),
        ).run(compilation, cases, harness)

        assert outcome.stop_reason is OptimizationStopReason.CONFIDENT_PREFERENCE
        assert outcome.selected_candidate_id == "a"
        assert outcome.preference_front == ("a",)
        assert outcome.ledger.judge_calls == 12
        assert len(outcome.archive.attempts) == 3
        assert len(outcome.model_snapshots) == 3

    async def test_order_swap_neutralizes_always_left_judges(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        first = ContentJudge("family-1", always_left=True)
        second = ContentJudge("family-2", always_left=True)

        outcome = await OptimizationLoop(
            ctx,
            panel=JudgePanel((first, second)),
            policy=policy(),
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.selected_candidate_id is None
        assert outcome.stop_reason is OptimizationStopReason.EVALUATOR_UNSTABLE
        assert all(
            observation.preferred_candidate_id is None
            for observation in outcome.archive.observations
        )

    async def test_judge_exception_without_authoritative_cost_invalidates_accounting(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        panel = JudgePanel(
            (
                ContentJudge("family-1", unavailable=True),
                ContentJudge("family-2", unavailable=True),
            )
        )

        outcome = await OptimizationLoop(
            ctx, panel=panel, policy=policy()
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.selected_candidate_id is None
        assert outcome.ledger.cost_accounting_complete is False

    async def test_panel_level_exception_cannot_claim_complete_accounting(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))

        outcome = await OptimizationLoop(
            ctx,
            panel=ExplodingPanel(),  # type: ignore[arg-type]
            policy=policy(),
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.ledger.cost_accounting_complete is False

    async def test_known_partial_judge_cost_is_retained_when_accounting_fails(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        panel = JudgePanel(
            (
                ContentJudge(
                    "family-1",
                    preferred_text="a",
                    cost=CostProfile(usd=0.1, tokens=2),
                ),
                ContentJudge("family-2", unavailable=True),
            )
        )

        outcome = await OptimizationLoop(
            ctx, panel=panel, policy=policy()
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.ledger.cost_accounting_complete is False
        assert outcome.ledger.judge_cost.usd == pytest.approx(0.2)
        assert outcome.ledger.judge_cost.tokens == 4

    async def test_judge_budget_reserves_complete_order_swapped_families(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        first = ContentJudge("family-1", preferred_text="a")
        second = ContentJudge("family-2", preferred_text="a")
        constrained = policy().model_copy(
            update={
                "budget": policy().budget.model_copy(
                    update={"max_judge_calls": 3}
                )
            }
        )

        outcome = await OptimizationLoop(
            ctx,
            panel=JudgePanel((first, second)),
            policy=constrained,
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.BUDGET_EXHAUSTED
        assert outcome.selected_candidate_id is None
        assert first.requests == second.requests == []

    @pytest.mark.parametrize(
        ("field", "limit", "cost"),
        [
            ("soft_max_usd", 0.05, CostProfile(usd=0.1)),
            ("soft_max_tokens", 5, CostProfile(tokens=10)),
            (
                "soft_max_execution_latency_s",
                0.25,
                CostProfile(latency_s=0.5),
            ),
        ],
    )
    async def test_soft_limits_allow_one_observed_unit_then_stop(
        self, field: str, limit: float | int, cost: CostProfile
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        budget = policy().budget.model_copy(update={field: limit})
        constrained = policy().model_copy(update={"budget": budget})

        outcome = await OptimizationLoop(ctx, policy=constrained).run(
            compilation,
            (evaluation_case(),),
            RecordingHarness(costs={"a": cost}),
        )

        assert outcome.stop_reason is OptimizationStopReason.BUDGET_EXHAUSTED
        assert outcome.selected_candidate_id is None
        assert outcome.ledger.soft_limits_exceeded == (field,)
        assert outcome.ledger.candidate_executions == 1

    async def test_invalid_execution_accounting_has_precedence_over_selection(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        invalid = CostProfile.model_construct(usd=math.nan)

        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation,
            (evaluation_case(),),
            RecordingHarness(costs={"a": invalid}),
        )

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.selected_candidate_id is None
        assert outcome.ledger.cost_accounting_complete is False

    async def test_finite_execution_costs_that_overflow_stop_with_typed_reason(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        cases = (evaluation_case("first"), evaluation_case("second"))

        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation,
            cases,
            RecordingHarness(costs={"a": CostProfile(usd=1e308)}),
        )

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.ledger.cost_accounting_complete is False
        assert math.isfinite(outcome.ledger.execution_cost.usd)

    async def test_verbosity_baseline_must_remain_on_the_objective_front(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        verbosity = VerbosityPolicy(
            mode=VerbosityMode.AUTO_LOSS,
            baseline_candidate_id="not-on-front",
            sigma=1.1,
        )
        configured = policy().model_copy(update={"verbosity": verbosity})

        outcome = await OptimizationLoop(ctx, policy=configured).run(
            compilation, (evaluation_case(),), RecordingHarness()
        )

        assert outcome.stop_reason is OptimizationStopReason.VERBOSITY_BASELINE_UNAVAILABLE
        assert outcome.selected_candidate_id is None

    async def test_verbosity_auto_loss_can_form_a_singleton_without_judges(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        verbosity = VerbosityPolicy(
            mode=VerbosityMode.AUTO_LOSS,
            baseline_candidate_id="a",
            sigma=1.1,
        )
        configured = policy().model_copy(update={"verbosity": verbosity})
        harness = RecordingHarness(
            payloads={
                "a": {"text": "short"},
                "b": {"text": "long " * 50},
            }
        )

        outcome = await OptimizationLoop(ctx, policy=configured).run(
            compilation, (evaluation_case(),), harness
        )

        assert outcome.stop_reason is OptimizationStopReason.VERBOSITY_POLICY_SINGLETON
        assert outcome.selected_candidate_id == "a"
        assert len(outcome.archive.policy_observations) == 1

    async def test_objective_singleton_expands_one_unseen_child_before_stopping(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        harness = RecordingHarness(
            costs={
                "a": CostProfile(usd=0.1),
                "child": CostProfile(usd=0.2),
            }
        )

        outcome = await OptimizationLoop(
            ctx,
            policy=policy(),
            mutation_source=authorized_mutation_source(ctx, compilation),
        ).run(compilation, (evaluation_case(),), harness)

        assert outcome.stop_reason is OptimizationStopReason.OBJECTIVE_SINGLETON
        assert outcome.selected_candidate_id == "a"
        assert outcome.ledger.candidate_executions == 2
        assert len(outcome.mutation_records) == 1
        assert any(event.state is OptimizationState.MUTATING for event in outcome.events)

    async def test_injected_mutation_source_cannot_forge_authorization(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))

        outcome = await OptimizationLoop(
            ctx,
            policy=policy(),
            mutation_source=ForgedMutationSource(),
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.MUTATION_SOURCE_INVALID
        assert outcome.selected_candidate_id is None
        assert outcome.ledger.candidate_executions == 1
        assert outcome.mutation_records == ()
        assert any("authorization" in note for note in outcome.notes)

    async def test_mixed_valid_and_forged_mutation_batch_fails_closed(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        source = MixedMutationSource(
            authorized_mutation_source(ctx, compilation)
        )

        outcome = await OptimizationLoop(
            ctx,
            policy=policy(),
            mutation_source=source,
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.MUTATION_SOURCE_INVALID
        assert outcome.ledger.candidate_executions == 1
        assert outcome.mutation_records == ()

    async def test_existing_candidate_id_does_not_skip_mutation_authorization(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a",))

        outcome = await OptimizationLoop(
            ctx,
            policy=policy(),
            mutation_source=ExistingIdForgedMutationSource(),
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.MUTATION_SOURCE_INVALID
        assert outcome.ledger.candidate_executions == 1
        assert outcome.mutation_records == ()

    @pytest.mark.parametrize(
        "source",
        [BrokenMutationSource(), MalformedMutationSource()],
    )
    async def test_mutation_source_failure_cannot_claim_terminal_confidence(
        self, source: object
    ) -> None:
        ctx, compilation = compilation_archive(("a",))

        outcome = await OptimizationLoop(
            ctx,
            policy=policy(),
            mutation_source=source,  # type: ignore[arg-type]
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.MUTATION_SOURCE_INVALID
        assert outcome.selected_candidate_id is None
        assert any(
            "mutation source" in note or "untyped proposal" in note
            for note in outcome.notes
        )

    @pytest.mark.parametrize(
        "budget_update",
        [
            {"max_candidates": 1},
            {"max_generations": 0},
            {"max_candidate_executions": 1},
        ],
    )
    async def test_unseen_child_blocked_by_any_hard_cap_is_not_a_success(
        self, budget_update: dict[str, int]
    ) -> None:
        ctx, compilation = compilation_archive(("a",))
        budget = policy().budget.model_copy(update=budget_update)
        constrained = policy().model_copy(update={"budget": budget})

        outcome = await OptimizationLoop(
            ctx,
            policy=constrained,
            mutation_source=authorized_mutation_source(ctx, compilation),
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.BUDGET_EXHAUSTED
        assert outcome.selected_candidate_id is None
        assert outcome.ledger.candidate_executions == 1

    async def test_empty_configured_neighborhood_has_unresolved_only_semantics(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        panel = JudgePanel(
            (ContentJudge("family-1", tie=True), ContentJudge("family-2", tie=True))
        )

        unresolved = await OptimizationLoop(
            ctx,
            panel=panel,
            policy=policy(),
            mutation_source=EmptyMutationSource(),
        ).run(compilation, (evaluation_case(),), RecordingHarness())
        singleton_ctx, singleton_compilation = compilation_archive(("a",))
        successful = await OptimizationLoop(
            singleton_ctx,
            policy=policy(),
            mutation_source=EmptyMutationSource(),
        ).run(singleton_compilation, (evaluation_case(),), RecordingHarness())

        assert unresolved.stop_reason is OptimizationStopReason.NO_MUTATIONS
        assert successful.stop_reason is OptimizationStopReason.OBJECTIVE_SINGLETON
        assert successful.selected_candidate_id == "a"

    async def test_valid_exhausted_tie_without_mutations_is_front_stable_unresolved(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        panel = JudgePanel(
            (ContentJudge("family-1", tie=True), ContentJudge("family-2", tie=True))
        )

        outcome = await OptimizationLoop(
            ctx, panel=panel, policy=policy()
        ).run(compilation, (evaluation_case(),), RecordingHarness())

        assert outcome.stop_reason is OptimizationStopReason.FRONT_STABLE_UNRESOLVED
        assert outcome.selected_candidate_id is None
        assert outcome.preference_front == ("a", "b")

    async def test_strict_majority_cycle_remains_unresolved(self) -> None:
        ctx, compilation = compilation_archive(("a", "b", "c"))
        cycle = {
            frozenset(("A", "B")): "A",
            frozenset(("B", "C")): "B",
            frozenset(("A", "C")): "C",
        }
        panel = JudgePanel(
            (
                ContentJudge("family-1", preferred_by_pair=cycle),
                ContentJudge("family-2", preferred_by_pair=cycle),
            )
        )
        harness = RecordingHarness(
            payloads={
                "a": {"text": "A"},
                "b": {"text": "B"},
                "c": {"text": "C"},
            }
        )

        outcome = await OptimizationLoop(
            ctx, panel=panel, policy=policy()
        ).run(compilation, (evaluation_case(),), harness)

        assert outcome.stop_reason is OptimizationStopReason.FRONT_STABLE_UNRESOLVED
        assert outcome.selected_candidate_id is None
        assert len(outcome.archive.attempts) == 3
        assert outcome.preference_front == ("a", "b", "c")

    async def test_confident_incumbent_expands_before_terminal_selection(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        cases = tuple(evaluation_case(f"case-{index}") for index in range(3))
        panel = JudgePanel(
            (
                ContentJudge("family-1", preferred_text="good"),
                ContentJudge("family-2", preferred_text="good"),
            )
        )
        harness = RecordingHarness(
            payloads={"a": {"text": "good"}, "b": {"text": "bad"}},
            costs={
                "a": CostProfile(usd=0.1),
                "b": CostProfile(usd=0.1),
                "child": CostProfile(usd=0.2),
            },
        )

        outcome = await OptimizationLoop(
            ctx,
            panel=panel,
            policy=policy(),
            mutation_source=authorized_mutation_source(ctx, compilation),
        ).run(compilation, cases, harness)

        assert outcome.stop_reason is OptimizationStopReason.CONFIDENT_PREFERENCE
        assert outcome.selected_candidate_id == "a"
        assert len(outcome.mutation_records) == 1
        assert outcome.ledger.candidate_executions == 9

    async def test_harness_exception_never_claims_zero_cost_accounting(self) -> None:
        ctx, compilation = compilation_archive(("a",))

        async def broken_harness(
            workflow: CompiledWorkflow, case: EvaluationCase
        ) -> CaseExecution:
            raise RuntimeError("transport failed before a cost record")

        outcome = await OptimizationLoop(ctx, policy=policy()).run(
            compilation, (evaluation_case(),), broken_harness
        )

        assert outcome.stop_reason is OptimizationStopReason.RESOURCE_ACCOUNTING_INVALID
        assert outcome.ledger.cost_accounting_complete is False
        assert outcome.ledger.candidate_executions == 1

    async def test_judge_soft_cost_overshoot_starts_no_second_target(self) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        cases = (evaluation_case("first"), evaluation_case("second"))
        budget = policy().budget.model_copy(update={"soft_max_usd": 0.21})
        configured = policy().model_copy(update={"budget": budget})
        panel = JudgePanel(
            (
                ContentJudge("family-1", preferred_text="a"),
                ContentJudge("family-2", preferred_text="a"),
            )
        )
        harness = RecordingHarness(
            costs={"a": CostProfile(usd=0.05), "b": CostProfile(usd=0.05)}
        )

        outcome = await OptimizationLoop(
            ctx, panel=panel, policy=configured
        ).run(compilation, cases, harness)

        assert outcome.stop_reason is OptimizationStopReason.BUDGET_EXHAUSTED
        assert outcome.ledger.soft_limits_exceeded == ("soft_max_usd",)
        assert outcome.ledger.judge_calls == 2
        assert len(outcome.archive.attempts) == 1

    async def test_repeated_runs_and_serialized_outcomes_are_deterministic(
        self,
    ) -> None:
        ctx, compilation = compilation_archive(("a", "b"))
        cases = tuple(evaluation_case(f"case-{index}") for index in range(3))

        async def run_once() -> OptimizationOutcome:
            panel = JudgePanel(
                (
                    ContentJudge("family-1", preferred_text="good"),
                    ContentJudge("family-2", preferred_text="good"),
                )
            )
            return await OptimizationLoop(ctx, panel=panel, policy=policy()).run(
                compilation,
                cases,
                RecordingHarness(
                    payloads={"a": {"text": "good"}, "b": {"text": "bad"}}
                ),
            )

        first = await run_once()
        second = await run_once()
        replayed = OptimizationOutcome.model_validate_json(first.model_dump_json())

        assert first == second == replayed
        assert replayed.algorithm_version == first.algorithm_version
