"""Systems under test: AgentCo-Op and the baselines it has to beat.

The baselines exist to close specific escape routes, and each one closes a
different one:

``BestSingleComponent``
    Closes "the workflow only won because it had access to the good tool."
    Give the single best component the whole task and see how far it gets.

``EqualBudgetSingleAgent``
    Closes "the workflow only won because it spent more." One capable
    generalist, one shot, the same budget.

``SameWorkflowSingleAgent``
    Closes the important one: "the *structure* is doing nothing; a single
    model walking the same steps would do just as well." It takes
    AgentCo-Op's own compiled graph and rebinds every node to one generalist.
    A win here that AgentCo-Op does not also get means the topology mattered;
    a tie means it did not, and that is a result worth reporting.

``CodingAgentBaseline``
    Closes "why not just use Codex or Claude Code?" — the question every agent
    paper has to answer. It is a real arm, not a straw man: the coding agent
    gets the same task and the same repositories.

A baseline that cannot run in the current environment reports
``available=False`` and is excluded from aggregates. It never reports a score
of zero, because "we could not run it" and "it ran and failed" are different
findings and averaging them together would flatter whatever did run.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.bench.task import BenchEnvironment, BenchTask
from agentcoop.compile.compiler import Compiler
from agentcoop.diagnose.detectors import detect_all
from agentcoop.diagnose.diagnose import DiagnosisOutcome, Diagnoser
from agentcoop.diagnose.localize import localize
from agentcoop.execute.engine import ExecutionEngine, ExecutionResult
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.capability import CapabilityCard, ComponentKind, CostProfile
from agentcoop.ir.checks import CheckLevel, CheckReport, CheckResult, CheckStatus
from agentcoop.ir.dossier import Subgoal
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, WorkflowTerm, atomics
from agentcoop.repair.loop import RepairLoop
from agentcoop.repair.shadow import ShadowValidator


class SystemOutput(BaseModel):
    """One system's attempt at one task."""

    model_config = ConfigDict(extra="forbid")

    system: str
    task_id: str
    #: False when the arm could not be run here at all. Never scored as zero.
    available: bool = True
    unavailable_reason: str = ""
    outputs: dict[str, Artifact] = Field(default_factory=dict)
    report: CheckReport = Field(default_factory=CheckReport)
    cost: CostProfile = Field(default_factory=CostProfile)
    #: Distinct components actually invoked, in sorted order.
    components_used: list[str] = Field(default_factory=list)
    #: Human-readable account of the structure the system chose.
    structure: str = ""
    #: Set when the system deliberately built a single-component workflow. In
    #: the ``multi_harmful`` regime this is the correct answer.
    declined_to_compose: bool = False
    diagnosis: Optional[DiagnosisOutcome] = None
    repaired: bool = False
    notes: list[str] = Field(default_factory=list)

    @property
    def n_components(self) -> int:
        return len(self.components_used)


@runtime_checkable
class SystemUnderTest(Protocol):
    name: str

    async def solve(self, env: BenchEnvironment) -> SystemOutput: ...


def unavailable(name: str, task: BenchTask, reason: str) -> SystemOutput:
    return SystemOutput(
        system=name, task_id=task.task_id, available=False, unavailable_reason=reason
    )


# ---------------------------------------------------------------------------
# Shared execution
# ---------------------------------------------------------------------------


def _engine(env: BenchEnvironment) -> ExecutionEngine:
    return ExecutionEngine(
        env.adapters, type_registry=env.types, library=env.library
    )


def _used_from_workflow(
    workflow: CompiledWorkflow, execution: ExecutionResult
) -> list[str]:
    by_node = {a.term_id or f"{a.subgoal_id}__{a.component}": a.component for a in atomics(workflow.term)}
    used = {
        by_node[node_id]
        for node_id in execution.state.executed
        if node_id in by_node
    }
    return sorted(used)


async def _execute(
    env: BenchEnvironment, workflow: CompiledWorkflow, *, run_id: str
) -> ExecutionResult:
    return await _engine(env).run(
        workflow,
        env.task.dossier,
        env.task.inputs,
        limits=env.task.dossier.limits,
        run_id=run_id,
    )


def _single_node_workflow(
    task: BenchTask, component: str, subgoal_id: str, *, workflow_id: str
) -> CompiledWorkflow:
    return CompiledWorkflow(
        workflow_id=workflow_id,
        task_id=task.task_id,
        term=Atomic(component=component, subgoal_id=subgoal_id).ensure_ids(),
    )


