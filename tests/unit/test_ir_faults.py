"""The fault taxonomy and its constraint on what repair may propose.

The taxonomy exists to stop a symptom being mapped straight onto an action.
Its load-bearing property is ``admissible_patches``: a fault class enumerates
which repair families are even *legal* responses, so a semantic contract
violation can never be answered by re-rolling a prompt.
"""

from __future__ import annotations

import math

import pytest

from agentcoop.ir.faults import (
    FAULT_TAXONOMY,
    BlameTarget,
    Diagnosis,
    FaultClass,
    FaultHypothesis,
    RepairTier,
)


def hypothesis(fault: FaultClass, p: float = 1.0, **kwargs) -> FaultHypothesis:
    return FaultHypothesis(fault_class=fault, probability=p, **kwargs)


class TestTaxonomyCompleteness:
    def test_every_fault_class_has_a_spec(self) -> None:
        assert set(FAULT_TAXONOMY) == set(FaultClass)

    def test_every_spec_declares_blame_targets_and_patches(self) -> None:
        for fault, spec in FAULT_TAXONOMY.items():
            assert spec.blame_targets, f"{fault} has no blame target"
            assert spec.admissible_patches, f"{fault} admits no repair"
            assert spec.description

    def test_escalating_classes_are_the_ones_automation_cannot_fix(self) -> None:
        escalating = {f for f, s in FAULT_TAXONOMY.items() if s.escalate}
        assert escalating == {
            FaultClass.TASK_SPECIFICATION,
            FaultClass.IRREDUCIBLE_UNCERTAINTY,
        }


class TestAdmissiblePatches:
    def test_semantic_contract_faults_reject_prompt_and_config_fixes(self) -> None:
        """A gene-identifier namespace mismatch is not fixed by retrying, by
        rewriting a prompt, or by raising a temperature. This is exactly the
        symptom-to-action shortcut the rewrite exists to eliminate."""
        h = hypothesis(FaultClass.ARTIFACT_CONTRACT)
        for illegal in ("retry_node", "rewrite_prompt", "retune_config", "add_specialist"):
            assert not h.admits(illegal), f"{illegal} must not be admissible for a contract fault"
        assert h.admits("insert_adapter")
        assert h.admits("tighten_contract")

    def test_configuration_faults_reject_structural_surgery(self) -> None:
        h = hypothesis(FaultClass.CONFIGURATION)
        for illegal in ("insert_adapter", "parallelize", "pin_environment", "define_merge"):
            assert not h.admits(illegal)
        assert h.admits("retune_config") and h.admits("retry_node")

    def test_environment_faults_reject_llm_level_fixes(self) -> None:
        h = hypothesis(FaultClass.ENVIRONMENT)
        assert not h.admits("rewrite_prompt")
        assert not h.admits("add_specialist")
        assert h.admits("pin_environment") and h.admits("isolate_environment")

    def test_evaluator_failure_is_not_repaired_by_changing_the_generator(self) -> None:
        """If the judge is broken, patching the thing being judged is worse than
        doing nothing — it optimizes against a broken oracle."""
        h = hypothesis(FaultClass.EVALUATOR_FAILURE)
        assert not h.admits("retry_node")
        assert not h.admits("replace_component")
        assert h.admits("diversify_evaluator") and h.admits("add_deterministic_check")

    def test_irreducible_uncertainty_admits_only_honesty(self) -> None:
        h = hypothesis(FaultClass.IRREDUCIBLE_UNCERTAINTY)
        assert set(h.spec.admissible_patches) == {"report_uncertainty", "add_human_gate"}


class TestRepairTiers:
    def test_executability_faults_are_contract_repair(self) -> None:
        for fault in (
            FaultClass.TOOL_FAILURE,
            FaultClass.ENVIRONMENT,
            FaultClass.ARTIFACT_CONTRACT,
            FaultClass.STALE_STATE,
        ):
            assert FAULT_TAXONOMY[fault].tier is RepairTier.CONTRACT_REPAIR

    def test_structural_faults_demand_redesign_not_local_patching(self) -> None:
        """A missing verifier or an absent information path is not fixable by
        touching one node — v1 had no way to express this."""
        assert FAULT_TAXONOMY[FaultClass.COORDINATION].tier is RepairTier.GLOBAL_REDESIGN
        assert FAULT_TAXONOMY[FaultClass.TASK_SPECIFICATION].tier is RepairTier.GLOBAL_REDESIGN

    def test_quality_faults_are_local_optimization(self) -> None:
        for fault in (
            FaultClass.CAPABILITY_MISMATCH,
            FaultClass.CONFIGURATION,
            FaultClass.EVALUATOR_FAILURE,
        ):
            assert FAULT_TAXONOMY[fault].tier is RepairTier.LOCAL_OPTIMIZATION

    def test_hypothesis_exposes_its_tier(self) -> None:
        assert hypothesis(FaultClass.COORDINATION).tier is RepairTier.GLOBAL_REDESIGN


