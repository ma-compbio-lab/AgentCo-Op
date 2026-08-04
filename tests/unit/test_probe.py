"""Probe layer: does certification actually require what it claims to require?

The property under test throughout is that the certification ladder cannot be
climbed by declaration. Most of these tests build a component that is *nice* in
every visible way — it runs, it emits the right shape, it never crashes — and
check that it is still refused CERTIFIED because it accepts garbage.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from agentcoop.components.base import (
    AdapterRegistry,
    Invocation,
    InvocationResult,
    error_line,
    make_artifact,
    zero_clock,
)
from agentcoop.ir.artifacts import ArtifactType, TypeRegistry
from agentcoop.ir.capability import (
    CapabilityCard,
    CertificationLevel,
    ComponentKind,
    CostProfile,
    IOContract,
    ReliabilityPosterior,
)
from agentcoop.ir.checks import CheckStatus
from agentcoop.ir.faults import FaultClass
from agentcoop.probe import (
    Corruption,
    ProbeRunner,
    ProbeSpec,
    applicable_corruptions,
    certification_report,
    corrupt_payload,
    derived_level,
    explain_level,
    first_blocking_step,
    probe_artifact,
    standard_suite,
    synthesize_payload,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GENE_SET = ArtifactType(
    name="gene_set",
    json_schema={
        "type": "object",
        "required": ["genes"],
        "properties": {
            "genes": {"type": "array", "items": {"type": "string"}},
            "n": {"type": "integer"},
        },
    },
    required_facets=["namespace", "organism"],
    facets={"namespace": "HGNC", "organism": "human"},
)

ENRICHMENT = ArtifactType(
    name="enrichment",
    json_schema={
        "type": "object",
        "required": ["terms"],
        "properties": {"terms": {"type": "array"}},
    },
    required_facets=["namespace"],
    facets={"namespace": "GO"},
)


def card(**overrides: Any) -> CapabilityCard:
    base = CapabilityCard(
        name="enrichr",
        kind=ComponentKind.EXTERNAL_REPO,
        io=IOContract(consumes=[GENE_SET], produces=[ENRICHMENT]),
        declared_cost=CostProfile(latency_s=5.0),
    )
    return base.model_copy(update=overrides, deep=True)


def registry() -> TypeRegistry:
    reg = TypeRegistry()
    reg.register_type(GENE_SET)
    reg.register_type(ENRICHMENT)
    return reg


class FakeAdapter:
    """A component whose every behaviour is dialled in by the test.

    Defaults describe the dangerous component this system exists to catch: it
    always succeeds, it always emits something plausible, and it never
    complains about anything.
    """

    def __init__(
        self,
        name: str = "enrichr",
        *,
        rejects_invalid: bool = False,
        payload: Any = None,
        deterministic: bool = True,
        cost: Optional[CostProfile] = None,
        fail_with: Optional[FaultClass] = None,
        emit: bool = True,
        facets: Optional[dict[str, str]] = None,
    ) -> None:
        self.name = name
        self.rejects_invalid = rejects_invalid
        self.payload = payload if payload is not None else {"terms": ["GO:0006915"]}
        self.deterministic = deterministic
        self.cost = cost or CostProfile()
        self.fail_with = fail_with
        self.emit = emit
        self.facets = facets if facets is not None else {"namespace": "GO"}
        self.calls: list[Invocation] = []

    def _looks_invalid(self, inv: Invocation) -> bool:
        if any(k.startswith("__agentcoop_probe") for k in inv.config):
            return True
        for art in inv.inputs.values():
            payload = art.payload
            if payload is None or payload == {} or payload == "":
                return True
            if isinstance(payload, dict):
                if "genes" not in payload:
                    return True
                if not isinstance(payload.get("genes"), list):
                    return True
            if any(v.startswith("invalid_") for v in art.facets.values()):
                return True
        return False

    async def invoke(self, inv: Invocation) -> InvocationResult:
        self.calls.append(inv)
        if self.fail_with is not None:
            return InvocationResult(
                ok=False, errors=[error_line(self.fail_with, "adapter says no")]
            )
        if self.rejects_invalid and self._looks_invalid(inv):
            return InvocationResult(
                ok=False,
                errors=[error_line(FaultClass.ARTIFACT_CONTRACT, "input rejected")],
            )
        if not self.emit:
            return InvocationResult(ok=True, outputs={}, cost=self.cost)

        payload = self.payload
        if not self.deterministic:
            payload = {**self.payload, "nonce": len(self.calls)}
        art = make_artifact(
            type_name="enrichment", payload=payload, facets=self.facets, producer=self.name
        )
        return InvocationResult(
            ok=True,
            outputs={"enrichment": art},
            cost=self.cost,
            observed_facets={"enrichment": dict(self.facets)},
        )


def runner(adapter: FakeAdapter) -> ProbeRunner:
    return ProbeRunner(
        AdapterRegistry([adapter]), registry=registry(), clock=zero_clock
    )


# ---------------------------------------------------------------------------
# Payload synthesis
# ---------------------------------------------------------------------------


class TestSynthesis:
    def test_synthesized_payload_satisfies_the_schema_it_came_from(self) -> None:
        from agentcoop.ir.artifacts import validate_payload

        assert validate_payload(synthesize_payload(GENE_SET), GENE_SET) == []

    def test_synthesis_is_deterministic(self) -> None:
        assert synthesize_payload(GENE_SET) == synthesize_payload(GENE_SET)

    def test_synthesis_fills_optional_properties_too(self) -> None:
        """Probing only the required subset would miss dropped optional fields."""
        payload = synthesize_payload(GENE_SET)
        assert "genes" in payload and "n" in payload

    def test_each_corruption_actually_corrupts(self) -> None:
        from agentcoop.ir.artifacts import validate_payload

        base = synthesize_payload(GENE_SET)
        for corruption in (Corruption.MISSING_REQUIRED, Corruption.WRONG_TYPE):
            payload, _, described = corrupt_payload(base, GENE_SET, corruption)
            assert validate_payload(payload, GENE_SET), corruption
            assert described

    def test_facet_violation_is_invisible_to_the_schema(self) -> None:
        """The corruption a structural validator cannot possibly catch."""
        from agentcoop.ir.artifacts import validate_payload

        base = synthesize_payload(GENE_SET)
        payload, facets, _ = corrupt_payload(base, GENE_SET, Corruption.FACET_VIOLATION)
        assert validate_payload(payload, GENE_SET) == []
        assert facets == {"namespace": "invalid_namespace", "organism": "invalid_organism"}

    def test_corruptions_are_skipped_when_inapplicable(self) -> None:
        bare = ArtifactType(name="blob")
        assert applicable_corruptions(bare) == [Corruption.EMPTY]
        assert Corruption.FACET_VIOLATION in applicable_corruptions(GENE_SET)


# ---------------------------------------------------------------------------
# Suite construction
# ---------------------------------------------------------------------------


class TestSuite:
    def test_suite_covers_every_kind(self) -> None:
        kinds = {s.kind for s in standard_suite(card(), registry=registry())}
        assert kinds == {
            "reachable",
            "schema",
            "smoke",
            "invalid_input",
            "determinism",
            "resource",
        }

    def test_probe_ids_are_stable_across_calls(self) -> None:
        a = [s.probe_id for s in standard_suite(card(), registry=registry())]
        b = [s.probe_id for s in standard_suite(card(), registry=registry())]
        assert a == b and len(set(a)) == len(a)

    def test_every_applicable_corruption_gets_its_own_probe(self) -> None:
        """No silent cap: a suite that tested one corruption would over-claim."""
        specs = [
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        ]
        corruptions = {s.expectations["corruption"] for s in specs}
        assert corruptions == {c.value for c in applicable_corruptions(GENE_SET)}

    def test_zero_input_components_are_still_probed_for_refusal(self) -> None:
        source = card(io=IOContract(produces=[ENRICHMENT]))
        specs = [
            s for s in standard_suite(source, registry=registry()) if s.kind == "invalid_input"
        ]
        assert len(specs) == 1
        assert specs[0].expectations["corruption"] == "unknown_config_parameter"
        assert specs[0].config

    def test_registry_required_facets_are_merged_into_the_probe(self) -> None:
        """A component that under-declares a type is probed against the real one."""
        thin = card(
            io=IOContract(
                consumes=[ArtifactType(name="gene_set", facets={"namespace": "HGNC"})],
                produces=[ENRICHMENT],
            )
        )
        specs = [
            s for s in standard_suite(thin, registry=registry()) if s.kind == "invalid_input"
        ]
        facet_specs = [
            s for s in specs if s.expectations["corruption"] == Corruption.FACET_VIOLATION.value
        ]
        assert facet_specs, "registry required_facets should make this corruption applicable"

    def test_declared_smoke_inputs_are_used_when_available(self) -> None:
        c = card(binding={"smoke_inputs": {"gene_set": {"genes": ["TP53"], "n": 1}}})
        smoke = next(s for s in standard_suite(c, registry=registry()) if s.kind == "smoke")
        assert smoke.expectations["input_source"] == "declared"
        assert smoke.inputs["gene_set"].payload["genes"] == ["TP53"]

    def test_synthetic_smoke_does_not_check_deep_emptiness(self) -> None:
        smoke = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "smoke"
        )
        assert smoke.expectations["input_source"] == "synthetic"


# ---------------------------------------------------------------------------
# The invalid_input probe — the crux
# ---------------------------------------------------------------------------


class TestInvalidInputProbe:
    async def test_rejecting_component_passes(self) -> None:
        adapter = FakeAdapter(rejects_invalid=True)
        specs = [
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        ]
        outcomes = await runner(adapter).run_suite(card(), specs)
        assert all(o.passed for o in outcomes)
        assert all(not o.evidence["silent"] for o in outcomes)

    async def test_silently_succeeding_component_fails(self) -> None:
        """Exit code 0 with a plausible answer is the worst outcome, not the best."""
        adapter = FakeAdapter(rejects_invalid=False)
        specs = [
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        ]
        outcomes = await runner(adapter).run_suite(card(), specs)
        assert all(not o.passed for o in outcomes)
        assert all(o.evidence["silent"] for o in outcomes)
        assert {o.evidence["silent_mode"] for o in outcomes} == {"plausible_output"}

    async def test_empty_output_is_a_distinct_silent_mode(self) -> None:
        adapter = FakeAdapter(payload={})
        spec = next(
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert not outcome.passed
        assert outcome.evidence["silent_mode"] == "empty_output"

    async def test_no_output_at_all_is_still_a_silent_failure(self) -> None:
        adapter = FakeAdapter(emit=False)
        spec = next(
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert not outcome.passed
        assert outcome.evidence["silent_mode"] == "no_output"

    async def test_the_specific_defect_accepted_is_named(self) -> None:
        """'invalid input was accepted' would not tell anyone what to fix."""
        adapter = FakeAdapter()
        specs = [
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        ]
        outcomes = await runner(adapter).run_suite(card(), specs)
        details = " ".join(o.detail for o in outcomes)
        assert "missing the required property 'genes'" in details
        assert "impossible facets" in details

    async def test_adapter_crash_is_inconclusive_not_a_pass(self) -> None:
        """A refusal we could not observe is not a refusal."""

        class Exploding:
            name = "enrichr"

            async def invoke(self, inv: Invocation) -> InvocationResult:
                raise RuntimeError("boom")

        r = ProbeRunner(AdapterRegistry([Exploding()]), registry=registry(), clock=zero_clock)
        spec = next(
            s
            for s in standard_suite(card(), registry=registry())
            if s.kind == "invalid_input"
        )
        outcome = await r.run_probe(card(), spec)
        assert not outcome.passed
        assert outcome.evidence["inconclusive"] is True
        assert outcome.evidence["silent"] is False


# ---------------------------------------------------------------------------
# The other probes
# ---------------------------------------------------------------------------


class TestOtherProbes:
    async def test_reachable_distinguishes_unreachable_from_unhappy(self) -> None:
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "reachable"
        )
        unhappy = await runner(FakeAdapter(fail_with=FaultClass.ARTIFACT_CONTRACT)).run_probe(
            card(), spec
        )
        missing_dep = await runner(FakeAdapter(fail_with=FaultClass.ENVIRONMENT)).run_probe(
            card(), spec
        )
        assert unhappy.passed, "rejecting our input still proves the entrypoint resolves"
        assert not missing_dep.passed

    async def test_missing_adapter_fails_reachability(self) -> None:
        r = ProbeRunner(AdapterRegistry([]), registry=registry(), clock=zero_clock)
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "reachable"
        )
        outcome = await r.run_probe(card(), spec)
        assert not outcome.passed
        assert "could not be invoked" in outcome.detail

    async def test_schema_probe_catches_a_missing_required_facet(self) -> None:
        adapter = FakeAdapter(facets={})
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "schema"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert not outcome.passed
        assert "required facet 'namespace' is absent" in outcome.detail

    async def test_schema_probe_catches_a_contradicted_facet(self) -> None:
        """The manifest says GO, the tool emits KEGG."""
        adapter = FakeAdapter(facets={"namespace": "KEGG"})
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "schema"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert not outcome.passed
        assert "declared 'GO' but 'KEGG' was emitted" in outcome.detail

    async def test_schema_probe_records_the_observation_even_when_it_fails(self) -> None:
        adapter = FakeAdapter(facets={"namespace": "KEGG"})
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "schema"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert outcome.observed_facets == {"enrichment": {"namespace": "KEGG"}}

    async def test_smoke_fails_on_an_empty_payload(self) -> None:
        adapter = FakeAdapter(payload={})
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "smoke"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert not outcome.passed
        assert "empty payload" in outcome.detail

    async def test_deep_emptiness_only_fails_on_a_declared_input(self) -> None:
        """{"terms": []} may be the right answer to a synthetic question."""
        adapter = FakeAdapter(payload={"terms": []})
        synthetic = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "smoke"
        )
        assert (await runner(adapter).run_probe(card(), synthetic)).passed

        real = card(binding={"smoke_inputs": {"gene_set": {"genes": ["TP53"], "n": 1}}})
        declared = next(
            s for s in standard_suite(real, registry=registry()) if s.kind == "smoke"
        )
        outcome = await runner(adapter).run_probe(real, declared)
        assert not outcome.passed
        assert "empty where it matters" in outcome.detail

    async def test_determinism_compares_content_hashes(self) -> None:
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "determinism"
        )
        assert (await runner(FakeAdapter(deterministic=True)).run_probe(card(), spec)).passed
        drifting = await runner(FakeAdapter(deterministic=False)).run_probe(card(), spec)
        assert not drifting.passed
        assert drifting.evidence["differing_outputs"] == ["enrichment"]

    async def test_undemonstrated_determinism_is_recorded_as_non_determinism(self) -> None:
        adapter = FakeAdapter(fail_with=FaultClass.TOOL_FAILURE)
        certified = await runner(adapter).certify(card())
        assert certified.behavior.deterministic is False
        outcome = next(p for p in certified.empirical.probes if p.kind == "determinism")
        assert outcome.evidence["inconclusive"] is True

    async def test_resource_probe_catches_a_dishonest_budget(self) -> None:
        greedy = FakeAdapter(cost=CostProfile(latency_s=500.0))
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "resource"
        )
        outcome = await runner(greedy).run_probe(card(), spec)
        assert not outcome.passed
        assert "exceeding the stated budget" in outcome.detail

    async def test_clean_refusal_honours_the_budget(self) -> None:
        adapter = FakeAdapter(fail_with=FaultClass.TOOL_FAILURE)
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "resource"
        )
        outcome = await runner(adapter).run_probe(card(), spec)
        assert outcome.passed
        assert outcome.evidence["clean_refusal"] is True

    async def test_runner_termination_is_not_credited_as_compliance(self) -> None:
        class Hanging:
            name = "enrichr"

            async def invoke(self, inv: Invocation) -> InvocationResult:
                import asyncio

                await asyncio.sleep(10)
                return InvocationResult(ok=True)

        r = ProbeRunner(AdapterRegistry([Hanging()]), registry=registry(), clock=zero_clock)
        spec = next(
            s for s in standard_suite(card(), registry=registry()) if s.kind == "resource"
        )
        outcome = await r.run_probe(card(), spec.model_copy(update={"timeout_s": 0.01}))
        assert not outcome.passed
        assert outcome.evidence["terminated_by_runner"] is True


# ---------------------------------------------------------------------------
# Certification
# ---------------------------------------------------------------------------


class TestCertification:
    async def test_a_nice_but_credulous_component_cannot_be_certified(self) -> None:
        """It runs, it emits the right shape, it never crashes — and it lies."""
        certified = await runner(FakeAdapter(rejects_invalid=False)).certify(card())
        assert certified.certification_level == CertificationLevel.PROBED
        assert certified.has_silent_failure_mode()

    async def test_a_strict_component_reaches_certified(self) -> None:
        certified = await runner(FakeAdapter(rejects_invalid=True)).certify(card())
        assert certified.certification_level == CertificationLevel.CERTIFIED

    async def test_certify_does_not_mutate_the_input_card(self) -> None:
        original = card()
        await runner(FakeAdapter(rejects_invalid=True)).certify(original)
        assert original.empirical.probes == []
        assert original.certification_level == CertificationLevel.DECLARED

    async def test_probes_alone_never_grant_trusted(self) -> None:
        """TRUSTED is earned in deployment, not on a probe bench."""
        certified = await runner(FakeAdapter(rejects_invalid=True)).certify(card())
        assert certified.empirical.reliability.n_observations == 0
        assert certified.certification_level < CertificationLevel.TRUSTED

    async def test_reliability_history_lifts_a_certified_card_to_trusted(self) -> None:
        certified = await runner(FakeAdapter(rejects_invalid=True)).certify(card())
        with_history = certified.model_copy(deep=True)
        with_history.empirical.reliability = ReliabilityPosterior(alpha=31, beta=2)
        assert with_history.certification_level == CertificationLevel.TRUSTED

    async def test_observed_facets_are_promoted_into_the_card(self) -> None:
        adapter = FakeAdapter(rejects_invalid=True, facets={"namespace": "KEGG"})
        certified = await runner(adapter).certify(card())
        assert certified.effective_facets("enrichment")["namespace"] == "KEGG"

    async def test_reprobing_one_spec_preserves_the_rest_of_the_record(self) -> None:
        full = await runner(FakeAdapter(rejects_invalid=True)).certify(card())
        before = len(full.empirical.probes)
        one = next(s for s in standard_suite(card(), registry=registry()) if s.kind == "smoke")
        again = await runner(FakeAdapter(rejects_invalid=True)).certify(full, [one])
        assert len(again.empirical.probes) == before
        assert again.certification_level == CertificationLevel.CERTIFIED

    async def test_observed_cost_is_the_maximum_not_the_mean(self) -> None:
        adapter = FakeAdapter(rejects_invalid=True, cost=CostProfile(latency_s=2.0, usd=0.5))
        certified = await runner(adapter).certify(card())
        assert certified.expected_cost().latency_s == 2.0
        assert certified.expected_cost().usd == 0.5


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def test_unrun_probes_report_unavailable_not_pass(self) -> None:
        report = certification_report(card())
        kinds = {
            r.check_id: r.status
            for r in report.results
            if r.check_id.startswith("certification.") and "." in r.check_id
        }
        assert kinds["certification.invalid_input"] == CheckStatus.UNAVAILABLE
        assert CheckStatus.PASS not in kinds.values()

    def test_unavailable_negative_probes_do_not_satisfy_hard_constraints(self) -> None:
        assert certification_report(card()).hard_constraints_satisfied is False

    async def test_a_silent_failure_gets_its_own_attributable_check(self) -> None:
        certified = await runner(FakeAdapter()).certify(card())
        report = certification_report(certified)
        silent = [
            r for r in report.results if r.check_id.startswith("certification.silent_failure.")
        ]
        assert silent
        assert all(r.subject == "enrichr" and r.subject_kind == "component" for r in silent)
        assert all(r.blocking for r in silent)

    async def test_explanation_and_level_cannot_drift_apart(self) -> None:
        cards = [card()]
        for adapter in (
            FakeAdapter(rejects_invalid=True),
            FakeAdapter(rejects_invalid=False),
            FakeAdapter(fail_with=FaultClass.ENVIRONMENT),
            FakeAdapter(facets={}),
            FakeAdapter(payload={}),
        ):
            cards.append(await runner(adapter).certify(card()))
        cards.append(card(io=IOContract()))
        for c in cards:
            assert derived_level(c) == c.certification_level, explain_level(c)

    def test_explanation_names_the_rung_that_blocked(self) -> None:
        blocker = first_blocking_step(card())
        assert blocker is not None
        assert blocker.level == CertificationLevel.REACHABLE
        assert "no probe has ever been executed" in " ".join(blocker.blockers)

    async def test_explanation_says_what_would_move_it_up(self) -> None:
        certified = await runner(FakeAdapter()).certify(card())
        text = explain_level(certified)
        assert "PROBED" in text
        assert "to reach CERTIFIED" in text
        assert "refuses malformed input" in text

    def test_a_card_with_no_contract_is_unknown(self) -> None:
        assert card(io=IOContract()).certification_level == CertificationLevel.UNKNOWN
        assert derived_level(card(io=IOContract())) == CertificationLevel.UNKNOWN


@pytest.mark.parametrize(
    "corruption",
    [c for c in Corruption],
)
def test_every_corruption_produces_a_usable_artifact(corruption: Corruption) -> None:
    payload, facets, described = corrupt_payload(
        synthesize_payload(GENE_SET), GENE_SET, corruption
    )
    art = probe_artifact(GENE_SET, payload, facet_overrides=facets)
    assert art.artifact_id and art.content_hash
    assert "synthetic_probe_input" in art.notes
    assert described
