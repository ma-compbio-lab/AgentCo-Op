"""Design evidence admissibility — the rule that stops LLM-imagined topology."""

from __future__ import annotations

from agentcoop.ir.evidence import (
    Alternative,
    DecisionKind,
    DesignEvidenceRecord,
    EvidenceItem,
    EvidenceKind,
    EvidenceLedger,
    EvidenceStrength,
    asserted_evidence,
    capability_evidence,
    coordination_evidence,
    outcome_evidence,
    requirement_evidence,
)


def bind_record(*evidence: EvidenceItem, decision_id: str = "d1") -> DesignEvidenceRecord:
    return DesignEvidenceRecord(
        decision_id=decision_id,
        decision_kind=DecisionKind.BIND_COMPONENT,
        decision="bind GeneAgent to gene-set interpretation",
        target="s2__geneagent",
        subgoal_id="s2",
        evidence=list(evidence),
    )


class TestAdmissibility:
    def test_bind_needs_both_requirement_and_capability(self) -> None:
        record = bind_record(
            requirement_evidence("s2", "subgoal s2 requires gene-set interpretation"),
            capability_evidence("geneagent", "P-17", "consumed a 50-gene list and emitted labels"),
        )
        assert record.admissible
        assert record.rejection_reason() is None

    def test_requirement_alone_is_not_enough_to_bind(self) -> None:
        """Knowing the work is needed says nothing about whether this component
        can do it."""
        record = bind_record(requirement_evidence("s2", "subgoal s2 requires interpretation"))
        assert not record.admissible
        assert EvidenceKind.CAPABILITY in record.missing_evidence_kinds()

    def test_llm_assertion_never_makes_a_decision_admissible(self) -> None:
        """The central rule. A model may propose; it may not justify."""
        record = bind_record(
            requirement_evidence("s2", "subgoal s2 requires interpretation"),
            asserted_evidence(
                EvidenceKind.CAPABILITY,
                "GeneAgent looks like the right tool for gene sets",
                "llm:designer",
            ),
        )
        record.proposed_by = "llm:designer"
        assert not record.admissible
        assert EvidenceKind.CAPABILITY in record.missing_evidence_kinds()

    def test_readme_documentation_is_not_load_bearing(self) -> None:
        """A README claim is documentation, not observation."""
        record = bind_record(
            requirement_evidence("s2", "needed"),
            capability_evidence("geneagent", "readme", "README says it does gene sets", observed=False),
        )
        assert not record.admissible

    def test_probe_observation_is_load_bearing(self) -> None:
        item = capability_evidence("geneagent", "P-17", "probe passed", observed=True)
        assert item.strength is EvidenceStrength.OBSERVED
        assert item.load_bearing

    def test_rejection_reason_names_the_missing_kinds(self) -> None:
        record = bind_record()
        reason = record.rejection_reason()
        assert reason is not None
        assert "requirement" in reason and "capability" in reason


class TestOutcomeEvidenceThreshold:
    def test_thin_history_is_downgraded_to_asserted(self) -> None:
        """Two prior runs is anecdote, not statistics."""
        item = outcome_evidence("this pairing worked before", "stats:a->b", n=2)
        assert item.strength is EvidenceStrength.ASSERTED
        assert not item.load_bearing

    def test_sufficient_history_becomes_statistical(self) -> None:
        item = outcome_evidence("this pairing worked before", "stats:a->b", n=3)
        assert item.strength is EvidenceStrength.STATISTICAL
        assert item.load_bearing

    def test_fallback_requires_outcome_evidence(self) -> None:
        """Adding a fallback arm is only justified by an observed failure rate."""
        record = DesignEvidenceRecord(
            decision_id="d",
            decision_kind=DecisionKind.ADD_FALLBACK,
            decision="add fallback",
            target="t",
            evidence=[outcome_evidence("primary fails 30% of runs", "stats:primary", n=10)],
        )
        assert record.admissible

        thin = DesignEvidenceRecord(
            decision_id="d2",
            decision_kind=DecisionKind.ADD_FALLBACK,
            decision="add fallback",
            target="t",
            evidence=[outcome_evidence("primary failed once", "stats:primary", n=1)],
        )
        assert not thin.admissible


