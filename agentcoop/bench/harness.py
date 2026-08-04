"""Running a suite, and scoring it without collapsing what cannot be collapsed.

Three scoring commitments, each of which rules out a way of reporting a result
that would be easier and wrong:

**Regime correctness is separate from task success.** A system that produces
the right answer by composing four components on a task where one would do has
succeeded at the task and failed at the decision. Both are reported. Averaging
them into one number would make "knows when not to compose" invisible, and
that capability is the entire negative claim of the method.

**Unavailable arms are excluded, not zeroed.** ``CodingAgentBaseline`` usually
cannot run offline. Scoring it zero would silently inflate every comparison
against it. Every aggregate therefore reports how many tasks it was computed
over.

**Blame accuracy is scored against the injected fault, not the diagnoser.**
The answer sheet is written when the task is authored.

Nothing in this module runs an experiment on import, and nothing writes to a
results directory. The harness is a library; the CLI decides when to use it.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.bench.baselines import SystemOutput, SystemUnderTest
from agentcoop.bench.faults import expected_fault_class, is_silent
from agentcoop.bench.task import (
    BenchEnvironment,
    BenchSuite,
    BenchTask,
    GroundTruth,
    build_environment,
)
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.checks import CheckLevel, CheckStatus


def blame_matches(predicted: Optional[str], expected: str) -> bool:
    """Whether a localization result names the thing that was actually broken.

    Exact equality, plus two tolerances that follow from the identifier
    conventions rather than from any particular task:

    * an edge ``A->B`` counts as naming ``A`` or ``B``. When a component
      stamps the wrong facet on its output, the earliest place the contract
      is *observably* violated is the edge leaving it, and blaming that edge
      is not a worse answer than blaming the node.
    * an artifact id ``producer::type::hash`` counts as naming its producer.
      Blaming the specific artifact is strictly more precise than blaming the
      node that emitted it, and scoring the more precise answer wrong would
      reward coarser localization.

    Both tolerances are structural and stated once here, rather than an
    ``acceptable_blame`` list per task, so they cannot be widened case by case
    until every answer counts as right. :attr:`BlameScore.blame_exact` reports
    the strict rate alongside it.
    """
    if not predicted:
        return False
    if predicted == expected:
        return True
    if "::" in predicted and predicted.split("::")[0] == expected:
        return True
    if "->" in predicted:
        endpoints = [part.strip() for part in predicted.split("->")]
        return any(blame_matches(end, expected) for end in endpoints)
    return False


class BlameScore(BaseModel):
    """How well the system localized one injected fault."""

    model_config = ConfigDict(extra="forbid")

    fault_id: str
    expected_blame: str
    expected_fault_class: Optional[str] = None
    predicted_blame: Optional[str] = None
    predicted_fault_class: Optional[str] = None
    #: The fault was silent, so detecting it at all is the harder result.
    silent: bool = False
    #: Whether anything was diagnosed. A system that never noticed scores
    #: ``detected=False``, which is distinct from noticing and blaming wrongly.
    detected: bool = False

    @property
    def blame_correct(self) -> bool:
        return blame_matches(self.predicted_blame, self.expected_blame)

    @property
    def blame_exact(self) -> bool:
        """Strict equality, reported alongside the tolerant rule.

        Kept separate so a reader can see how much of the accuracy came from
        the edge/endpoint tolerance rather than having it folded in silently.
        """
        return bool(self.predicted_blame) and self.predicted_blame == self.expected_blame

    @property
    def class_correct(self) -> bool:
        return (
            self.predicted_fault_class is not None
            and self.predicted_fault_class == self.expected_fault_class
        )


class TaskScore(BaseModel):
    """One (system, task) pair, scored on every axis that is measurable."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    system: str
    regime: str
    horizon: str
    available: bool = True
    unavailable_reason: str = ""

    #: Every required output artifact was produced.
    produced_required_outputs: bool = False
    #: No blocking check failed.
    hard_constraints_satisfied: bool = False
    #: Output matched the ground truth, when a ground truth exists.
    output_correct: Optional[bool] = None
    #: The composition decision matched the regime.
    regime_decision_correct: Optional[bool] = None
    n_components_used: int = 0
    minimal_components: Optional[int] = None
    usd: float = 0.0
    latency_s: float = 0.0
    repaired: bool = False
    blame: list[BlameScore] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """Task-level success. Deliberately excludes the regime decision."""
        if self.output_correct is not None:
            return self.output_correct and self.hard_constraints_satisfied
        return self.produced_required_outputs and self.hard_constraints_satisfied


