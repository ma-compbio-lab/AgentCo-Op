"""End-to-end: dossier -> probe -> compile -> execute -> diagnose -> repair.

Unit tests pin each subsystem's contract. These pin the properties that only
exist once the subsystems are wired together, and that would be easy to lose
in a refactor that kept every unit test green:

* certification actually gates binding, so a component that fails its negative
  probes cannot end up in a compiled workflow;
* the compiler declines to compose when a single component suffices, and says
  so explicitly rather than by omission;
* a silent failure survives all the way from the adapter boundary to a named
  blame target;
* running the same task twice produces byte-identical results.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentcoop.bench.baselines import AgentCoOpSystem
from agentcoop.bench.harness import BenchHarness, certify_environment
from agentcoop.bench.suites.synthetic import (
    analyst_spec,
    synthetic_suite,
    task_multi_harmful,
    task_multi_necessary,
    task_silent_empty,
    task_single_sufficient,
)
from agentcoop.bench.task import build_environment
from agentcoop.cli import RunRecord, app
from agentcoop.compile.compiler import Compiler
from agentcoop.diagnose.detectors import detect_all
from agentcoop.diagnose.diagnose import Diagnoser
from agentcoop.diagnose.localize import localize
from agentcoop.execute.engine import ExecutionEngine
from agentcoop.ir.capability import CertificationLevel
from agentcoop.ir.evidence import EvidenceKind
from agentcoop.probe.certificate import certification_report

runner = CliRunner()


async def _prepared(task):
    return await BenchHarness(synthetic_suite(), []).prepare(task)


# ---------------------------------------------------------------------------
# Certification gates composition
# ---------------------------------------------------------------------------


class TestCertificationGatesComposition:
    async def test_a_credulous_component_cannot_be_bound(self) -> None:
        """A component that accepts garbage must not reach a compiled workflow.

        The analyst is made permissive, so its ``invalid_input`` probes fail
        and it stalls at PROBED — below the PROBED-or-better bar its subgoal
        sets. Nothing else about it changes.
        """
        task = task_multi_necessary()
        permissive = analyst_spec()
        permissive.params["strict"] = False
        task.component_specs = [
            s for s in task.component_specs if s.name != "analyst"
        ] + [permissive]
        for subgoal in task.dossier.subgoals:
            subgoal.min_certification = "CERTIFIED"

        env = await certify_environment(build_environment(task, inject_faults=False))
        assert (
            env.library.require("analyst").certification_level
            < CertificationLevel.CERTIFIED
        )

        result = Compiler(types=env.types).compile(task.dossier, env.library)
        chosen = result.workflow.components if result.workflow else []
        assert "analyst" not in chosen

    async def test_certification_is_earned_by_execution_not_declaration(self) -> None:
        task = task_multi_necessary()
        declared = build_environment(task, inject_faults=False)
        assert all(
            declared.library.require(n).certification_level == CertificationLevel.DECLARED
            for n in declared.library.names()
        )

        certified = await certify_environment(declared)
        assert all(
            certified.library.require(n).certification_level >= CertificationLevel.PROBED
            for n in certified.library.names()
        )

    async def test_an_unprobed_library_fails_its_certification_report(self) -> None:
        env = build_environment(task_multi_necessary(), inject_faults=False)
        report = certification_report(env.library.require("mapper"))
        assert not report.hard_constraints_satisfied
        assert report.unavailable()


# ---------------------------------------------------------------------------
# The compiler's negative decision
# ---------------------------------------------------------------------------


class TestDecliningToCompose:
    async def test_a_single_component_workflow_is_always_a_candidate(self) -> None:
        env = await _prepared(task_multi_necessary())
        result = Compiler(types=env.types).compile(env.task.dossier, env.library)
        singles = [c for c in result.candidates if len(c.components) == 1]
        assert singles, "the option not to compose must always be on the table"

    async def test_declining_is_recorded_as_a_decision_not_an_omission(self) -> None:
        env = await _prepared(task_single_sufficient())
        result = Compiler(types=env.types).compile(env.task.dossier, env.library)
        assert result.workflow is not None
        assert result.workflow.n_distinct_components == 1
        kinds = {
            record.decision_kind.value
            for record in result.workflow.evidence.records.values()
        }
        assert kinds, "the choice must leave an evidence record behind"

    async def test_composition_is_refused_where_it_would_lose_data(self) -> None:
        env = await _prepared(task_multi_harmful())
        output = await AgentCoOpSystem(repair=False).solve(env)
        assert output.declined_to_compose
        assert "mapper" not in output.components_used

    async def test_composition_is_chosen_where_it_is_the_only_route(self) -> None:
        env = await _prepared(task_multi_necessary())
        output = await AgentCoOpSystem(repair=False).solve(env)
        assert output.n_components >= 2
        assert not output.declined_to_compose


# ---------------------------------------------------------------------------
# Evidence survives the whole pipeline
# ---------------------------------------------------------------------------


class TestEvidence:
    async def test_every_binding_carries_load_bearing_evidence(self) -> None:
        env = await _prepared(task_multi_necessary())
        result = Compiler(types=env.types).compile(env.task.dossier, env.library)
        assert result.workflow is not None
        records = result.workflow.evidence.records
        assert records
        for record in records.values():
            assert record.admissible, record.decision_id
        assert result.workflow.evidence.fully_justified

    async def test_capability_evidence_is_backed_by_observation(self) -> None:
        """A binding justified by an LLM's assertion would not be admissible."""
        env = await _prepared(task_multi_necessary())
        result = Compiler(types=env.types).compile(env.task.dossier, env.library)
        kinds = {
            item.kind
            for record in result.workflow.evidence.records.values()
            for item in record.evidence
            if item.load_bearing
        }
        assert EvidenceKind.CAPABILITY in kinds
        assert EvidenceKind.REQUIREMENT in kinds


