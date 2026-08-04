"""Benchmark layer: is the measurement itself honest?

These tests are mostly about the harness rather than about any system's
score. A benchmark that quietly zeroes an arm it could not run, or that counts
composing-when-it-should-not as a success, would produce numbers that look
like evidence and are not.
"""

from __future__ import annotations

import pytest

from agentcoop.bench import (
    BenchHarness,
    BenchSuite,
    FaultyAdapter,
    Mechanism,
    build_environment,
    default_systems,
    expected_fault_class,
    is_silent,
    score,
)
from agentcoop.bench.baselines import (
    AgentCoOpSystem,
    BestSingleComponent,
    CodingAgentBaseline,
    EqualBudgetSingleAgent,
    SameWorkflowSingleAgent,
    SystemOutput,
    unavailable,
)
from agentcoop.bench.faults import serialize_subgoals
from agentcoop.bench.harness import (
    BlameScore,
    SystemReport,
    blame_matches,
    payload_matches,
    score_outputs,
    score_regime_decision,
)
from agentcoop.bench.suites.synthetic import (
    MAPPING,
    SOURCE_IDS,
    source_artifact,
    synthetic_suite,
    task_multi_harmful,
    task_multi_necessary,
    task_needless_serialization,
    task_silent_corruption,
    task_silent_empty,
    task_single_sufficient,
)
from agentcoop.components.base import Invocation, make_artifact
from agentcoop.ir.capability import CertificationLevel
from agentcoop.ir.faults import FaultClass


# ---------------------------------------------------------------------------
# Suite construction
# ---------------------------------------------------------------------------


class TestSuite:
    def test_all_three_regimes_are_represented(self) -> None:
        coverage = synthetic_suite().regime_coverage()
        assert all(coverage[r] > 0 for r in coverage)

    def test_a_complete_suite_reports_no_coverage_defects(self) -> None:
        assert synthetic_suite().coverage_defects() == []

    def test_a_suite_without_multi_harmful_says_what_it_cannot_support(self) -> None:
        """The negative claim needs tasks where composing is the wrong answer."""
        suite = BenchSuite(
            suite_id="partial",
            tasks=[task_multi_necessary(), task_silent_corruption()],
        )
        defects = " ".join(suite.coverage_defects())
        assert "multi_harmful" in defects
        assert "decides correctly in that regime" in defects

    def test_a_suite_of_only_loud_faults_is_flagged(self) -> None:
        suite = BenchSuite(
            suite_id="loud",
            tasks=[
                task_single_sufficient(),
                task_multi_harmful(),
                task_needless_serialization(),
            ],
        )
        assert any("no silent faults" in d for d in suite.coverage_defects())

    def test_tasks_have_no_specification_defects(self) -> None:
        for task in synthetic_suite().tasks:
            assert task.dossier.specification_defects() == [], task.task_id

    def test_the_mapper_is_genuinely_lossy(self) -> None:
        """The premise of the multi_harmful task, asserted rather than assumed."""
        assert set(SOURCE_IDS) - set(MAPPING) == {"S4"}


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------


