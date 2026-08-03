"""Dossier self-consistency — catching specification faults before spending money."""

from __future__ import annotations

from agentcoop.ir.artifacts import ArtifactType
from agentcoop.ir.checks import CheckLevel
from agentcoop.ir.dossier import (
    EvaluatorAvailability,
    Invariant,
    Preference,
    Subgoal,
    TaskEvidenceDossier,
)


def types(*names: str) -> list[ArtifactType]:
    return [ArtifactType(name=n) for n in names]


def wellformed() -> TaskEvidenceDossier:
    return TaskEvidenceDossier(
        task_id="t1",
        goal="find markers and interpret them",
        artifact_types=types("matrix", "de_results", "gene_set", "report"),
        provided_inputs=["matrix"],
        required_outputs=["report"],
        subgoals=[
            Subgoal(
                subgoal_id="s1",
                description="differential expression",
                required_capability="differential_expression",
                consumes=["matrix"],
                produces=["de_results"],
            ),
            Subgoal(
                subgoal_id="s2",
                description="select markers",
                required_capability="marker_selection",
                consumes=["de_results"],
                produces=["gene_set"],
            ),
            Subgoal(
                subgoal_id="s3",
                description="interpret",
                required_capability="gene_set_interpretation",
                consumes=["gene_set"],
                produces=["report"],
            ),
        ],
        invariants=[
            Invariant(invariant_id="i1", description="outputs present", check="required_outputs_present")
        ],
    )


class TestSpecificationDefects:
    def test_wellformed_dossier_has_no_defects(self) -> None:
        assert wellformed().specification_defects() == []

    def test_required_output_with_no_producer_is_a_defect(self) -> None:
        """The v1 system would have discovered this only after paying to run
        several components."""
        d = wellformed()
        d.required_outputs = ["report", "figure"]
        d.artifact_types = types("matrix", "de_results", "gene_set", "report", "figure")
        defects = d.specification_defects()
        assert any("'figure' is not produced" in x for x in defects)

    def test_consumed_but_never_produced_artifact_is_a_defect(self) -> None:
        d = wellformed()
        d.subgoals[0].consumes = ["matrix", "metadata"]
        d.artifact_types = types("matrix", "de_results", "gene_set", "report", "metadata")
        assert any("'metadata' is consumed but never produced" in x for x in d.specification_defects())

    def test_undeclared_artifact_type_is_a_defect(self) -> None:
        d = wellformed()
        d.subgoals[0].produces = ["de_results", "qc_metrics"]
        assert any("'qc_metrics' is referenced but not declared" in x for x in d.specification_defects())

    def test_invariant_without_an_executable_check_is_a_defect(self) -> None:
        """An unenforceable hard constraint is worse than none: it looks like
        a guarantee and is not one."""
        d = wellformed()
        d.invariants.append(Invariant(invariant_id="i2", description="results must be biologically sensible"))
        assert any("i2" in x and "no executable check" in x for x in d.specification_defects())

    def test_duplicate_subgoal_id_is_a_defect(self) -> None:
        d = wellformed()
        d.subgoals.append(
            Subgoal(subgoal_id="s1", description="dup", required_capability="x", produces=["report"])
        )
        assert any("duplicate subgoal id 's1'" in x for x in d.specification_defects())

    def test_dependency_on_unknown_subgoal_is_a_defect(self) -> None:
        d = wellformed()
        d.subgoals[0].depends_on = ["s99"]
        assert any("depends on unknown 's99'" in x for x in d.specification_defects())

    def test_dependency_cycle_is_detected(self) -> None:
        d = wellformed()
        d.subgoals[0].depends_on = ["s3"]
        d.subgoals[2].depends_on = ["s1"]
        assert any("cycle" in x for x in d.specification_defects())

    def test_preference_without_a_dimension_is_a_defect(self) -> None:
        d = wellformed()
        d.preferences.append(Preference(preference_id="p1", description="be good", dimension=""))
        assert any("p1" in x and "no utility dimension" in x for x in d.specification_defects())


class TestEvaluatorAvailability:
    def test_missing_level_defaults_to_unavailable_not_available(self) -> None:
        """Silence about an evaluator means we do not have one."""
        assert wellformed().availability(CheckLevel.CLAIM) is EvaluatorAvailability.UNAVAILABLE

    def test_scalar_oracle_requires_a_deterministic_claim_evaluator(self) -> None:
        d = wellformed()
        assert not d.has_scalar_oracle

        d.evaluators = {CheckLevel.CLAIM: EvaluatorAvailability.RUBRIC}
        assert not d.has_scalar_oracle, "a rubric is not an oracle"

        d.evaluators = {CheckLevel.CLAIM: EvaluatorAvailability.DETERMINISTIC}
        assert d.has_scalar_oracle

    def test_preference_only_is_not_a_scalar_oracle(self) -> None:
        d = wellformed()
        d.evaluators = {CheckLevel.CLAIM: EvaluatorAvailability.PREFERENCE_ONLY}
        assert not d.has_scalar_oracle


class TestLookups:
    def test_required_capabilities_collects_from_subgoals(self) -> None:
        assert wellformed().required_capabilities == {
            "differential_expression",
            "marker_selection",
            "gene_set_interpretation",
        }

    def test_subgoal_and_artifact_lookup(self) -> None:
        d = wellformed()
        assert d.subgoal("s2") is not None
        assert d.subgoal("nope") is None
        assert d.artifact_type("gene_set") is not None
        assert d.artifact_type("nope") is None


class TestRoundTrip:
    def test_yaml_round_trip_preserves_defect_status(self, tmp_path) -> None:
        d = wellformed()
        d.evaluators = {CheckLevel.HARD: EvaluatorAvailability.DETERMINISTIC}
        path = tmp_path / "dossier.yaml"
        d.to_yaml(path)
        restored = TaskEvidenceDossier.from_yaml(path)
        assert restored.task_id == d.task_id
        assert restored.specification_defects() == d.specification_defects()
        assert restored.availability(CheckLevel.HARD) is EvaluatorAvailability.DETERMINISTIC
        assert restored.required_capabilities == d.required_capabilities