class Aggregate(BaseModel):
    """A rate plus the denominator it was computed over."""

    model_config = ConfigDict(extra="forbid")

    n: int = 0
    value: Optional[float] = None
    #: Tasks excluded because the arm could not run here.
    n_unavailable: int = 0

    @property
    def reportable(self) -> bool:
        return self.n > 0 and self.value is not None

    def __str__(self) -> str:
        if not self.reportable:
            return f"n/a (0/{self.n + self.n_unavailable} runnable)"
        return f"{self.value:.2f} (n={self.n})"


class SystemReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str
    scores: list[TaskScore] = Field(default_factory=list)

    def runnable(self) -> list[TaskScore]:
        return [s for s in self.scores if s.available]

    def _rate(self, predicate, subset: Optional[Sequence[TaskScore]] = None) -> Aggregate:
        pool = list(subset if subset is not None else self.scores)
        runnable = [s for s in pool if s.available]
        decided = [s for s in runnable if predicate(s) is not None]
        if not decided:
            return Aggregate(n=0, value=None, n_unavailable=len(pool) - len(runnable))
        return Aggregate(
            n=len(decided),
            value=sum(1 for s in decided if predicate(s)) / len(decided),
            n_unavailable=len(pool) - len(runnable),
        )

    def success_rate(self, regime: Optional[str] = None) -> Aggregate:
        subset = [s for s in self.scores if regime is None or s.regime == regime]
        return self._rate(lambda s: s.succeeded, subset)

    def regime_accuracy(self, regime: Optional[str] = None) -> Aggregate:
        """How often the system made the right *structural* decision."""
        subset = [s for s in self.scores if regime is None or s.regime == regime]
        return self._rate(lambda s: s.regime_decision_correct, subset)

    def blame_accuracy(
        self, *, silent_only: bool = False, exact: bool = False
    ) -> Aggregate:
        blames = [
            b
            for s in self.runnable()
            for b in s.blame
            if not silent_only or b.silent
        ]
        if not blames:
            return Aggregate(n=0, value=None)
        hit = (lambda b: b.blame_exact) if exact else (lambda b: b.blame_correct)
        return Aggregate(
            n=len(blames), value=sum(1 for b in blames if hit(b)) / len(blames)
        )

    def detection_rate(self, *, silent_only: bool = False) -> Aggregate:
        blames = [
            b for s in self.runnable() for b in s.blame if not silent_only or b.silent
        ]
        if not blames:
            return Aggregate(n=0, value=None)
        return Aggregate(
            n=len(blames), value=sum(1 for b in blames if b.detected) / len(blames)
        )

    def mean_cost_usd(self) -> Aggregate:
        runnable = self.runnable()
        if not runnable:
            return Aggregate(n=0, value=None, n_unavailable=len(self.scores))
        return Aggregate(
            n=len(runnable), value=sum(s.usd for s in runnable) / len(runnable)
        )

    def unavailable_reasons(self) -> list[str]:
        return sorted({s.unavailable_reason for s in self.scores if not s.available and s.unavailable_reason})


class BenchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    systems: dict[str, SystemReport] = Field(default_factory=dict)
    #: Defects in the suite itself, surfaced alongside the results so a reader
    #: knows which claims the suite cannot support.
    suite_defects: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def table(self) -> str:
        """A results table that shows its denominators."""
        regimes = ["single_sufficient", "multi_necessary", "multi_harmful"]
        lines = [f"suite: {self.suite_id}"]
        if self.suite_defects:
            lines.append("SUITE DEFECTS (claims this suite cannot support):")
            lines.extend(f"  - {d}" for d in self.suite_defects)
        lines.append("")
        header = f"{'system':<28}{'success':>16}{'regime-decision':>18}{'blame':>14}{'silent-blame':>16}"
        lines.append(header)
        lines.append("-" * len(header))
        for name in sorted(self.systems):
            report = self.systems[name]
            lines.append(
                f"{name:<28}{str(report.success_rate()):>16}"
                f"{str(report.regime_accuracy()):>18}"
                f"{str(report.blame_accuracy()):>14}"
                f"{str(report.blame_accuracy(silent_only=True)):>16}"
            )
        lines.append("")
        lines.append("per-regime success")
        for regime in regimes:
            row = f"  {regime:<24}"
            for name in sorted(self.systems):
                row += f"{name}={self.systems[name].success_rate(regime)}  "
            lines.append(row)

        unavailable = {
            name: r.unavailable_reasons()
            for name, r in self.systems.items()
            if r.unavailable_reasons()
        }
        if unavailable:
            lines.append("")
            lines.append("ARMS NOT RUN (excluded from every aggregate above)")
            for name in sorted(unavailable):
                lines.append(f"  {name}: {unavailable[name][0]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def payload_matches(
    produced: Any, expected: Any, key_fields: Optional[list[str]] = None
) -> bool:
    """Structural comparison that tolerates ordering but not omission.

    Lists are compared as multisets of their canonical forms, because a
    component that returns the same findings in a different order has not made
    a mistake. Everything else is compared exactly.
    """
    if key_fields:
        if not isinstance(produced, dict) or not isinstance(expected, dict):
            return False
        return all(
            payload_matches(produced.get(k), expected.get(k)) for k in key_fields
        )
    if isinstance(expected, list) and isinstance(produced, list):
        if len(expected) != len(produced):
            return False
        return sorted(map(_canonical, produced)) == sorted(map(_canonical, expected))
    if isinstance(expected, dict) and isinstance(produced, dict):
        if set(expected) != set(produced):
            return False
        return all(payload_matches(produced[k], expected[k]) for k in expected)
    return produced == expected