class TestFaultInjection:
    def test_silent_and_loud_mechanisms_are_distinguished(self) -> None:
        assert is_silent(Mechanism.NAMESPACE_CORRUPTION.value)
        assert is_silent(Mechanism.SILENT_EMPTY_OUTPUT.value)
        assert not is_silent(Mechanism.DEPENDENCY_BREAK.value)

    def test_every_mechanism_declares_its_expected_fault_class(self) -> None:
        for mechanism in Mechanism:
            assert expected_fault_class(mechanism.value) is not None

    def test_an_unknown_mechanism_is_reported_not_skipped(self) -> None:
        """A fault that was never injected must not be scored as undetected."""
        task = task_silent_corruption()
        task.injected_faults[0].mechanism = "teleportation"
        env = build_environment(task)
        assert env.active_faults == []
        assert any("NOT injected" in n for n in env.notes)

    def test_a_fault_targeting_an_absent_component_is_reported(self) -> None:
        task = task_silent_corruption()
        task.injected_faults[0].target = "nonexistent"
        env = build_environment(task)
        assert env.active_faults == []
        assert any("not in this task" in n for n in env.notes)

    def test_injection_is_invisible_to_inspection(self) -> None:
        """A system must not be able to detect the fault by reading the card."""
        clean = build_environment(task_silent_corruption(), inject_faults=False)
        faulty = build_environment(task_silent_corruption())
        assert clean.library.require("mapper") == faulty.library.require("mapper")
        assert faulty.adapters.get("mapper").name == "mapper"

    async def test_namespace_corruption_changes_the_artifact_identity(self) -> None:
        """A corrupted artifact must not inherit the clean one's content hash."""
        env = build_environment(task_multi_necessary(), inject_faults=False)
        inner = env.adapters.get("mapper")
        inv = Invocation(
            component="mapper",
            subgoal_id="map",
            inputs={"source_set": source_artifact()},
        )
        clean = await inner.invoke(inv)
        faulty = await FaultyAdapter(
            inner, Mechanism.NAMESPACE_CORRUPTION, {"facet": "namespace", "value": "source"}
        ).invoke(inv)

        assert clean.ok and faulty.ok
        assert faulty.outputs["canonical_set"].facets["namespace"] == "source"
        assert (
            faulty.outputs["canonical_set"].content_hash
            != clean.outputs["canonical_set"].content_hash
        )

    async def test_silent_empty_preserves_the_shape(self) -> None:
        """The envelope has to keep looking healthy, or the fault is not silent."""
        env = build_environment(task_multi_necessary(), inject_faults=False)
        inv = Invocation(
            component="mapper", subgoal_id="map", inputs={"source_set": source_artifact()}
        )
        result = await FaultyAdapter(
            env.adapters.get("mapper"), Mechanism.SILENT_EMPTY_OUTPUT
        ).invoke(inv)
        payload = result.outputs["canonical_set"].payload
        assert result.ok
        assert set(payload) == {"items", "n", "dropped"}
        assert payload["items"] == []

    async def test_stale_cache_answers_from_the_first_input_it_ever_saw(self) -> None:
        env = build_environment(task_multi_necessary(), inject_faults=False)
        adapter = FaultyAdapter(env.adapters.get("mapper"), Mechanism.STALE_CACHE)
        first = await adapter.invoke(
            Invocation(
                component="mapper",
                subgoal_id="map",
                inputs={"source_set": source_artifact(["S1"])},
            )
        )
        second = await adapter.invoke(
            Invocation(
                component="mapper",
                subgoal_id="map",
                inputs={"source_set": source_artifact(["S2", "S3"])},
            )
        )
        assert second.outputs["canonical_set"].payload == first.outputs["canonical_set"].payload

    async def test_dependency_break_is_loud_and_classed(self) -> None:
        env = build_environment(task_multi_necessary(), inject_faults=False)
        result = await FaultyAdapter(
            env.adapters.get("analyst"), Mechanism.DEPENDENCY_BREAK, {"package": "libassoc"}
        ).invoke(Invocation(component="analyst", subgoal_id="analyze"))
        assert not result.ok
        assert result.has_fault(FaultClass.ENVIRONMENT)
        assert "libassoc" in result.errors[0]

    def test_serialization_only_touches_the_named_subgoal(self) -> None:
        dossier = task_needless_serialization().dossier
        serialized = serialize_subgoals(dossier, "analyze_direct", "map")
        assert serialized.subgoal("map").depends_on == ["analyze_direct"]
        assert serialized.subgoal("analyze_direct").depends_on == []
        assert dossier.subgoal("map").depends_on == [], "must not mutate the original"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_composing_on_a_multi_harmful_task_is_scored_wrong(self) -> None:
        """Even a correct answer. Spending more for nothing is the failure."""
        task = task_multi_harmful()
        composed = SystemOutput(
            system="x", task_id=task.task_id, components_used=["mapper", "analyst"]
        )
        declined = SystemOutput(
            system="x", task_id=task.task_id, components_used=["direct_analyst"]
        )
        assert score_regime_decision(task, composed) is False
        assert score_regime_decision(task, declined) is True

    def test_declining_on_a_multi_necessary_task_is_scored_wrong(self) -> None:
        task = task_multi_necessary()
        declined = SystemOutput(
            system="x", task_id=task.task_id, components_used=["generalist"]
        )
        assert score_regime_decision(task, declined) is False

    def test_the_regime_decision_is_not_folded_into_task_success(self) -> None:
        task = task_multi_harmful()
        truth = task.ground_truth
        expected = truth.expected_outputs["finding_set"]
        output = SystemOutput(
            system="x",
            task_id=task.task_id,
            components_used=["mapper", "analyst"],
            outputs={"finding_set": make_artifact(type_name="finding_set", payload=expected)},
        )
        result = score(task, output)
        assert result.succeeded is True
        assert result.regime_decision_correct is False

    def test_a_task_with_no_scalar_oracle_is_not_given_an_invented_score(self) -> None:
        truth = task_multi_necessary().ground_truth
        assert truth.has_scalar_oracle is False
        assert score_outputs({}, truth) is None

    def test_payload_comparison_tolerates_order_but_not_omission(self) -> None:
        assert payload_matches({"r": ["a", "b"]}, {"r": ["b", "a"]})
        assert not payload_matches({"r": ["a"]}, {"r": ["a", "b"]})
        assert not payload_matches({"r": ["a"]}, {"r": ["a"], "n": 1})

    def test_key_fields_narrow_the_comparison(self) -> None:
        assert payload_matches(
            {"r": ["a"], "debug": "noise"}, {"r": ["a"], "debug": "other"}, ["r"]
        )


