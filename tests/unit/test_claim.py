"""Tests for claim bundles: well-formedness and traceability.

The cases that matter are the ones where a bundle *looks* finished: support
that is only an assertion, a sensitivity analysis that never ran, instability
that was found and then not mentioned, and provenance that stops short of the
raw inputs.
"""

from __future__ import annotations

from agentcoop.evaluate.claim import (
    ClaimBundle,
    DefectSeverity,
    EvidenceRef,
    EvidenceRefKind,
    SensitivityKind,
    SensitivityResult,
    audit_claim,
    check_wellformed,
    has_alternatives,
    raw_inputs,
    trace_claim,
    uncertainty_declared,
)
from agentcoop.ir.artifacts import Artifact
from agentcoop.ir.checks import SignalSource
from agentcoop.ir.dossier import EvidenceChainRequirement
from agentcoop.ir.evidence import EvidenceStrength


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _artifacts() -> dict[str, Artifact]:
    """counts (raw) -> normalized -> de_table (the claim is read off de_table)."""
    counts = Artifact(artifact_id="counts", type_name="CountMatrix", producer="input")
    normalized = Artifact(
        artifact_id="normalized",
        type_name="NormalizedMatrix",
        producer="norm_node",
        derived_from=["counts"],
    )
    de_table = Artifact(
        artifact_id="de_table",
        type_name="DETable",
        producer="de_node",
        derived_from=["normalized"],
    )
    return {a.artifact_id: a for a in (counts, normalized, de_table)}


def _statistical_ref(ref_id: str = "e1", artifact_ids: list[str] | None = None) -> EvidenceRef:
    return EvidenceRef(
        ref_id=ref_id,
        kind=EvidenceRefKind.STATISTIC,
        statement="adjusted p < 0.01 for 42 genes",
        source=SignalSource.DETERMINISTIC,
        strength=EvidenceStrength.OBSERVED,
        artifact_ids=list(artifact_ids or ["de_table"]),
    )


def _good_bundle() -> ClaimBundle:
    return ClaimBundle(
        claim_id="c1",
        claim="Pathway X is upregulated in treated samples.",
        supporting_evidence=[_statistical_ref()],
        assumptions=["counts are raw and unfiltered"],
        sensitivity_analyses=[
            SensitivityResult(
                analysis_id="threshold_sweep",
                kind=SensitivityKind.SENSITIVITY,
                parameter="fdr_threshold",
                variations=["0.01", "0.05", "0.1"],
                conclusion_stable=True,
            )
        ],
        alternative_explanations=["batch effect between treated and control plates"],
        uncertainty="effect direction is stable; magnitude varies 1.4-2.1x across thresholds",
        provenance=["de_table"],
    )


# ---------------------------------------------------------------------------
# well-formedness
# ---------------------------------------------------------------------------


def test_a_complete_bundle_is_well_formed() -> None:
    report = check_wellformed(_good_bundle())
    assert report.well_formed, report.summary()
    assert report.n_load_bearing_support == 1
    assert report.errors == []


def test_a_claim_supported_only_by_assertion_is_not_well_formed() -> None:
    bundle = _good_bundle()
    bundle.supporting_evidence = [
        EvidenceRef(
            ref_id="e1",
            kind=EvidenceRefKind.MODEL_ASSERTION,
            statement="the model considers this well established",
            source=SignalSource.LLM_JUDGE,
            strength=EvidenceStrength.ASSERTED,
        )
    ]
    report = check_wellformed(bundle)
    assert not report.well_formed
    assert "SUPPORT_NOT_LOAD_BEARING" in {d.code for d in report.errors}


def test_llm_evidence_cannot_be_promoted_to_load_bearing() -> None:
    """Constraint 1: model output enters as ASSERTED, whatever the caller wrote."""
    bundle = _good_bundle()
    bundle.supporting_evidence = [
        EvidenceRef(
            ref_id="judge",
            kind=EvidenceRefKind.MODEL_ASSERTION,
            statement="a rubric scored this 9/10",
            source=SignalSource.LLM_JUDGE,
            strength=EvidenceStrength.OBSERVED,  # mislabelled on purpose
        )
    ]
    report = check_wellformed(bundle)
    codes = {d.code for d in report.errors}
    assert "LLM_EVIDENCE_OVERSTATED" in codes
    # ...and it must not be counted as support either.
    assert "SUPPORT_NOT_LOAD_BEARING" in codes
    assert report.n_load_bearing_support == 0