def _canonical(value: Any) -> str:
    import json

    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def score_outputs(
    outputs: dict[str, Artifact], truth: GroundTruth
) -> Optional[bool]:
    """Whether the produced artifacts match the ground truth.

    Returns ``None`` when the task declares no scalar oracle. That is not a
    gap to be filled with a proxy metric — it is the situation the evaluation
    contract stack exists for, and the harness reports contract-level results
    for those tasks instead of inventing a score.
    """
    if not truth.has_scalar_oracle or not truth.expected_outputs:
        return None
    for type_name, expected in truth.expected_outputs.items():
        art = outputs.get(type_name)
        if art is None:
            return False
        if not payload_matches(art.payload, expected, truth.key_fields.get(type_name)):
            return False
    return True


def score_regime_decision(task: BenchTask, output: SystemOutput) -> Optional[bool]:
    """Did the system make the right call about *whether* to compose?

    ``multi_harmful`` is the discriminating case: composing there is a mistake
    even when the answer comes out right, because the extra components cost
    budget and add failure surface for nothing.
    """
    truth = task.ground_truth
    if truth is None:
        return None
    used = output.n_components
    if used == 0:
        return None
    minimal = truth.minimal_size or None

    if task.regime == "multi_harmful":
        return used <= 1
    if task.regime == "single_sufficient":
        return used <= 1
    if task.regime == "multi_necessary":
        if minimal is None:
            return used > 1
        return used >= minimal
    return None


def score_blame(task: BenchTask, output: SystemOutput) -> list[BlameScore]:
    """Compare the diagnosis against the answer sheet written with the task."""
    diagnosis = output.diagnosis
    top = diagnosis.diagnosis.top if diagnosis is not None else None
    blamed = diagnosis.diagnosis.localized_to if diagnosis is not None else None

    scores: list[BlameScore] = []
    for fault in task.injected_faults:
        expected_class = expected_fault_class(fault.mechanism)
        scores.append(
            BlameScore(
                fault_id=fault.fault_id,
                expected_blame=fault.expected_blame,
                expected_fault_class=expected_class.value if expected_class else None,
                predicted_blame=blamed,
                predicted_fault_class=top.fault_class.value if top else None,
                silent=fault.silent or is_silent(fault.mechanism),
                detected=top is not None,
            )
        )
    return scores


def score(task: BenchTask, output: SystemOutput) -> TaskScore:
    if not output.available:
        return TaskScore(
            task_id=task.task_id,
            system=output.system,
            regime=task.regime,
            horizon=task.horizon,
            available=False,
            unavailable_reason=output.unavailable_reason,
        )

    truth = task.ground_truth
    produced = all(t in output.outputs for t in task.dossier.required_outputs)
    return TaskScore(
        task_id=task.task_id,
        system=output.system,
        regime=task.regime,
        horizon=task.horizon,
        produced_required_outputs=produced,
        hard_constraints_satisfied=output.report.hard_constraints_satisfied,
        output_correct=score_outputs(output.outputs, truth) if truth else None,
        regime_decision_correct=score_regime_decision(task, output),
        n_components_used=output.n_components,
        minimal_components=truth.minimal_size if truth else None,
        usd=output.cost.usd,
        latency_s=output.cost.latency_s,
        repaired=output.repaired,
        blame=score_blame(task, output),
        notes=list(output.notes[:4]),
    )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def certify_environment(env: BenchEnvironment) -> BenchEnvironment:
    """Replace every declared card with the one its probes actually earned.

    This is the pipeline the method describes, run for real inside the
    benchmark: components arrive declared, and only execution moves them up
    the ladder. It is applied to the shared library, so every arm sees the
    same certified components — the question the benchmark asks is what each
    system *does* with that information, not who was given better cards.
    """
    from agentcoop.probe.runner import ProbeRunner

    runner = ProbeRunner(env.adapters, registry=env.types)
    library = env.library.model_copy(deep=True)
    for name in library.names():
        library.cards[name] = await runner.certify(library.require(name))
    return env.model_copy(update={"library": library})