class TestBlameMatching:
    def test_exact_match(self) -> None:
        assert blame_matches("node_a", "node_a")

    def test_an_edge_counts_as_naming_either_endpoint(self) -> None:
        assert blame_matches("node_a->node_b", "node_a")
        assert blame_matches("node_a->node_b", "node_b")

    def test_an_artifact_id_counts_as_naming_its_producer(self) -> None:
        assert blame_matches("node_a::gene_set::deadbeef", "node_a")

    def test_the_tolerance_does_not_accept_unrelated_answers(self) -> None:
        assert not blame_matches("node_c->node_d", "node_a")
        assert not blame_matches("node_c::gene_set::deadbeef", "node_a")
        assert not blame_matches(None, "node_a")
        assert not blame_matches("", "node_a")

    def test_exact_and_tolerant_rates_are_reported_separately(self) -> None:
        report = SystemReport(
            system="x",
            scores=[
                score(
                    task_silent_corruption(),
                    SystemOutput(system="x", task_id="t", components_used=["mapper"]),
                )
            ],
        )
        report.scores[0].blame = [
            BlameScore(
                fault_id="f",
                expected_blame="node_a",
                predicted_blame="node_a->node_b",
                detected=True,
            )
        ]
        assert report.blame_accuracy().value == 1.0
        assert report.blame_accuracy(exact=True).value == 0.0


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_an_unavailable_arm_is_excluded_from_aggregates_not_zeroed(self) -> None:
        task = task_single_sufficient()
        report = SystemReport(
            system="coding_agent",
            scores=[score(task, unavailable("coding_agent", task, "no live agent"))],
        )
        assert report.success_rate().reportable is False
        assert report.success_rate().n == 0
        assert report.success_rate().n_unavailable == 1

    def test_the_reason_an_arm_did_not_run_is_reported(self) -> None:
        task = task_single_sufficient()
        report = SystemReport(
            system="coding_agent",
            scores=[score(task, unavailable("coding_agent", task, "no live agent"))],
        )
        assert report.unavailable_reasons() == ["no live agent"]

    async def test_the_coding_agent_arm_refuses_to_fake_a_run(self) -> None:
        """The 'why not just use Codex' arm must not be silently substituted."""
        env = build_environment(task_multi_necessary())
        output = await CodingAgentBaseline().solve(env)
        assert output.available is False
        assert "coding-agent" in output.unavailable_reason

    def test_the_results_table_names_the_arms_that_did_not_run(self) -> None:
        task = task_single_sufficient()
        report = SystemReport(
            system="coding_agent",
            scores=[score(task, unavailable("coding_agent", task, "no live agent"))],
        )
        from agentcoop.bench.harness import BenchReport

        text = BenchReport(suite_id="s", systems={"coding_agent": report}).table()
        assert "ARMS NOT RUN" in text
        assert "no live agent" in text


# ---------------------------------------------------------------------------
# Certification ordering
# ---------------------------------------------------------------------------