# ---------------------------------------------------------------------------
# Silent failure survives to a blame target
# ---------------------------------------------------------------------------


class TestSilentFailure:
    async def test_an_empty_result_becomes_a_named_blame_target(self) -> None:
        task = task_silent_empty()
        env = await _prepared(task)

        engine = ExecutionEngine(
            env.adapters, type_registry=env.types, library=env.library
        )
        compilation = Compiler(types=env.types).compile(env.task.dossier, env.library)
        execution = await engine.run(
            compilation.workflow, env.task.dossier, task.inputs, run_id="silent"
        )

        # The run looks healthy by every measure the executor has.
        assert execution.ok
        assert "report" in execution.outputs

        signals = detect_all(
            execution.trace, execution.report, compilation.workflow, env.task.dossier
        )
        blocking = signals.blocking()
        assert blocking, "an empty result with exit code 0 must not pass silently"

        localization = localize(
            execution.trace, signals, compilation.workflow, env.task.dossier, env.types
        )
        outcome = Diagnoser().diagnose(
            signals, localization, compilation.workflow, env.task.dossier, env.library
        )
        assert outcome.diagnosis.localized_to
        assert outcome.diagnosis.localized_to.startswith("analyze__analyst")

    async def test_an_uncertain_diagnosis_stops_rather_than_guessing(self) -> None:
        result = await BenchHarness(synthetic_suite(), []).run_task(
            task_silent_empty(), AgentCoOpSystem(repair=True)
        )
        assert result.blame[0].detected
        assert not result.repaired, "a 1.6-bit hypothesis set is not grounds to patch"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    async def test_the_same_task_compiles_to_the_same_workflow(self) -> None:
        first = await _prepared(task_multi_necessary())
        second = await _prepared(task_multi_necessary())
        a = Compiler(types=first.types).compile(first.task.dossier, first.library)
        b = Compiler(types=second.types).compile(second.task.dossier, second.library)
        assert a.workflow.structural_key() == b.workflow.structural_key()
        assert a.selection.rationale == b.selection.rationale

    async def test_the_same_run_produces_the_same_artifact_hashes(self) -> None:
        outputs = []
        for _ in range(2):
            env = await _prepared(task_multi_necessary())
            output = await AgentCoOpSystem(repair=False).solve(env)
            outputs.append(
                {k: v.content_hash for k, v in sorted(output.outputs.items())}
            )
        assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_bench_list_shows_regimes_and_faults(self) -> None:
        result = runner.invoke(app, ["bench", "list"])
        assert result.exit_code == 0
        assert "multi_harmful" in result.stdout
        assert "namespace_corruption" in result.stdout

    def test_compile_explains_the_choice(self) -> None:
        result = runner.invoke(app, ["compile", "--task", "syn-multi"])
        assert result.exit_code == 0
        assert "PARETO FRONT" in result.stdout
        assert "SELECTED" in result.stdout
        assert "DESIGN EVIDENCE" in result.stdout

    def test_probe_on_a_library_yaml_refuses_rather_than_faking(self, tmp_path: Path) -> None:
        """Printing declared cards as certified would invert the ladder."""
        library = tmp_path / "lib.yaml"
        library.write_text("components: []\n")
        result = runner.invoke(app, ["probe", str(library)])
        assert result.exit_code == 1
        assert "requires live adapters" in result.output

    def test_probe_reports_the_earned_level(self) -> None:
        result = runner.invoke(app, ["probe", "--task", "syn-multi", "--component", "mapper"])
        assert result.exit_code == 0
        assert "CERTIFIED" in result.stdout
        assert "probes do not count" in result.stdout

    def test_run_then_diagnose_round_trips_through_disk(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        run = runner.invoke(app, ["run", "--task", "syn-silent-empty", "--out", str(out)])
        assert (out / "run.json").exists()
        assert run.exit_code == 1, "a silent failure must not exit clean"
        assert "should not be trusted" in run.stdout

        record = RunRecord.load(out)
        assert record.task_id == "syn-silent-empty"

        diagnosis = runner.invoke(app, ["diagnose", str(out)])
        assert diagnosis.exit_code == 0
        assert "empty_output" in diagnosis.stdout
        assert "localized to" in diagnosis.stdout

    def test_a_healthy_run_exits_clean(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        result = runner.invoke(app, ["run", "--task", "syn-multi", "--out", str(out)])
        assert result.exit_code == 0
        assert "no blocking signal" in result.stdout

    def test_diagnose_on_a_healthy_run_says_so(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        runner.invoke(app, ["run", "--task", "syn-multi", "--out", str(out)])
        result = runner.invoke(app, ["diagnose", str(out)])
        assert result.exit_code == 0
        assert "healthy" in result.stdout

    def test_explain_fails_on_an_unjustified_workflow(self, tmp_path: Path) -> None:
        from agentcoop.ir.workflow import Atomic, CompiledWorkflow

        path = tmp_path / "wf.json"
        path.write_text(
            CompiledWorkflow(
                workflow_id="w",
                task_id="t",
                term=Atomic(component="c", subgoal_id="s").ensure_ids(),
            ).model_dump_json()
        )
        result = runner.invoke(app, ["explain", str(path)])
        assert result.exit_code == 1
        assert "unjustified" in result.output

    def test_dossier_lint_reports_defects(self, tmp_path: Path) -> None:
        task = task_multi_necessary()
        broken = task.dossier.model_copy(deep=True)
        broken.required_outputs = ["nothing_produces_this"]
        path = tmp_path / "dossier.yaml"
        broken.to_yaml(path)

        result = runner.invoke(app, ["dossier", "lint", str(path)])
        assert result.exit_code == 1
        assert "nothing_produces_this" in result.output

    def test_dossier_lint_passes_a_clean_dossier(self, tmp_path: Path) -> None:
        path = tmp_path / "dossier.yaml"
        task_multi_necessary().dossier.to_yaml(path)
        result = runner.invoke(app, ["dossier", "lint", str(path)])
        assert result.exit_code == 0
        assert "no specification defects" in result.stdout

    def test_bench_run_writes_a_report_and_names_unavailable_arms(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "report.json"
        result = runner.invoke(
            app,
            [
                "bench", "run",
                "--system", "agentcoop-norepair",
                "--system", "coding_agent",
                "--task", "syn-single",
                "--out", str(out),
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        assert "ARMS NOT RUN" in result.stdout
        assert "coding_agent" in result.stdout

    def test_unknown_system_is_rejected_with_the_available_list(self) -> None:
        result = runner.invoke(app, ["bench", "run", "--system", "gpt-9"])
        assert result.exit_code == 1
        assert "unknown system" in result.output
        assert "agentcoop" in result.output


@pytest.mark.parametrize("task_id", [t.task_id for t in synthetic_suite().tasks])
async def test_every_task_in_the_suite_is_runnable(task_id: str) -> None:
    """A task that cannot be attempted cannot contribute a result."""
    task = synthetic_suite().task(task_id)
    result = await BenchHarness(synthetic_suite(), []).run_task(
        task, AgentCoOpSystem(repair=False)
    )
    assert result.available
    assert not any("RuntimeError" in n or "Traceback" in n for n in result.notes)