def test_unaddressed_contradicting_evidence_is_a_defect() -> None:
    bundle = _good_bundle()
    bundle.alternative_explanations = []
    bundle.contradicting_evidence = [
        EvidenceRef(
            ref_id="neg",
            kind=EvidenceRefKind.STATISTIC,
            statement="the effect vanishes in the replicate cohort",
            strength=EvidenceStrength.OBSERVED,
            artifact_ids=["de_table"],
        )
    ]
    report = check_wellformed(bundle)
    assert "CONTRADICTION_UNADDRESSED" in {d.code for d in report.errors}


def test_contradicting_evidence_engaged_by_a_sensitivity_analysis_is_accepted() -> None:
    bundle = _good_bundle()
    bundle.alternative_explanations = []
    bundle.contradicting_evidence = [
        EvidenceRef(
            ref_id="neg",
            kind=EvidenceRefKind.STATISTIC,
            statement="the effect vanishes in the replicate cohort",
            strength=EvidenceStrength.OBSERVED,
            artifact_ids=["de_table"],
        )
    ]
    bundle.sensitivity_analyses[0].evidence_refs = ["neg"]
    report = check_wellformed(bundle)
    assert "CONTRADICTION_UNADDRESSED" not in {d.code for d in report.errors}


def test_instability_found_and_not_reported_is_a_defect() -> None:
    """Selective reporting: the sweep broke the claim, the bundle says nothing."""
    bundle = _good_bundle()
    bundle.alternative_explanations = []
    bundle.contradicting_evidence = []
    bundle.sensitivity_analyses = [
        SensitivityResult(
            analysis_id="threshold_sweep",
            parameter="fdr_threshold",
            conclusion_stable=False,
            executed=True,
        )
    ]
    report = check_wellformed(bundle)
    assert "INSTABILITY_NOT_REPORTED" in {d.code for d in report.errors}


def test_unexecuted_sensitivity_cannot_report_stability() -> None:
    bundle = _good_bundle()
    bundle.sensitivity_analyses = [
        SensitivityResult(
            analysis_id="threshold_sweep",
            parameter="fdr_threshold",
            conclusion_stable=True,
            executed=False,
        )
    ]
    report = check_wellformed(bundle)
    codes = {d.code for d in report.errors}
    assert "UNEXECUTED_SENSITIVITY_CLAIMED_STABLE" in codes
    # and it must not count toward executed sensitivity coverage
    assert report.n_executed_sensitivity == 0


def test_vacuous_uncertainty_counts_as_undeclared() -> None:
    for text in ("", "  ", "N/A", "none.", "No Uncertainty"):
        bundle = _good_bundle()
        bundle.uncertainty = text
        assert not uncertainty_declared(bundle), text
        assert "UNCERTAINTY_NOT_DECLARED" in {d.code for d in check_wellformed(bundle).errors}


def test_restating_the_claim_is_not_an_alternative_explanation() -> None:
    bundle = _good_bundle()
    bundle.alternative_explanations = ["  pathway x IS upregulated in treated samples.  "]
    assert not has_alternatives(bundle)
    warnings = {d.code for d in check_wellformed(bundle).warnings}
    assert "NO_ALTERNATIVE_EXPLANATIONS" in warnings


def test_duplicate_evidence_ids_are_rejected() -> None:
    bundle = _good_bundle()
    bundle.contradicting_evidence = [_statistical_ref(ref_id="e1")]
    bundle.alternative_explanations = ["batch effect"]
    report = check_wellformed(bundle)
    assert "DUPLICATE_EVIDENCE_REF" in {d.code for d in report.errors}