class BenchHarness:
    """Runs systems over a suite. Never runs anything on construction."""

    def __init__(
        self,
        suite: BenchSuite,
        systems: Sequence[SystemUnderTest],
        *,
        inject_faults: bool = True,
        certify: bool = True,
        certify_before_injection: bool = True,
    ) -> None:
        """
        ``certify_before_injection``
            When true (the default), components are probed while healthy and
            the fault is installed afterwards. That is the realistic ordering
            — certification happens once at registration, and drift, version
            skew, or a corrupted reference file arrives later — and it is the
            only ordering under which runtime diagnosis is measurable at all:
            probing a component that is *already* broken causes the compiler
            to refuse to bind it, so no run ever happens to diagnose.

            Setting it false measures something different and also worth
            reporting: how many injected faults certification catches before
            execution. It is not the default because a suite run that way
            yields no localization numbers, and a reader would have no way to
            tell that from an empty column.
        """
        self.suite = suite
        self.systems = list(systems)
        self.inject_faults = inject_faults
        self.certify = certify
        self.certify_before_injection = certify_before_injection

    def environment(self, task: BenchTask) -> BenchEnvironment:
        return build_environment(task, inject_faults=self.inject_faults)

    async def prepare(self, task: BenchTask) -> BenchEnvironment:
        """Build, certify, and fault-inject one environment, in that order."""
        if not (self.certify and self.certify_before_injection):
            env = self.environment(task)
            return await certify_environment(env) if self.certify else env

        from agentcoop.bench.faults import install_faults

        healthy = build_environment(task, inject_faults=False)
        certified = await certify_environment(healthy)
        if not (self.inject_faults and task.injected_faults):
            return certified
        return install_faults(certified, task.injected_faults)

    async def run_task(
        self, task: BenchTask, system: SystemUnderTest
    ) -> TaskScore:
        # A fresh environment per (task, system): adapters accumulate state
        # (the stale-cache fault depends on it), and letting one system's run
        # prime another's would silently couple the arms.
        env = await self.prepare(task)
        try:
            output = await system.solve(env)
        except Exception as exc:  # a crashed arm is a finding, not a lost run
            output = SystemOutput(
                system=system.name,
                task_id=task.task_id,
                notes=[f"{type(exc).__name__}: {exc}"],
            )
        return score(task, output)

    async def run(
        self, *, tasks: Optional[Sequence[BenchTask]] = None
    ) -> BenchReport:
        selected = list(tasks if tasks is not None else self.suite.tasks)
        report = BenchReport(
            suite_id=self.suite.suite_id,
            suite_defects=self.suite.coverage_defects(),
        )
        for system in self.systems:
            system_report = SystemReport(system=system.name)
            for task in selected:
                system_report.scores.append(await self.run_task(task, system))
            report.systems[system.name] = system_report
        return report


def contract_summary(output: SystemOutput) -> dict[str, Any]:
    """Per-level pass rates for tasks that have no scalar oracle.

    Kept explicit rather than folded into ``TaskScore``: on an open-ended task
    this *is* the result, and it must not be summarized into a single number
    that reads like an accuracy.
    """
    return {
        level.value: {
            "coverage": output.report.coverage(level),
            "pass_rate": output.report.pass_rate(level),
            "unavailable": len(
                [
                    r
                    for r in output.report.by_level(level)
                    if r.status is CheckStatus.UNAVAILABLE
                ]
            ),
        }
        for level in CheckLevel
    }


__all__ = [
    "BlameScore",
    "TaskScore",
    "Aggregate",
    "SystemReport",
    "BenchReport",
    "BenchHarness",
    "score",
    "score_outputs",
    "score_regime_decision",
    "score_blame",
    "payload_matches",
    "blame_matches",
    "contract_summary",
    "certify_environment",
]