class TestCertification:
    async def test_healthy_components_certify(self) -> None:
        harness = BenchHarness(synthetic_suite(), [])
        env = await harness.prepare(task_multi_necessary())
        for name in env.library.names():
            assert env.library.require(name).certification_level >= CertificationLevel.PROBED, name

    async def test_certification_precedes_fault_onset_by_default(self) -> None:
        """Otherwise the compiler refuses the broken component and nothing runs.

        Both orderings are meaningful; the default is the one under which
        runtime localization is measurable at all.
        """
        task = task_silent_corruption()
        after = await BenchHarness(synthetic_suite(), []).prepare(task)
        assert after.library.require("mapper").certification_level >= CertificationLevel.PROBED
        assert after.active_faults

        before = await BenchHarness(
            synthetic_suite(), [], certify_before_injection=False
        ).prepare(task)
        assert (
            before.library.require("mapper").certification_level
            < CertificationLevel.PROBED
        ), "probing a corrupted component should fail its schema probe"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    async def test_agentcoop_declines_to_compose_where_composing_is_harmful(self) -> None:
        env = await BenchHarness(synthetic_suite(), []).prepare(task_multi_harmful())
        output = await AgentCoOpSystem(repair=False).solve(env)
        assert output.declined_to_compose is True
        assert output.n_components == 1

    async def test_agentcoop_composes_where_composition_is_necessary(self) -> None:
        env = await BenchHarness(synthetic_suite(), []).prepare(task_multi_necessary())
        output = await AgentCoOpSystem(repair=False).solve(env)
        assert output.n_components >= 2

    async def test_a_single_component_cannot_discharge_a_multi_necessary_task(self) -> None:
        """The premise of the regime, checked rather than assumed."""
        env = await BenchHarness(synthetic_suite(), []).prepare(task_multi_necessary())
        for baseline in (BestSingleComponent(), EqualBudgetSingleAgent()):
            output = await baseline.solve(env)
            assert not score(task_multi_necessary(), output).succeeded, baseline.name

    async def test_the_same_workflow_arm_replays_the_structure_with_one_component(
        self,
    ) -> None:
        env = await BenchHarness(synthetic_suite(), []).prepare(task_multi_necessary())
        agentcoop = await AgentCoOpSystem(repair=False).solve(env)
        replay = await SameWorkflowSingleAgent().solve(env)
        assert replay.available
        assert replay.structure != agentcoop.structure, "components differ"
        assert replay.n_components == 1
        assert agentcoop.n_components > 1

    async def test_a_silent_fault_is_detected_and_correctly_blamed(self) -> None:
        task = task_silent_empty()
        result = await BenchHarness(synthetic_suite(), []).run_task(
            task, AgentCoOpSystem(repair=False)
        )
        assert result.blame and result.blame[0].silent
        assert result.blame[0].detected
        assert result.blame[0].blame_correct
        assert result.blame[0].class_correct

    async def test_a_needless_serialization_is_diagnosed_as_coordination(self) -> None:
        """A fault no node-level repair can fix, so misclassing it is costly."""
        task = task_needless_serialization()
        result = await BenchHarness(synthetic_suite(), []).run_task(
            task, AgentCoOpSystem(repair=False)
        )
        assert result.blame[0].detected
        assert result.blame[0].predicted_fault_class == FaultClass.COORDINATION.value

    async def test_a_full_run_is_reproducible(self) -> None:
        suite = BenchSuite(suite_id="rep", tasks=[task_multi_necessary()])
        first = await BenchHarness(suite, default_systems()).run()
        second = await BenchHarness(suite, default_systems()).run()
        assert first.model_dump() == second.model_dump()

    async def test_a_crashing_arm_is_recorded_rather_than_lost(self) -> None:
        class Exploding:
            name = "exploding"

            async def solve(self, env):
                raise RuntimeError("boom")

        result = await BenchHarness(synthetic_suite(), []).run_task(
            task_single_sufficient(), Exploding()
        )
        assert result.available is True
        assert not result.succeeded
        assert any("RuntimeError" in n for n in result.notes)


@pytest.mark.parametrize("mechanism", [m for m in Mechanism])
def test_every_mechanism_is_exercised_by_the_suite_or_documented(
    mechanism: Mechanism,
) -> None:
    """A mechanism nothing uses is a mechanism nothing tests."""
    used = {f.mechanism for t in synthetic_suite().tasks for f in t.injected_faults}
    unused = {
        Mechanism.STALE_CACHE.value,
        Mechanism.MISCALIBRATED_GRADER.value,
        Mechanism.TRUNCATED_OUTPUT.value,
    }
    assert mechanism.value in used or mechanism.value in unused
