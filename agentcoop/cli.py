"""Command line surface.

Every command here is a thin shell over a library call, with one editorial
rule: a command that cannot do the thing it was asked to do says so and exits
non-zero. It does not substitute something adjacent and report success. The
places that matters most:

* ``probe`` needs live adapters. Without them there is nothing to certify, and
  printing declared capability cards as though they had been verified would
  invert the meaning of the whole certification ladder.
* ``bench run`` prints arms it could not run as ``n/a`` with the reason,
  never as a zero.
* ``explain`` is a first-class command, not a debug aid. If the system cannot
  say why a node or an edge is in the workflow, that is a bug in the compiler,
  and this command is where it becomes visible.

Runs are written as a single self-contained ``run.json``, so ``diagnose`` and
``repair`` can work on a run recorded earlier — including one recorded on a
different machine — without needing the components to still be reachable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import BaseModel, ConfigDict, Field

from agentcoop.bench.baselines import (
    AgentCoOpSystem,
    BestSingleComponent,
    CodingAgentBaseline,
    EqualBudgetSingleAgent,
    SameWorkflowSingleAgent,
)
from agentcoop.bench.harness import BenchHarness, certify_environment
from agentcoop.bench.suites.synthetic import synthetic_suite
from agentcoop.bench.task import BenchSuite, BenchTask, build_environment
from agentcoop.compile.compiler import Compiler
from agentcoop.diagnose.detectors import detect_all
from agentcoop.diagnose.diagnose import Diagnoser
from agentcoop.diagnose.localize import localize
from agentcoop.execute.engine import ExecutionEngine, ExecutionResult
from agentcoop.execute.trace import Trace
from agentcoop.ir.artifacts import TypeRegistry
from agentcoop.ir.capability import CapabilityCard, ComponentLibrary
from agentcoop.ir.checks import CheckReport
from agentcoop.ir.dossier import TaskEvidenceDossier
from agentcoop.ir.workflow import CompiledWorkflow
from agentcoop.probe.certificate import certification_report, explain_level
from agentcoop.repair.loop import RepairLoop
from agentcoop.repair.shadow import ShadowValidator

app = typer.Typer(
    add_completion=False,
    help="AgentCo-Op — an evidence-driven compiler for heterogeneous agent workflows.",
    no_args_is_help=True,
)
dossier_app = typer.Typer(no_args_is_help=True, help="Work with task dossiers.")
bench_app = typer.Typer(no_args_is_help=True, help="Run benchmark suites.")
app.add_typer(dossier_app, name="dossier")
app.add_typer(bench_app, name="bench")


#: Named suites available to ``--suite``.
SUITES = {"synthetic": synthetic_suite}

#: Named systems available to ``bench run --system``.
SYSTEMS = {
    "agentcoop": lambda: AgentCoOpSystem(),
    "agentcoop-norepair": lambda: AgentCoOpSystem(repair=False),
    "best_single_component": BestSingleComponent,
    "equal_budget_single_agent": EqualBudgetSingleAgent,
    "same_workflow_single_agent": SameWorkflowSingleAgent,
    "coding_agent": CodingAgentBaseline,
}


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _suite(name: str) -> BenchSuite:
    builder = SUITES.get(name)
    if builder is None:
        _fail(f"unknown suite '{name}' (available: {', '.join(sorted(SUITES))})")
    return builder()


def _task(suite_name: str, task_id: str) -> BenchTask:
    suite = _suite(suite_name)
    task = suite.task(task_id)
    if task is None:
        _fail(
            f"suite '{suite_name}' has no task '{task_id}' "
            f"(available: {', '.join(t.task_id for t in suite.tasks)})"
        )
    return task


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        path.write_text(payload.model_dump_json(indent=2))
    else:
        path.write_text(json.dumps(payload, indent=2, default=str))


def load_library(path: Path) -> ComponentLibrary:
    """Read a library YAML: either a list of cards or ``{components: [...]}``."""
    import yaml

    raw = yaml.safe_load(path.read_text())
    entries = raw.get("components", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        _fail(f"{path}: expected a list of capability cards")
    library = ComponentLibrary()
    for entry in entries:
        library.add(CapabilityCard.model_validate(entry))
    return library


# ---------------------------------------------------------------------------
# Run records
# ---------------------------------------------------------------------------


class RunRecord(BaseModel):
    """Everything ``diagnose`` and ``repair`` need from a finished run.

    Self-contained on purpose. Diagnosis reads a trace, not a live system, so
    a run recorded on a cluster can be diagnosed on a laptop — and, more to
    the point, a diagnosis can be re-derived later and checked against what
    the system concluded at the time.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite: str = ""
    task_id: str
    dossier: TaskEvidenceDossier
    workflow: CompiledWorkflow
    library: ComponentLibrary
    trace: Trace
    report: CheckReport = Field(default_factory=CheckReport)
    ok: bool = False
    notes: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, run_dir: Path) -> "RunRecord":
        path = run_dir / "run.json" if run_dir.is_dir() else run_dir
        if not path.exists():
            _fail(f"no run record at {path}")
        return cls.model_validate_json(path.read_text())