def _terminal_subgoal(task: BenchTask) -> Optional[Subgoal]:
    """The subgoal that produces a required output, or the last one declared."""
    for subgoal in reversed(task.dossier.subgoals):
        if set(subgoal.produces) & set(task.dossier.required_outputs):
            return subgoal
    return task.dossier.subgoals[-1] if task.dossier.subgoals else None


def _coverage(card: CapabilityCard, task: BenchTask) -> int:
    """How many subgoals this one component could serve on its declarations."""
    served = 0
    for subgoal in task.dossier.subgoals:
        if subgoal.required_capability not in card.functional_capabilities:
            continue
        if all(card.can_produce(t) for t in subgoal.produces):
            served += 1
    return served


# ---------------------------------------------------------------------------
# AgentCo-Op
# ---------------------------------------------------------------------------


class AgentCoOpSystem:
    """Compile, execute, and — only when the evidence warrants — repair."""

    name = "agentcoop"

    def __init__(
        self,
        *,
        compiler: Optional[Compiler] = None,
        repair: bool = True,
        max_repair_attempts: int = 3,
    ) -> None:
        self.compiler = compiler
        self.repair = repair
        self.max_repair_attempts = max_repair_attempts

    async def solve(self, env: BenchEnvironment) -> SystemOutput:
        task = env.task
        compiler = self.compiler or Compiler(types=env.types)
        compilation = compiler.compile(task.dossier, env.library, workflow_id=task.task_id)

        if compilation.workflow is None:
            return SystemOutput(
                system=self.name,
                task_id=task.task_id,
                report=_refusal_report(task, compilation),
                structure="declined to compile",
                notes=[compilation.explain()],
            )

        workflow = compilation.workflow
        execution = await _execute(env, workflow, run_id=f"{task.task_id}::{self.name}")
        output = SystemOutput(
            system=self.name,
            task_id=task.task_id,
            outputs=dict(execution.outputs),
            report=execution.report,
            cost=execution.cost,
            components_used=_used_from_workflow(workflow, execution),
            structure=workflow.structural_key(),
            declined_to_compose=workflow.n_distinct_components <= 1,
            notes=list(compilation.notes),
        )

        signals = detect_all(execution.trace, execution.report, workflow, task.dossier)
        if not signals.blocking():
            return output

        localization = localize(
            execution.trace, signals, workflow, task.dossier, env.types
        )
        ctx = compiler.context(task.dossier, env.library)
        output.diagnosis = Diagnoser().diagnose(
            signals, localization, workflow, task.dossier, env.library
        )

        if not self.repair:
            return output

        async def harness(candidate: CompiledWorkflow) -> ExecutionResult:
            return await _execute(env, candidate, run_id=f"{task.task_id}::shadow")

        loop = RepairLoop(ctx, env.library, validator=ShadowValidator(env.library))
        repair_outcome = await loop.run(
            workflow, execution, harness, max_attempts=self.max_repair_attempts
        )
        output.notes.extend(repair_outcome.notes)
        if not repair_outcome.repaired:
            return output

        repaired_execution = await _execute(
            env, repair_outcome.workflow, run_id=f"{task.task_id}::repaired"
        )
        return output.model_copy(
            update={
                "outputs": dict(repaired_execution.outputs),
                "report": repaired_execution.report,
                "cost": output.cost + repaired_execution.cost,
                "components_used": _used_from_workflow(
                    repair_outcome.workflow, repaired_execution
                ),
                "structure": repair_outcome.workflow.structural_key(),
                "declined_to_compose": repair_outcome.workflow.n_distinct_components <= 1,
                "repaired": True,
            }
        )


def _refusal_report(task: BenchTask, compilation: Any) -> CheckReport:
    """A refusal to compile is a reportable verdict, not a crash."""
    report = CheckReport()
    report.add(
        CheckResult(
            check_id="compile.no_workflow",
            level=CheckLevel.HARD,
            status=CheckStatus.FAIL,
            summary=(
                "no admissible workflow could be compiled: "
                + "; ".join(
                    list(compilation.dossier_defects)
                    or list(compilation.rejected.values())[:2]
                    or ["no candidate survived selection"]
                )
            ),
            subject=task.task_id,
            subject_kind="workflow",
            blocking=True,
        )
    )
    return report


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


