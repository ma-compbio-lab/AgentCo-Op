"""Capability certification — the ladder from README claim to earned trust."""

from __future__ import annotations

import pytest

from agentcoop.ir.artifacts import ArtifactType
from agentcoop.ir.capability import (
    CapabilityCard,
    CertificationLevel,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    EmpiricalRecord,
    EnvironmentContract,
    FailureSignature,
    IOContract,
    ProbeOutcome,
    ReliabilityPosterior,
)


def probe(kind: str, passed: bool = True, **facets: str) -> ProbeOutcome:
    return ProbeOutcome(
        probe_id=f"P-{kind}",
        kind=kind,
        passed=passed,
        observed_facets={"gene_set": dict(facets)} if facets else {},
    )


def card(*probes: ProbeOutcome, reliability: ReliabilityPosterior | None = None) -> CapabilityCard:
    return CapabilityCard(
        name="geneagent",
        kind=ComponentKind.EXTERNAL_REPO,
        functional_capabilities=["gene_set_interpretation"],
        io=IOContract(
            consumes=[ArtifactType(name="gene_set")],
            produces=[ArtifactType(name="report", facets={"format": "markdown"})],
        ),
        empirical=EmpiricalRecord(
            probes=list(probes), reliability=reliability or ReliabilityPosterior()
        ),
    )


ALL_PROBES = ("reachable", "schema", "smoke", "invalid_input", "resource")


class TestCertificationLadder:
    def test_no_contract_at_all_is_unknown(self) -> None:
        bare = CapabilityCard(name="mystery", kind=ComponentKind.EXTERNAL_REPO)
        assert bare.certification_level is CertificationLevel.UNKNOWN

    def test_declared_contract_without_probes_stays_declared(self) -> None:
        """A README is not evidence. This is the level a v1-style tag match
        would have been operating at."""
        assert card().certification_level is CertificationLevel.DECLARED

    def test_unreachable_component_cannot_climb(self) -> None:
        c = card(probe("reachable", passed=False), probe("schema"), probe("smoke"))
        assert c.certification_level is CertificationLevel.DECLARED

    def test_reachable_only_is_reachable(self) -> None:
        assert card(probe("reachable")).certification_level is CertificationLevel.REACHABLE

    def test_smoke_without_schema_is_still_only_reachable(self) -> None:
        c = card(probe("reachable"), probe("smoke"))
        assert c.certification_level is CertificationLevel.REACHABLE

    def test_positive_probes_reach_probed_but_not_certified(self) -> None:
        c = card(probe("reachable"), probe("schema"), probe("smoke"))
        assert c.certification_level is CertificationLevel.PROBED

    def test_certification_requires_the_negative_probes(self) -> None:
        """The property that matters most — refusing bad input instead of
        silently succeeding — is only established by negative testing."""
        without = card(probe("reachable"), probe("schema"), probe("smoke"), probe("resource"))
        assert without.certification_level is CertificationLevel.PROBED

        with_negative = card(*(probe(k) for k in ALL_PROBES))
        assert with_negative.certification_level is CertificationLevel.CERTIFIED

    def test_failing_invalid_input_probe_blocks_certification(self) -> None:
        c = card(
            probe("reachable"), probe("schema"), probe("smoke"),
            probe("invalid_input", passed=False), probe("resource"),
        )
        assert c.certification_level is CertificationLevel.PROBED

    def test_trusted_needs_a_real_track_record(self) -> None:
        certified = card(*(probe(k) for k in ALL_PROBES))
        assert certified.certification_level is CertificationLevel.CERTIFIED

        lucky = card(*(probe(k) for k in ALL_PROBES), reliability=ReliabilityPosterior(alpha=3, beta=1))
        assert lucky.certification_level is CertificationLevel.CERTIFIED, "2 successes is not a record"

        seasoned = card(
            *(probe(k) for k in ALL_PROBES), reliability=ReliabilityPosterior(alpha=19, beta=2)
        )
        assert seasoned.certification_level is CertificationLevel.TRUSTED

    def test_poor_reliability_does_not_reach_trusted(self) -> None:
        unreliable = card(
            *(probe(k) for k in ALL_PROBES), reliability=ReliabilityPosterior(alpha=6, beta=8)
        )
        assert unreliable.certification_level is CertificationLevel.CERTIFIED

    def test_levels_are_ordered_for_comparison(self) -> None:
        assert CertificationLevel.PROBED >= CertificationLevel.REACHABLE
        assert CertificationLevel.DECLARED < CertificationLevel.CERTIFIED