def test_missing_claim_and_provenance_are_errors_not_warnings() -> None:
    bundle = ClaimBundle(claim="", supporting_evidence=[_statistical_ref()])
    report = check_wellformed(bundle)
    codes = {d.code for d in report.errors}
    assert {"CLAIM_EMPTY", "NO_PROVENANCE", "UNCERTAINTY_NOT_DECLARED"} <= codes
    assert all(d.severity is DefectSeverity.ERROR for d in report.errors)


# ---------------------------------------------------------------------------
# traceability
# ---------------------------------------------------------------------------


def test_traceability_walks_back_to_the_raw_input() -> None:
    report = trace_claim(_good_bundle(), _artifacts())
    assert report.traceable
    assert report.lineage == ["counts", "de_table", "normalized"]
    assert report.root_artifacts == ["counts"]
    assert report.root_types == ["CountMatrix"]
    assert raw_inputs(_good_bundle(), _artifacts())[0].artifact_id == "counts"


def test_dangling_provenance_is_untraceable() -> None:
    bundle = _good_bundle()
    bundle.provenance = ["de_table", "ghost_table"]
    report = trace_claim(bundle, _artifacts())
    assert not report.traceable
    assert report.dangling_provenance == ["ghost_table"]
    assert "DANGLING_PROVENANCE" in {d.code for d in report.defects()}


def test_a_lineage_that_names_an_unrecorded_ancestor_is_a_broken_chain() -> None:
    """Worse than a short chain: the claim asserts a parent nobody recorded."""
    artifacts = _artifacts()
    artifacts["normalized"] = artifacts["normalized"].model_copy(
        update={"derived_from": ["counts", "secret_reference_panel"]}
    )
    report = trace_claim(_good_bundle(), artifacts)
    assert report.broken_lineage_links == ["secret_reference_panel"]
    assert not report.traceable


def test_evidence_citing_an_artifact_outside_the_lineage_is_flagged() -> None:
    artifacts = _artifacts()
    artifacts["side_table"] = Artifact(artifact_id="side_table", type_name="DETable")
    bundle = _good_bundle()
    bundle.supporting_evidence = [_statistical_ref(artifact_ids=["side_table"])]
    report = trace_claim(bundle, artifacts)
    assert report.unlinked_evidence_refs == ["e1"]
    assert not report.traceable


def test_required_artifact_type_missing_from_lineage_fails_traceability() -> None:
    requirement = EvidenceChainRequirement(
        requirement_id="r1",
        description="claims must trace to the raw counts and to the QC report",
        must_trace_to=["CountMatrix", "QCReport"],
    )
    report = trace_claim(_good_bundle(), _artifacts(), requirements=[requirement])
    assert report.missing_required_types == ["QCReport"]
    assert not report.traceable
    assert "REQUIRED_TYPE_NOT_TRACED" in {d.code for d in report.defects()}


def test_required_sensitivity_must_have_actually_run() -> None:
    requirement = EvidenceChainRequirement(
        requirement_id="r2",
        description="threshold sweep is mandatory",
        required_sensitivity=["fdr_threshold"],
    )
    bundle = _good_bundle()
    assert trace_claim(bundle, _artifacts(), requirements=[requirement]).traceable

    bundle.sensitivity_analyses[0].executed = False
    bundle.sensitivity_analyses[0].conclusion_stable = False
    report = trace_claim(bundle, _artifacts(), requirements=[requirement])
    assert report.missing_required_sensitivity == ["fdr_threshold"]
    assert not report.traceable


def test_audit_requires_both_halves() -> None:
    artifacts = _artifacts()
    good = audit_claim(_good_bundle(), artifacts)
    assert good.admissible
    assert good.error_codes() == []

    untraceable = _good_bundle()
    untraceable.provenance = ["ghost"]
    audit = audit_claim(untraceable, artifacts)
    assert audit.wellformedness.well_formed  # structurally fine...
    assert not audit.traceability.traceable  # ...but it cannot be traced
    assert not audit.admissible


def test_audit_is_deterministic() -> None:
    artifacts = _artifacts()
    first = audit_claim(_good_bundle(), artifacts)
    second = audit_claim(_good_bundle(), artifacts)
    assert first.model_dump() == second.model_dump()