class BestSingleComponent:
    """The strongest single component in the library, given the whole task.

    'Strongest' is decided by declared coverage first and earned certification
    second — deliberately generous, since the point of the arm is to make the
    single-component ceiling as high as it honestly goes.
    """

    name = "best_single_component"

    async def solve(self, env: BenchEnvironment) -> SystemOutput:
        task = env.task
        cards = [env.library.require(n) for n in env.library.names()]
        if not cards:
            return unavailable(self.name, task, "the task declares no components")

        best = max(
            cards,
            key=lambda c: (_coverage(c, task), int(c.certification_level), c.name),
        )
        subgoal = _terminal_subgoal(task)
        if subgoal is None:
            return unavailable(self.name, task, "the dossier declares no subgoals")

        workflow = _single_node_workflow(
            task, best.name, subgoal.subgoal_id, workflow_id=f"{task.task_id}::single"
        )
        execution = await _execute(env, workflow, run_id=f"{task.task_id}::{self.name}")
        return SystemOutput(
            system=self.name,
            task_id=task.task_id,
            outputs=dict(execution.outputs),
            report=execution.report,
            cost=execution.cost,
            components_used=_used_from_workflow(workflow, execution),
            structure=workflow.structural_key(),
            declined_to_compose=True,
            notes=[
                f"chose '{best.name}' (covers {_coverage(best, task)}/"
                f"{len(task.dossier.subgoals)} subgoals, "
                f"{best.certification_level.name})"
            ],
        )


class EqualBudgetSingleAgent:
    """One generalist, one invocation, the same budget as the workflow.

    Bound to the terminal subgoal rather than to a synthetic 'whole task'
    pseudo-subgoal, so it is asked for the final answer directly — which is
    what a single agent is actually given.
    """

    name = "equal_budget_single_agent"

    def __init__(self, generalist: Optional[str] = None) -> None:
        self.generalist = generalist

    async def solve(self, env: BenchEnvironment) -> SystemOutput:
        task = env.task
        component = self.generalist or _find_generalist(env)
        if component is None:
            return unavailable(
                self.name,
                task,
                "no generalist component is declared for this task, so the "
                "single-agent arm has nothing to run",
            )
        subgoal = _terminal_subgoal(task)
        if subgoal is None:
            return unavailable(self.name, task, "the dossier declares no subgoals")

        workflow = _single_node_workflow(
            task, component, subgoal.subgoal_id, workflow_id=f"{task.task_id}::oneshot"
        )
        execution = await _execute(env, workflow, run_id=f"{task.task_id}::{self.name}")
        return SystemOutput(
            system=self.name,
            task_id=task.task_id,
            outputs=dict(execution.outputs),
            report=execution.report,
            cost=execution.cost,
            components_used=_used_from_workflow(workflow, execution),
            structure=workflow.structural_key(),
            declined_to_compose=True,
            notes=[f"single invocation of '{component}' on '{subgoal.subgoal_id}'"],
        )


class SameWorkflowSingleAgent:
    """AgentCo-Op's graph, every node executed by the same generalist.

    The counterfactual that isolates *structure* from *heterogeneity*. If this
    arm matches AgentCo-Op, then the compiled topology carried the result and
    the component diversity did not — or the reverse. Either way the paper
    has to say which, and this is the only arm that can tell it.
    """

    name = "same_workflow_single_agent"

    def __init__(
        self, generalist: Optional[str] = None, *, compiler: Optional[Compiler] = None
    ) -> None:
        self.generalist = generalist
        self.compiler = compiler

    async def solve(self, env: BenchEnvironment) -> SystemOutput:
        task = env.task
        component = self.generalist or _find_generalist(env)
        if component is None:
            return unavailable(
                self.name,
                task,
                "no generalist component is declared, so the same-workflow arm "
                "cannot be constructed",
            )

        compiler = self.compiler or Compiler(types=env.types)
        compilation = compiler.compile(task.dossier, env.library, workflow_id=task.task_id)
        if compilation.workflow is None:
            return unavailable(
                self.name,
                task,
                "AgentCo-Op produced no workflow for this task, so there is no "
                "structure to replay with a single agent",
            )

        rebound = compilation.workflow.with_term(
            _rebind(compilation.workflow.term, component),
            note=f"rebound every node to '{component}' (OneFlow counterfactual)",
        )
        execution = await _execute(env, rebound, run_id=f"{task.task_id}::{self.name}")
        return SystemOutput(
            system=self.name,
            task_id=task.task_id,
            outputs=dict(execution.outputs),
            report=execution.report,
            cost=execution.cost,
            components_used=_used_from_workflow(rebound, execution),
            structure=rebound.structural_key(),
            declined_to_compose=False,
            notes=[
                f"replayed {rebound.n_components}-node structure "
                f"{compilation.workflow.structural_key()} with one component"
            ],
        )