# ---------------------------------------------------------------------------
# dossier lint
# ---------------------------------------------------------------------------


@dossier_app.command("lint")
def dossier_lint(
    path: Path = typer.Argument(..., help="Path to a dossier YAML."),
) -> None:
    """Report specification defects before anything is compiled.

    A defective specification cannot be fixed by any amount of node-level
    repair, so catching it here is the only cheap opportunity.
    """
    if not path.exists():
        _fail(f"no such file: {path}")
    dossier = TaskEvidenceDossier.from_yaml(path)
    defects = dossier.specification_defects()
    typer.echo(f"{dossier.task_id}: {len(dossier.subgoals)} subgoal(s)")
    if not defects:
        typer.secho("no specification defects", fg=typer.colors.GREEN)
        return
    typer.secho(f"{len(defects)} specification defect(s):", fg=typer.colors.RED)
    for defect in defects:
        typer.echo(f"  - {defect}")
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


@app.command("probe")
def probe_cmd(
    library: Optional[Path] = typer.Argument(
        None, help="Library YAML. Requires adapters to be resolvable."
    ),
    task: Optional[str] = typer.Option(None, "--task", help="Benchmark task id."),
    suite: str = typer.Option("synthetic", "--suite"),
    component: Optional[str] = typer.Option(None, "--component"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write certified cards here."),
) -> None:
    """Certify components by executing probes against them."""
    if task is None and library is None:
        _fail("give either a library path or --task")
    if library is not None and task is None:
        _fail(
            f"{library}: certification requires live adapters, and a library YAML "
            "declares contracts but not how to invoke them. Probe a benchmark task "
            "with --task, or drive agentcoop.probe.ProbeRunner with your own "
            "AdapterRegistry. Printing the declared cards here would report "
            "declarations as if they had been verified."
        )

    env = build_environment(_task(suite, task), inject_faults=False)
    certified = asyncio.run(certify_environment(env))
    names = [component] if component else certified.library.names()

    failures = 0
    for name in names:
        card = certified.library.get(name)
        if card is None:
            _fail(f"no component '{name}' in task '{task}'")
        typer.echo(explain_level(card))
        report = certification_report(card)
        if not report.hard_constraints_satisfied:
            failures += 1
        typer.echo("")

    if out is not None:
        _write(out, {"components": [c.model_dump() for c in certified.library.cards.values()]})
        typer.echo(f"wrote {out}")
    if failures:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# compile / explain
# ---------------------------------------------------------------------------


@app.command("compile")
def compile_cmd(
    dossier_path: Optional[Path] = typer.Argument(None, help="Dossier YAML."),
    library_path: Optional[Path] = typer.Option(None, "--library"),
    task: Optional[str] = typer.Option(None, "--task", help="Benchmark task id."),
    suite: str = typer.Option("synthetic", "--suite"),
    certify: bool = typer.Option(
        True,
        "--certify/--no-certify",
        help="Probe components before compiling. Without it every card stays "
        "DECLARED and most subgoals will refuse to bind — which is the correct "
        "behaviour, not a bug.",
    ),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the workflow here."),
) -> None:
    """Enumerate candidates, justify them, analyse them, and choose."""
    if task is not None:
        env = build_environment(_task(suite, task), inject_faults=False)
        if certify:
            env = asyncio.run(certify_environment(env))
        dossier, library, types = env.task.dossier, env.library, env.types
    else:
        if dossier_path is None or library_path is None:
            _fail("give a dossier path and --library, or --task")
        dossier = TaskEvidenceDossier.from_yaml(dossier_path)
        library = load_library(library_path)
        types = TypeRegistry()
        for artifact_type in dossier.artifact_types:
            types.register_type(artifact_type)

    result = Compiler(types=types).compile(dossier, library, workflow_id=dossier.task_id)
    typer.echo(result.explain())

    if result.workflow is None:
        typer.secho("\nno workflow was compiled", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if out is not None:
        _write(out, result.workflow)
        typer.echo(f"\nwrote {out}")


@app.command("explain")
def explain_cmd(
    workflow_path: Path = typer.Argument(..., help="A compiled workflow JSON."),
) -> None:
    """Print the design evidence behind every node and edge.

    If a decision cannot be explained here, that is a defect in the compiler,
    not in this command — so an inadmissible record exits non-zero.
    """
    if not workflow_path.exists():
        _fail(f"no such file: {workflow_path}")
    workflow = CompiledWorkflow.model_validate_json(workflow_path.read_text())

    typer.echo(f"workflow : {workflow.workflow_id}")
    typer.echo(f"task     : {workflow.task_id}")
    typer.echo(f"structure: {workflow.structural_key()}")
    typer.echo(f"components: {', '.join(workflow.components)}")
    typer.echo("")

    rows = workflow.evidence.summary_rows()
    if not rows:
        typer.secho(
            "this workflow carries no design evidence at all; every node in it is "
            "unjustified",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    inadmissible = 0
    for row in rows:
        ok = bool(row["admissible"])
        inadmissible += 0 if ok else 1
        typer.secho(
            f"[{'ok ' if ok else 'BAD'}] {row['decision_id']}: "
            f"{row['kind']} -> {row['target']}",
            fg=None if ok else typer.colors.RED,
        )
        typer.echo(
            f"        evidence: {', '.join(row['evidence_kinds']) or 'none'}"
            f"   alternatives considered: {row['n_alternatives']}"
        )

    typer.echo("")
    if inadmissible:
        typer.secho(
            f"{inadmissible} decision(s) rest on no load-bearing evidence",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.secho("every decision is backed by load-bearing evidence", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


async def _compile_and_run(
    task: BenchTask, *, suite: str, certify: bool, inject_faults: bool
) -> tuple[RunRecord, ExecutionResult]:
    env = build_environment(task, inject_faults=False)
    if certify:
        env = await certify_environment(env)
    if inject_faults and task.injected_faults:
        from agentcoop.bench.faults import install_faults

        env = install_faults(env, task.injected_faults)

    compilation = Compiler(types=env.types).compile(
        env.task.dossier, env.library, workflow_id=task.task_id
    )
    if compilation.workflow is None:
        typer.echo(compilation.explain())
        _fail("no workflow was compiled; nothing to run")

    engine = ExecutionEngine(env.adapters, type_registry=env.types, library=env.library)
    execution = await engine.run(
        compilation.workflow,
        env.task.dossier,
        task.inputs,
        limits=env.task.dossier.limits,
        run_id=f"{task.task_id}",
    )
    record = RunRecord(
        run_id=execution.trace.run_id,
        suite=suite,
        task_id=task.task_id,
        dossier=env.task.dossier,
        workflow=compilation.workflow,
        library=env.library,
        trace=execution.trace,
        report=execution.report,
        ok=execution.ok,
        notes=list(env.notes),
    )
    return record, execution


@app.command("run")
def run_cmd(
    task: str = typer.Option(..., "--task", help="Benchmark task id."),
    suite: str = typer.Option("synthetic", "--suite"),
    out: Path = typer.Option(Path("runs/latest"), "--out"),
    certify: bool = typer.Option(True, "--certify/--no-certify"),
    faults: bool = typer.Option(
        True, "--faults/--no-faults", help="Install the task's injected faults."
    ),
) -> None:
    """Compile and execute a task, writing a self-contained run record."""
    record, execution = asyncio.run(
        _compile_and_run(
            _task(suite, task), suite=suite, certify=certify, inject_faults=faults
        )
    )
    _write(out / "run.json", record)
    _write(out / "workflow.json", record.workflow)

    typer.echo(f"workflow : {record.workflow.structural_key()}")
    typer.echo(f"executed : {', '.join(sorted(execution.state.executed)) or 'nothing'}")
    if execution.state.failed:
        typer.secho(f"failed   : {', '.join(sorted(execution.state.failed))}", fg=typer.colors.RED)
    typer.echo(f"outputs  : {', '.join(sorted(execution.outputs)) or 'none'}")
    typer.echo(f"wrote {out / 'run.json'}")

    # "The workflow ran to completion" is not "the result is good". A node can
    # report success while emitting an empty result, and hard constraints will
    # happily pass. Reporting only `execution.ok` here would reproduce the
    # exact conflation this system exists to remove, so blocking signals are
    # surfaced at the same level as a hard failure.
    blocking = detect_all(
        execution.trace, execution.report, record.workflow, record.dossier
    ).blocking()
    if not execution.ok:
        typer.secho("run did not satisfy its hard constraints", fg=typer.colors.RED)
    elif blocking:
        typer.secho(
            f"run completed, but {len(blocking)} blocking signal(s) say the result "
            "should not be trusted:",
            fg=typer.colors.RED,
        )
        for signal in blocking[:4]:
            typer.echo(f"  [{signal.severity.value}] {signal.kind.value}: {signal.detail}")
        typer.echo(f"  run 'agentcoop diagnose {out}' for the full diagnosis")
    else:
        typer.secho("run ok, no blocking signal", fg=typer.colors.GREEN)

    if not execution.ok or blocking:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# diagnose / repair
# ---------------------------------------------------------------------------


@app.command("diagnose")
def diagnose_cmd(
    run_dir: Path = typer.Argument(..., help="A directory containing run.json."),
    top: int = typer.Option(4, "--top", help="Hypotheses to print."),
) -> None:
    """Detect, localize, and diagnose — from a recorded run, offline."""
    record = RunRecord.load(run_dir)
    types = TypeRegistry()
    for artifact_type in record.dossier.artifact_types:
        types.register_type(artifact_type)

    signals = detect_all(record.trace, record.report, record.workflow, record.dossier)
    blocking = signals.blocking()
    typer.echo(f"{len(signals.signals)} signal(s), {len(blocking)} blocking")
    for signal in blocking[:top]:
        typer.echo(f"  [{signal.severity.value}] {signal.kind.value}: {signal.detail}")
    if not blocking:
        typer.secho("\nno blocking signal; this run is healthy", fg=typer.colors.GREEN)
        return

    localization = localize(record.trace, signals, record.workflow, record.dossier, types)
    outcome = Diagnoser().diagnose(
        signals, localization, record.workflow, record.dossier, record.library
    )

    typer.echo(f"\nlocalized to: {outcome.diagnosis.localized_to or 'nothing specific'}")
    typer.echo(f"entropy     : {outcome.diagnosis.entropy:.2f} bits")
    typer.echo("")
    for hypothesis in outcome.diagnosis.top_k(top):
        typer.echo(
            f"  {hypothesis.probability:.3f}  {hypothesis.fault_class.value:<22}"
            f" tier={hypothesis.tier.value}"
        )
        typer.echo(f"          admissible patches: {', '.join(hypothesis.spec.admissible_patches)}")

    if outcome.needs_more_evidence:
        typer.secho(
            f"\nthe diagnosis is too uncertain to act on: "
            f"{outcome.evidence_gathering_hint}",
            fg=typer.colors.YELLOW,
        )


@app.command("repair")
def repair_cmd(
    run_dir: Path = typer.Argument(..., help="A directory containing run.json."),
    max_attempts: int = typer.Option(3, "--max-attempts"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the repaired workflow."),
) -> None:
    """Bounded transactional repair, with shadow validation before every commit.

    Needs the components to be runnable, because a patch that has not been
    re-executed has not been validated. A run record alone is enough to
    *diagnose*; it is not enough to repair.
    """
    record = RunRecord.load(run_dir)
    task = _task(record.suite or "synthetic", record.task_id)
    outcome = asyncio.run(_repair(record, task, max_attempts))

    for note in outcome.notes:
        typer.echo(f"  {note}")
    typer.echo("")
    for transaction in outcome.log.transactions:
        typer.echo(
            f"  [{transaction.outcome.value:<14}] {transaction.patch.family:<20} "
            f"{transaction.reason}"
        )
    typer.echo("")
    if outcome.terminal_reason:
        typer.secho(outcome.terminal_reason, fg=typer.colors.YELLOW)
    if not outcome.repaired:
        typer.secho("no repair was committed", fg=typer.colors.YELLOW)
        return
    typer.secho(
        f"repaired: {record.workflow.structural_key()} -> "
        f"{outcome.workflow.structural_key()}",
        fg=typer.colors.GREEN,
    )
    if out is not None:
        _write(out, outcome.workflow)
        typer.echo(f"wrote {out}")


async def _repair(record: RunRecord, task: BenchTask, max_attempts: int):
    from agentcoop.bench.faults import install_faults

    env = build_environment(task, inject_faults=False)
    env = await certify_environment(env)
    if task.injected_faults:
        env = install_faults(env, task.injected_faults)

    engine = ExecutionEngine(env.adapters, type_registry=env.types, library=env.library)

    async def harness(candidate: CompiledWorkflow) -> ExecutionResult:
        return await engine.run(
            candidate,
            env.task.dossier,
            task.inputs,
            limits=env.task.dossier.limits,
            run_id=f"{task.task_id}::shadow",
        )

    baseline = await harness(record.workflow)
    ctx = Compiler(types=env.types).context(env.task.dossier, env.library)
    loop = RepairLoop(ctx, env.library, validator=ShadowValidator(env.library))
    return await loop.run(
        record.workflow, baseline, harness, max_attempts=max_attempts
    )


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


@bench_app.command("run")
def bench_run(
    suite: str = typer.Option("synthetic", "--suite"),
    system: list[str] = typer.Option(
        [], "--system", help="Repeatable. Defaults to every registered system."
    ),
    task: list[str] = typer.Option([], "--task", help="Repeatable. Defaults to all."),
    certify: bool = typer.Option(True, "--certify/--no-certify"),
    certify_first: bool = typer.Option(
        True,
        "--certify-first/--certify-after-injection",
        help="Whether components are probed before faults are installed. "
        "Probing after injection measures how much certification catches "
        "pre-execution, but yields no runtime localization numbers.",
    ),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the report JSON here."),
) -> None:
    """Run a suite and print a results table that shows its denominators."""
    bench_suite = _suite(suite)
    names = system or list(SYSTEMS)
    unknown = [n for n in names if n not in SYSTEMS]
    if unknown:
        _fail(
            f"unknown system(s): {', '.join(unknown)} "
            f"(available: {', '.join(sorted(SYSTEMS))})"
        )

    tasks = None
    if task:
        tasks = [_task(suite, t) for t in task]

    harness = BenchHarness(
        bench_suite,
        [SYSTEMS[n]() for n in names],
        certify=certify,
        certify_before_injection=certify_first,
    )
    report = asyncio.run(harness.run(tasks=tasks))
    typer.echo(report.table())

    typer.echo("\nblame accuracy (tolerant / exact) and detection")
    for name in sorted(report.systems):
        system_report = report.systems[name]
        typer.echo(
            f"  {name:<28} blame={system_report.blame_accuracy()} "
            f"exact={system_report.blame_accuracy(exact=True)} "
            f"detected={system_report.detection_rate()}"
        )

    if out is not None:
        _write(out, report)
        typer.echo(f"\nwrote {out}")


@bench_app.command("list")
def bench_list() -> None:
    """Show the registered suites, their tasks, and anything they cannot support."""
    for name in sorted(SUITES):
        suite = SUITES[name]()
        typer.echo(f"{name}: {suite.description or suite.suite_id}")
        for task in suite.tasks:
            faults = ", ".join(f.mechanism for f in task.injected_faults) or "-"
            typer.echo(
                f"  {task.task_id:<24} {task.regime:<18} horizon={task.horizon:<7} faults={faults}"
            )
        for defect in suite.coverage_defects():
            typer.secho(f"  ! {defect}", fg=typer.colors.YELLOW)
        typer.echo("")
    typer.echo(f"systems: {', '.join(sorted(SYSTEMS))}")


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