class TestDecisionKindRequirements:
    def test_parallelize_requires_coordination_evidence(self) -> None:
        without = DesignEvidenceRecord(
            decision_id="p1",
            decision_kind=DecisionKind.PARALLELIZE,
            decision="run RNA and ATAC branches concurrently",
            target="par_1",
            evidence=[requirement_evidence("s1", "both modalities are required")],
        )
        assert not without.admissible

        with_coord = without.model_copy(
            update={
                "evidence": without.evidence
                + [
                    coordination_evidence(
                        "branches share no artifacts and observe different modalities",
                        "independence:par_1",
                    )
                ]
            }
        )
        assert with_coord.admissible

    def test_decline_structure_is_a_first_class_justified_decision(self) -> None:
        """Choosing NOT to add agents must itself be recorded and justified."""
        record = DesignEvidenceRecord(
            decision_id="d0",
            decision_kind=DecisionKind.DECLINE_STRUCTURE,
            decision="single component covers every subgoal; no composition added",
            target="workflow",
            evidence=[requirement_evidence("s1", "all subgoals served by seurat alone")],
            alternatives=[
                Alternative(
                    description="two-component RNA + ATAC workflow",
                    rejection_reason="no subgoal requires ATAC; added cost with no evidence gain",
                )
            ],
        )
        assert record.admissible
        assert record.alternatives[0].rejection_reason


class TestEvidenceLedger:
    def test_coverage_and_full_justification(self) -> None:
        ledger = EvidenceLedger()
        ledger.add(
            bind_record(
                requirement_evidence("s1", "needed"),
                capability_evidence("a", "P1", "probed"),
                decision_id="ok",
            )
        )
        assert ledger.fully_justified
        assert ledger.evidence_coverage() == 1.0

        ledger.add(bind_record(requirement_evidence("s2", "needed"), decision_id="bad"))
        assert not ledger.fully_justified
        assert ledger.evidence_coverage() == 0.5
        assert [r.decision_id for r in ledger.inadmissible()] == ["bad"]

    def test_observed_fraction_separates_derived_from_executed(self) -> None:
        """Derivable-from-spec is weaker than actually-ran; the distinction is
        reported rather than blurred."""
        ledger = EvidenceLedger()
        ledger.add(
            DesignEvidenceRecord(
                decision_id="derived_only",
                decision_kind=DecisionKind.ADD_VERIFIER,
                decision="verify",
                target="v",
                evidence=[requirement_evidence("s1", "verification required")],
            )
        )
        assert ledger.evidence_coverage() == 1.0
        assert ledger.observed_fraction() == 0.0

        ledger.add(
            bind_record(
                requirement_evidence("s2", "needed"),
                capability_evidence("a", "P1", "probed"),
                decision_id="observed",
            )
        )
        assert ledger.observed_fraction() == 0.5

    def test_empty_ledger_scores_zero_not_one(self) -> None:
        """A workflow with no recorded reasoning has no evidence, not perfect
        evidence."""
        assert EvidenceLedger().evidence_coverage() == 0.0
        assert EvidenceLedger().observed_fraction() == 0.0

    def test_for_target_groups_decisions(self) -> None:
        ledger = EvidenceLedger()
        ledger.add(bind_record(decision_id="a"))
        ledger.add(bind_record(decision_id="b"))
        assert len(ledger.for_target("s2__geneagent")) == 2

    def test_summary_rows_are_sorted_and_flat(self) -> None:
        ledger = EvidenceLedger()
        ledger.add(bind_record(decision_id="z"))
        ledger.add(bind_record(decision_id="a"))
        rows = ledger.summary_rows()
        assert [r["decision_id"] for r in rows] == ["a", "z"]
        assert rows[0]["proposed_by"] == "compiler"


class TestStrongestEvidence:
    def test_strongest_prefers_statistical_over_documented(self) -> None:
        record = bind_record(
            capability_evidence("a", "readme", "documented", observed=False),
            EvidenceItem(
                kind=EvidenceKind.CAPABILITY,
                strength=EvidenceStrength.STATISTICAL,
                claim="succeeded in 9/10 prior runs",
                source_ref="stats:a",
            ),
        )
        best = record.strongest(EvidenceKind.CAPABILITY)
        assert best is not None and best.strength is EvidenceStrength.STATISTICAL

    def test_strongest_returns_none_for_absent_kind(self) -> None:
        assert bind_record().strongest(EvidenceKind.OUTCOME) is None