class TestObservationBeatsDocumentation:
    def test_probe_observed_facets_override_declared_ones(self) -> None:
        """If the manifest says HGNC and the probe saw Ensembl, believe the probe."""
        c = card()
        c.io.produces = [ArtifactType(name="gene_set", facets={"namespace": "HGNC"})]
        c.empirical.confirmed_facets = {"gene_set": {"namespace": "ENSEMBL"}}
        assert c.effective_facets("gene_set")["namespace"] == "ENSEMBL"
        assert c.output_type("gene_set").facets["namespace"] == "ENSEMBL"

    def test_declared_facets_survive_when_unobserved(self) -> None:
        c = card()
        c.io.produces = [ArtifactType(name="gene_set", facets={"namespace": "HGNC", "organism": "human"})]
        c.empirical.confirmed_facets = {"gene_set": {"namespace": "ENSEMBL"}}
        assert c.effective_facets("gene_set")["organism"] == "human"

    def test_output_type_is_none_for_unproduced_artifact(self) -> None:
        assert card().output_type("nonexistent") is None

    def test_observed_cost_supersedes_declared_cost(self) -> None:
        c = card()
        c.declared_cost = CostProfile(usd=0.01)
        assert c.expected_cost().usd == 0.01
        c.empirical.observed_cost = CostProfile(usd=0.75)
        assert c.expected_cost().usd == 0.75


class TestReliabilityPosterior:
    def test_uninformative_prior_is_not_optimistic(self) -> None:
        fresh = ReliabilityPosterior()
        assert fresh.mean == 0.5
        assert fresh.n_observations == 0

    def test_lcb_penalizes_thin_evidence(self) -> None:
        """One success must not outrank a long solid record."""
        lucky = ReliabilityPosterior().update(successes=1)
        solid = ReliabilityPosterior().update(successes=18, failures=2)
        assert lucky.mean > 0.6
        assert lucky.lcb < solid.lcb

    def test_interval_is_clamped_to_unit_range(self) -> None:
        lo, hi = ReliabilityPosterior().update(successes=200).credible_interval()
        assert 0.0 <= lo <= hi <= 1.0

    def test_update_is_pure(self) -> None:
        base = ReliabilityPosterior()
        base.update(successes=5)
        assert base.alpha == 1.0


class TestEnvironmentConflicts:
    def test_matching_environments_do_not_conflict(self) -> None:
        a = EnvironmentContract(python="3.11", pip_packages=["numpy==1.26.0"])
        b = EnvironmentContract(python="3.11", pip_packages=["numpy==1.26.0"])
        assert a.conflicts_with(b) == []

    def test_python_pin_conflict_detected(self) -> None:
        a = EnvironmentContract(python="3.9")
        b = EnvironmentContract(python="3.11")
        assert any("python" in c for c in a.conflicts_with(b))

    def test_pip_version_conflict_detected(self) -> None:
        """The failure that shows up 20 minutes into a run as an ImportError."""
        a = EnvironmentContract(pip_packages=["torch==2.1.0", "numpy==1.26.0"])
        b = EnvironmentContract(pip_packages=["torch==1.13.0"])
        conflicts = a.conflicts_with(b)
        assert len(conflicts) == 1 and "torch" in conflicts[0]

    def test_unpinned_packages_are_not_treated_as_conflicts(self) -> None:
        a = EnvironmentContract(pip_packages=["numpy"])
        b = EnvironmentContract(pip_packages=["numpy>=1.20"])
        assert a.conflicts_with(b) == []

    def test_cuda_conflict_detected(self) -> None:
        a = EnvironmentContract(cuda="11.8")
        b = EnvironmentContract(cuda="12.1")
        assert any("cuda" in c for c in a.conflicts_with(b))


class TestCostProfile:
    def test_addition_sums_but_takes_max_memory(self) -> None:
        a = CostProfile(usd=1.0, tokens=100, peak_memory_gb=4.0, latency_s=2.0)
        b = CostProfile(usd=2.0, tokens=50, peak_memory_gb=8.0, latency_s=3.0)
        total = a + b
        assert total.usd == 3.0 and total.tokens == 150 and total.latency_s == 5.0
        assert total.peak_memory_gb == 8.0, "peak memory is not additive"

    def test_scaling_preserves_peak_memory(self) -> None:
        scaled = CostProfile(usd=1.0, tokens=100, peak_memory_gb=4.0).scaled(0.1)
        assert scaled.usd == pytest.approx(0.1) and scaled.tokens == 10
        assert scaled.peak_memory_gb == 4.0


class TestComponentLibrary:
    def test_lookup_and_filtering(self) -> None:
        lib = ComponentLibrary()
        certified = card(*(probe(k) for k in ALL_PROBES))
        lib.add(certified)
        declared = card()
        declared.name = "unprobed"
        lib.add(declared)

        assert lib.names() == ["geneagent", "unprobed"]
        assert [c.name for c in lib.producing("report")] == ["geneagent", "unprobed"]
        assert [c.name for c in lib.with_capability("gene_set_interpretation")] == [
            "geneagent",
            "unprobed",
        ]
        assert [c.name for c in lib.certified_at_least(CertificationLevel.CERTIFIED)] == ["geneagent"]

    def test_require_raises_for_unknown_component(self) -> None:
        with pytest.raises(KeyError):
            ComponentLibrary().require("ghost")


class TestSilentFailureExposure:
    def test_silent_failure_mode_is_surfaced(self) -> None:
        c = card()
        assert not c.has_silent_failure_mode()
        c.failure_profile.append(
            FailureSignature(
                name="empty_on_bad_symbols",
                silent=True,
                note="returns an empty gene set with exit code 0 on unmapped identifiers",
            )
        )
        assert c.has_silent_failure_mode()
