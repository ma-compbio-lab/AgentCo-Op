"""End-to-end ECPS: compile -> execute -> objective front -> preference search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agentcoop.bench.harness import BenchHarness
from agentcoop.bench.suites.synthetic import synthetic_suite, task_multi_necessary
from agentcoop.compile.compiler import CompilationResult, Compiler
from agentcoop.compile.grammar import RuleContext
from agentcoop.components.base import (
    AdapterRegistry,
    Invocation,
    InvocationResult,
    error_line,
    make_artifact,
    zero_clock,
)
from agentcoop.execute.engine import ExecutionEngine
from agentcoop.ir.artifacts import Artifact, ArtifactType, TypeRegistry
from agentcoop.ir.capability import (
    BehaviorContract,
    CapabilityCard,
    CertificationLevel,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    IOContract,
)
from agentcoop.ir.checks import CheckLevel, SignalSource
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    Preference,
    ResourceLimits,
    Subgoal,
    TaskEvidenceDossier,
)
from agentcoop.ir.faults import FaultClass
from agentcoop.ir.preference import (
    OrderedPairJudgment,
    PairRubric,
    PairwiseVerdict,
    PreferenceArchive,
    RubricCriterion,
    Scorepad,
    ScorepadEntry,
    VerbosityMode,
    VerbosityPolicy,
)
from agentcoop.optimize import (
    CaseExecution,
    CertifiedParameterDomain,
    ComponentPreferenceJudge,
    ConfigGridMutationSource,
    ConfigMutation,
    CostUnit,
    EvaluationCase,
    JudgePanel,
    JudgeRequest,
    JudgeResponse,
    OptimizationBudget,
    OptimizationHarness,
    OptimizationLoop,
    OptimizationOutcome,
    OptimizationPolicy,
    OptimizationStopReason,
)
from agentcoop.probe import ProbeRunner, parameter_domain_suite
from agentcoop.ir.workflow import CompiledWorkflow


REPORT = ArtifactType(
    name="report",
    json_schema={
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    },
)


class WriterAdapter:
    def __init__(
        self,
        name: str,
        text: str,
        *,
        parameterized: bool = False,
    ) -> None:
        self.name = name
        self.text = text
        self.parameterized = parameterized
        self.invocations: list[Invocation] = []

    async def invoke(self, invocation: Invocation) -> InvocationResult:
        self.invocations.append(invocation)
        if "__agentcoop_probe_unknown_parameter__" in invocation.config:
            return InvocationResult(
                ok=False,
                errors=[
                    error_line(FaultClass.CONFIGURATION, "unknown parameter")
                ],
                cost=CostProfile(),
            )
        temperature = float(invocation.config.get("temperature", 0.1))
        text = (
            f"temperature={temperature}"
            if self.parameterized
            else self.text
        )
        artifact = make_artifact(
            type_name="report",
            payload={"text": text},
            producer=str(invocation.config.get("node_id", self.name)),
        )
        return InvocationResult(
            ok=True,
            outputs={"report": artifact},
            cost=CostProfile(
                usd=temperature / 100.0 if self.parameterized else 0.01,
                tokens=10,
            ),
        )


class UnavailableJudgeAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def invoke(self, invocation: Invocation) -> InvocationResult:
        return InvocationResult(
            ok=False,
            errors=[error_line(FaultClass.ENVIRONMENT, "offline evaluator")],
            cost=CostProfile(),
        )


class OutputJudge:
    source = SignalSource.LLM_JUDGE

    def __init__(
        self,
        family: str,
        *,
        preferred_text: str | None = None,
        preferred_by_pair: Mapping[frozenset[str], str] | None = None,
        labels_by_usd: Mapping[float, str] | None = None,
    ) -> None:
        self.judge_id = f"judge::{family}"
        self.family = family
        self.preferred_text = preferred_text
        self.preferred_by_pair = dict(preferred_by_pair or {})
        self.labels_by_usd = dict(labels_by_usd or {})

    def _label(self, request: JudgeRequest, *, left: bool) -> str:
        view = request.left if left else request.right
        if self.labels_by_usd:
            return self.labels_by_usd[round(view.resources.usd, 6)]
        snapshot = view.outputs[0].snapshot
        if not isinstance(snapshot, dict):
            return str(snapshot)
        return str(snapshot.get("text", ""))

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        rubric = request.frozen_rubric or PairRubric(
            rubric_id=f"rubric::{self.family}",
            version=request.meta_rubric.version,
            meta_rubric=request.meta_rubric.ref(),
            criteria=tuple(
                RubricCriterion(
                    criterion_id=f"criterion::{criterion.preference_id}",
                    preference_id=criterion.preference_id,
                    description=(
                        f"Compare {criterion.preference_id} from packet evidence."
                    ),
                    direction=criterion.direction,
                )
                for criterion in request.meta_rubric.criteria
            ),
        )
        left_text = self._label(request, left=True)
        right_text = self._label(request, left=False)
        preferred = self.preferred_by_pair.get(
            frozenset((left_text, right_text)), self.preferred_text
        )
        assert preferred is not None
        verdict = (
            PairwiseVerdict.LEFT
            if left_text == preferred
            else PairwiseVerdict.RIGHT
        )
        preferred_view = request.left if verdict is PairwiseVerdict.LEFT else request.right
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
                scorepad=Scorepad(
                    entries=tuple(
                        ScorepadEntry(
                            preference_id=criterion.preference_id,
                            criterion_id=criterion.criterion_id,
                            verdict=verdict,
                            left_score=(
                                8.0 if verdict is PairwiseVerdict.LEFT else 6.0
                            ),
                            right_score=(
                                8.0 if verdict is PairwiseVerdict.RIGHT else 6.0
                            ),
                            evidence_refs=(
                                preferred_view.outputs[0].reference_id,
                            ),
                            confidence=0.8,
                            rationale="The preferred report is clearer.",
                        )
                        for criterion in rubric.criteria
                    )
                ),
            ),
            cost=CostProfile(tokens=1),
        )


@dataclass(frozen=True)
class SearchEnvironment:
    dossier: TaskEvidenceDossier
    types: TypeRegistry
    library: ComponentLibrary
    adapters: AdapterRegistry
    compilation: CompilationResult
    ctx: RuleContext
    inputs: Mapping[str, Artifact]


def dossier() -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="preference-integration",
        goal="Write the clearest useful report.",
        artifact_types=[REPORT],
        required_outputs=["report"],
        subgoals=[
            Subgoal(
                subgoal_id="write",
                description="write report",
                required_capability="write_report",
                produces=["report"],
            )
        ],
        preferences=[
            Preference(
                preference_id="clarity",
                description="Prefer the clearer useful report.",
                dimension="clarity",
            )
        ],
        evaluators={CheckLevel.PREFERENCE: EvaluatorAvailability.RUBRIC},
        limits=ResourceLimits(max_usd=10.0, max_tokens=10_000),
    )


def declared_card(name: str, *, parameterized: bool = False) -> CapabilityCard:
    return CapabilityCard(
        name=name,
        kind=ComponentKind.PYTHON_FUNCTION,
        functional_capabilities=["write_report"],
        io=IOContract(
            produces=[REPORT],
            parameters=(
                {"temperature": {"type": "number", "default": 0.1}}
                if parameterized
                else {}
            ),
        ),
        behavior=BehaviorContract(deterministic=True, idempotent=True),
        declared_cost=CostProfile(usd=0.01, tokens=10),
    )


async def search_environment(
    texts: Mapping[str, str],
) -> SearchEnvironment:
    task_dossier = dossier()
    types = TypeRegistry()
    types.register_type(REPORT)
    writer_adapters = tuple(
        WriterAdapter(name, text) for name, text in sorted(texts.items())
    )
    adapters = AdapterRegistry(writer_adapters)
    runner = ProbeRunner(adapters, registry=types, clock=zero_clock)
    library = ComponentLibrary()
    for name in sorted(texts):
        certified = await runner.certify(declared_card(name))
        assert certified.certification_level is CertificationLevel.CERTIFIED
        library.add(certified)
    compiler = Compiler(types=types)
    compilation = compiler.compile(task_dossier, library)
    assert len(compilation.workflows) == len(texts)
    return SearchEnvironment(
        dossier=task_dossier,
        types=types,
        library=library,
        adapters=adapters,
        compilation=compilation,
        ctx=compiler.context(task_dossier, library),
        inputs={},
    )


def cases(count: int) -> tuple[EvaluationCase, ...]:
    return tuple(
        EvaluationCase(
            case_id=f"case-{index}",
            input_fingerprint="input::none",
            seed=index + 11,
            limits=ResourceLimits(max_usd=1.0, max_tokens=1_000),
            evaluator_ids=("schema", "silent-empty"),
            tool_snapshot_id="tools::offline-v1",
        )
        for index in range(count)
    )


def policy(
    *, verbosity: VerbosityPolicy | None = None
) -> OptimizationPolicy:
    return OptimizationPolicy(
        budget=OptimizationBudget(
            max_candidate_executions=64,
            max_judge_calls=128,
            max_candidates=16,
            max_generations=2,
        ),
        epsilon={"clarity": 0.0},
        verbosity=verbosity or VerbosityPolicy(),
    )


def harness(environment: SearchEnvironment) -> OptimizationHarness:
    engine = ExecutionEngine(
        environment.adapters,
        type_registry=environment.types,
        library=environment.library,
    )

    async def run(
        workflow: CompiledWorkflow, case: EvaluationCase
    ) -> CaseExecution:
        execution = await engine.run(
            workflow,
            environment.dossier,
            environment.inputs,
            limits=case.limits,
            seed=case.seed,
            run_id=f"optimization::{workflow.workflow_id}::{case.case_id}",
        )
        return CaseExecution(
            case_fingerprint=case.fingerprint(),
            result=execution,
            cost_unit=CostUnit.USD,
            latency_measured=True,
        )

    return run


def candidate_for_component(
    compilation: CompilationResult, component: str
) -> str:
    return next(
        candidate_id
        for candidate_id, workflow in compilation.workflows.items()
        if workflow.components == [component]
    )


class TestPreferenceOptimizationIntegration:
    async def test_real_execution_selects_and_round_trips_without_changing_evidence(
        self,
    ) -> None:
        environment = await search_environment(
            {"writer-a": "preferred", "writer-b": "baseline"}
        )
        case_set = cases(4)
        evidence_before = {
            candidate_id: workflow.evidence.model_dump_json()
            for candidate_id, workflow in environment.compilation.workflows.items()
        }

        async def run_once() -> OptimizationOutcome:
            panel = JudgePanel(
                (
                    OutputJudge("family-1", preferred_text="preferred"),
                    OutputJudge("family-2", preferred_text="preferred"),
                )
            )
            return await OptimizationLoop(
                environment.ctx,
                panel=panel,
                policy=policy(),
            ).run(environment.compilation, case_set, harness(environment))

        first = await run_once()
        second = await run_once()
        selected = candidate_for_component(
            environment.compilation, "writer-a"
        )

        assert first.stop_reason is OptimizationStopReason.CONFIDENT_PREFERENCE
        assert first.selected_candidate_id == selected
        assert first == second
        assert first == OptimizationOutcome.model_validate_json(
            first.model_dump_json()
        )
        assert PreferenceArchive.model_validate_json(
            first.archive.model_dump_json()
        ) == first.archive
        assert {
            candidate_id: workflow.evidence.model_dump_json()
            for candidate_id, workflow in environment.compilation.workflows.items()
        } == evidence_before
        assert {
            evaluation.candidate.candidate_id:
            evaluation.candidate.workflow.evidence.model_dump_json()
            for evaluation in first.candidates
        } == evidence_before

    async def test_known_unavailable_judges_preserve_the_unresolved_front(
        self,
    ) -> None:
        environment = await search_environment(
            {"writer-a": "first", "writer-b": "second"}
        )
        registry = AdapterRegistry(
            (UnavailableJudgeAdapter("judge-a"), UnavailableJudgeAdapter("judge-b"))
        )
        panel = JudgePanel(
            (
                ComponentPreferenceJudge(
                    registry,
                    "judge-a",
                    judge_id="judge-a",
                    family="family-a",
                ),
                ComponentPreferenceJudge(
                    registry,
                    "judge-b",
                    judge_id="judge-b",
                    family="family-b",
                ),
            )
        )

        outcome = await OptimizationLoop(
            environment.ctx,
            panel=panel,
            policy=policy(),
        ).run(environment.compilation, cases(2), harness(environment))

        assert outcome.stop_reason is OptimizationStopReason.EVALUATOR_UNSTABLE
        assert outcome.selected_candidate_id is None
        assert set(outcome.preference_front) == set(environment.compilation.workflows)
        assert len(outcome.archive.attempts) == 2
        assert outcome.ledger.cost_accounting_complete is True

    async def test_real_three_candidate_cycle_is_unresolved(self) -> None:
        task = task_multi_necessary()
        task_dossier = task.dossier.model_copy(
            update={
                "preferences": [
                    Preference(
                        preference_id="clarity",
                        description="Prefer the clearest useful report.",
                        dimension="clarity",
                    )
                ],
                "evaluators": {
                    **task.dossier.evaluators,
                    CheckLevel.PREFERENCE: EvaluatorAvailability.RUBRIC,
                },
            }
        )
        task = task.model_copy(update={"dossier": task_dossier})
        prepared = await BenchHarness(
            synthetic_suite(), [], inject_faults=False
        ).prepare(task)
        compiler = Compiler(types=prepared.types, max_candidates=3)
        compilation = compiler.compile(task_dossier, prepared.library)
        assert len(compilation.workflows) == 3
        environment = SearchEnvironment(
            dossier=task_dossier,
            types=prepared.types,
            library=prepared.library,
            adapters=prepared.adapters,
            compilation=compilation,
            ctx=compiler.context(task_dossier, prepared.library),
            inputs=prepared.task.inputs,
        )
        cycle = {
            frozenset(("A", "B")): "A",
            frozenset(("B", "C")): "B",
            frozenset(("A", "C")): "C",
        }
        panel = JudgePanel(
            (
                OutputJudge(
                    "family-1",
                    preferred_by_pair=cycle,
                    labels_by_usd={0.09: "A", 0.08: "B", 0.055: "C"},
                ),
                OutputJudge(
                    "family-2",
                    preferred_by_pair=cycle,
                    labels_by_usd={0.09: "A", 0.08: "B", 0.055: "C"},
                ),
            )
        )

        outcome = await OptimizationLoop(
            environment.ctx,
            panel=panel,
            policy=policy(),
        ).run(environment.compilation, cases(3), harness(environment))

        assert outcome.stop_reason is OptimizationStopReason.FRONT_STABLE_UNRESOLVED
        assert outcome.selected_candidate_id is None
        assert set(outcome.preference_front) == set(environment.compilation.workflows)
        assert len(outcome.archive.attempts) == 9
        assert outcome.model_snapshots[-1].models[0].cycle_detected is True

    async def test_verbosity_duplication_auto_loses_before_judging(self) -> None:
        environment = await search_environment(
            {"writer-a": "answer", "writer-b": "answer " * 100}
        )
        baseline = candidate_for_component(environment.compilation, "writer-a")
        verbosity = VerbosityPolicy(
            mode=VerbosityMode.AUTO_LOSS,
            baseline_candidate_id=baseline,
            sigma=1.2,
        )

        outcome = await OptimizationLoop(
            environment.ctx,
            policy=policy(verbosity=verbosity),
        ).run(environment.compilation, cases(1), harness(environment))

        assert outcome.stop_reason is OptimizationStopReason.VERBOSITY_POLICY_SINGLETON
        assert outcome.selected_candidate_id == baseline
        assert len(outcome.archive.policy_observations) == 1
        assert outcome.archive.observations == ()


class TestCertifiedMutationIntegration:
    async def test_probe_evidence_authorizes_executes_and_round_trips_child(
        self,
    ) -> None:
        task_dossier = dossier()
        types = TypeRegistry()
        types.register_type(REPORT)
        adapter = WriterAdapter("writer", "unused", parameterized=True)
        adapters = AdapterRegistry((adapter,))
        runner = ProbeRunner(adapters, registry=types, clock=zero_clock)
        certified = await runner.certify(
            declared_card("writer", parameterized=True)
        )
        assert certified.certification_level is CertificationLevel.CERTIFIED
        specs = parameter_domain_suite(
            certified,
            parameter="temperature",
            values=(0.1, 0.2),
        )
        certified = await runner.certify(certified, specs)
        library = ComponentLibrary()
        library.add(certified)
        compiler = Compiler(types=types)
        compilation = compiler.compile(task_dossier, library)
        assert len(compilation.workflows) == 1
        parent_id = next(iter(compilation.workflows))
        target = compilation.workflows[parent_id].term.term_id
        domain = CertifiedParameterDomain(
            domain_id="temperature-domain",
            component="writer",
            key="temperature",
            values=(0.1, 0.2),
            probe_ids=tuple(spec.probe_id for spec in specs),
        )
        mutation = ConfigMutation(
            mutation_id="temperature-0.2",
            target=target,
            key="temperature",
            value=0.2,
            domain_id=domain.domain_id,
            rationale="execute the probe-certified alternative",
        )
        source = ConfigGridMutationSource(
            library,
            domains=(domain,),
            mutations=(mutation,),
        )
        environment = SearchEnvironment(
            dossier=task_dossier,
            types=types,
            library=library,
            adapters=adapters,
            compilation=compilation,
            ctx=compiler.context(task_dossier, library),
            inputs={},
        )

        outcome = await OptimizationLoop(
            environment.ctx,
            policy=policy(),
            mutation_source=source,
        ).run(compilation, cases(1), harness(environment))

        assert outcome.stop_reason is OptimizationStopReason.OBJECTIVE_SINGLETON
        assert outcome.selected_candidate_id == parent_id
        assert len(outcome.mutation_records) == 1
        record = outcome.mutation_records[0]
        assert record.probe_ids == tuple(spec.probe_id for spec in specs)
        child = next(
            evaluation
            for evaluation in outcome.candidates
            if evaluation.candidate.candidate_id == record.child_id
        )
        assert child.executions[0].result.outputs["report"].payload == {
            "text": "temperature=0.2"
        }
        assert child.candidate.workflow.evidence == (
            compilation.workflows[parent_id].evidence
        )
        assert outcome == OptimizationOutcome.model_validate_json(
            outcome.model_dump_json()
        )