class CodingAgentBaseline:
    """A general coding agent given the same task and the same repositories.

    Reports ``available=False`` unless a ``coding_agent`` component is present
    in the task's library *and* an adapter is registered for it. Running this
    arm needs a live agent and network access, which is exactly the sort of
    thing that quietly turns into a fabricated number if the harness is
    allowed to fall back to something else.
    """

    name = "coding_agent"

    def __init__(self, component: Optional[str] = None) -> None:
        self.component = component

    async def solve(self, env: BenchEnvironment) -> SystemOutput:
        task = env.task
        component = self.component or _find_by_kind(env, ComponentKind.CODING_AGENT)
        if component is None:
            return unavailable(
                self.name,
                task,
                "no coding-agent component is declared for this task",
            )
        if not env.adapters.has(component):
            return unavailable(
                self.name,
                task,
                f"'{component}' is declared but no adapter is registered; running "
                "this arm requires a live coding agent",
            )

        subgoal = _terminal_subgoal(task)
        if subgoal is None:
            return unavailable(self.name, task, "the dossier declares no subgoals")

        workflow = _single_node_workflow(
            task, component, subgoal.subgoal_id, workflow_id=f"{task.task_id}::coding_agent"
        )
        execution = await _execute(env, workflow, run_id=f"{task.task_id}::{self.name}")
        return SystemOutput(
            system=self.name,
            task_id=task.task_id,
            outputs=dict(execution.outputs),
            report=execution.report,
            cost=execution.cost,
            components_used=_used_from_workflow(workflow, execution),
            structure=workflow.structural_key(),
            declined_to_compose=True,
            notes=[f"coding agent '{component}' given the whole task"],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rebind(term: WorkflowTerm, component: str) -> WorkflowTerm:
    """Replace every atomic's component while preserving the structure exactly."""
    if isinstance(term, Atomic):
        return term.model_copy(update={"component": component, "term_id": ""}).ensure_ids()
    updates: dict[str, Any] = {}
    for field in ("children_terms", "branches"):
        children = getattr(term, field, None)
        if isinstance(children, list) and children:
            updates[field] = [_rebind(child, component) for child in children]
    for field in ("body", "verifier", "primary", "alternate"):
        child = getattr(term, field, None)
        if child is not None and hasattr(child, "kind"):
            updates[field] = _rebind(child, component)
    return term.model_copy(update=updates) if updates else term


def _find_generalist(env: BenchEnvironment) -> Optional[str]:
    """The component that claims the most capabilities. Ties broken by name."""
    best: Optional[CapabilityCard] = None
    for name in env.library.names():
        card = env.library.require(name)
        if len(card.functional_capabilities) < 2:
            continue
        if best is None or len(card.functional_capabilities) > len(
            best.functional_capabilities
        ):
            best = card
    return best.name if best else None


def _find_by_kind(env: BenchEnvironment, kind: ComponentKind) -> Optional[str]:
    for name in env.library.names():
        if env.library.require(name).kind is kind:
            return name
    return None


def default_systems(*, repair: bool = True) -> list[SystemUnderTest]:
    """The standard comparison set.

    ``CodingAgentBaseline`` is included even though it will usually report
    itself unavailable — its absence from a results table should be visible
    rather than silent.
    """
    return [
        AgentCoOpSystem(repair=repair),
        BestSingleComponent(),
        EqualBudgetSingleAgent(),
        SameWorkflowSingleAgent(),
        CodingAgentBaseline(),
    ]


__all__ = [
    "SystemOutput",
    "SystemUnderTest",
    "AgentCoOpSystem",
    "BestSingleComponent",
    "EqualBudgetSingleAgent",
    "SameWorkflowSingleAgent",
    "CodingAgentBaseline",
    "default_systems",
    "unavailable",
]