class TestDiagnosis:
    def test_normalization_ranks_and_renormalizes_after_filtering(self) -> None:
        """Hypotheses stay bounded in [0, 1]; ``normalized`` restores the sum
        after low-likelihood classes have been dropped, and re-ranks."""
        d = Diagnosis(
            hypotheses=[
                hypothesis(FaultClass.CONFIGURATION, 0.2),
                hypothesis(FaultClass.ARTIFACT_CONTRACT, 0.6),
            ]
        ).normalized()
        assert d.top.fault_class is FaultClass.ARTIFACT_CONTRACT
        assert sum(h.probability for h in d.hypotheses) == pytest.approx(1.0)
        assert d.hypotheses[0].probability == pytest.approx(0.75)

    def test_normalization_is_pure(self) -> None:
        original = Diagnosis(hypotheses=[hypothesis(FaultClass.CONFIGURATION, 0.2)])
        original.normalized()
        assert original.hypotheses[0].probability == pytest.approx(0.2)

    def test_confident_diagnosis_has_low_entropy(self) -> None:
        confident = Diagnosis(
            hypotheses=[
                hypothesis(FaultClass.ARTIFACT_CONTRACT, 0.97),
                hypothesis(FaultClass.CONFIGURATION, 0.03),
            ]
        )
        assert confident.entropy < 0.5

    def test_uniform_diagnosis_has_maximal_entropy(self) -> None:
        """Four equally likely causes is 2 bits — the signal that the loop must
        gather evidence instead of guessing."""
        uniform = Diagnosis(
            hypotheses=[
                hypothesis(FaultClass.CONFIGURATION, 0.25),
                hypothesis(FaultClass.TOOL_FAILURE, 0.25),
                hypothesis(FaultClass.ENVIRONMENT, 0.25),
                hypothesis(FaultClass.ARTIFACT_CONTRACT, 0.25),
            ]
        )
        assert uniform.entropy == pytest.approx(2.0)

    def test_entropy_is_zero_for_a_single_certain_hypothesis(self) -> None:
        assert Diagnosis(hypotheses=[hypothesis(FaultClass.TOOL_FAILURE, 1.0)]).entropy == 0.0

    def test_empty_diagnosis_is_safe(self) -> None:
        empty = Diagnosis()
        assert empty.top is None
        assert empty.entropy == 0.0
        assert empty.normalized().hypotheses == []

    def test_top_k_truncates_the_ranking(self) -> None:
        d = Diagnosis(
            hypotheses=[
                hypothesis(FaultClass.ARTIFACT_CONTRACT, 0.5),
                hypothesis(FaultClass.CONFIGURATION, 0.3),
                hypothesis(FaultClass.TOOL_FAILURE, 0.2),
            ]
        )
        assert len(d.top_k(2)) == 2
        assert d.top_k(2)[0].fault_class is FaultClass.ARTIFACT_CONTRACT

    def test_contradicting_signals_are_retained(self) -> None:
        """An auditable diagnosis records what argued against it, not only for."""
        h = hypothesis(
            FaultClass.TOOL_FAILURE,
            0.6,
            supporting_signals=["sig:nonzero_exit"],
            contradicting_signals=["sig:outputs_present"],
        )
        assert h.contradicting_signals == ["sig:outputs_present"]

    def test_localization_is_carried_on_the_diagnosis(self) -> None:
        d = Diagnosis(
            hypotheses=[hypothesis(FaultClass.ARTIFACT_CONTRACT, 1.0)],
            localized_to="a2",
            localized_kind=BlameTarget.ARTIFACT,
        )
        assert d.localized_to == "a2"
        assert d.localized_kind is BlameTarget.ARTIFACT


class TestProbabilityBounds:
    def test_probability_must_be_a_probability(self) -> None:
        with pytest.raises(Exception):
            FaultHypothesis(fault_class=FaultClass.CONFIGURATION, probability=1.5)
        with pytest.raises(Exception):
            FaultHypothesis(fault_class=FaultClass.CONFIGURATION, probability=-0.1)
