"""Certified, bounded configuration mutation for ECPS."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pytest

from agentcoop.compile.select import UtilityEstimate
from agentcoop.components.base import (
    AdapterRegistry,
    Invocation,
    InvocationResult,
    error_line,
    make_artifact,
    zero_clock,
)
from agentcoop.ir.artifacts import ArtifactType
from agentcoop.ir.capability import (
    CapabilityCard,
    CertificationLevel,
    ComponentKind,
    ComponentLibrary,
    CostProfile,
    IOContract,
)
from agentcoop.ir.faults import FaultClass
from agentcoop.ir.utility import UtilityVector
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, Sequence, find_term
from agentcoop.optimize.mutations import (
    CertifiedParameterDomain,
    ConfigGridMutationSource,
    ConfigMutation,
    apply_config_mutation,
    workflow_fingerprint,
)
from agentcoop.optimize.state import OptimizationCandidate
from agentcoop.probe import ProbeRunner, parameter_domain_suite


REPORT = ArtifactType(
    name="report",
    json_schema={
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    },
)


class ParameterAdapter:
    name = "writer"

    async def invoke(self, invocation: Invocation) -> InvocationResult:
        if "__agentcoop_probe_unknown_parameter__" in invocation.config:
            return InvocationResult(
                ok=False,
                errors=[error_line(FaultClass.CONFIGURATION, "unknown parameter")],
            )
        value = invocation.config.get("temperature", 0.1)
        artifact = make_artifact(
            type_name="report",
            payload={"text": f"temperature={value}"},
            producer=self.name,
        )
        return InvocationResult(
            ok=True,
            outputs={"report": artifact},
            cost=CostProfile(latency_s=0.1),
        )


def declared_card(*, parameters: dict[str, Any] | None = None) -> CapabilityCard:
    return CapabilityCard(
        name="writer",
        kind=ComponentKind.PYTHON_FUNCTION,
        io=IOContract(
            produces=[REPORT],
            parameters=parameters
            or {"temperature": {"type": "number", "default": 0.1}},
        ),
        declared_cost=CostProfile(latency_s=1.0),
    )


@dataclass(frozen=True)
class Authorized:
    library: ComponentLibrary
    card: CapabilityCard
    domain: CertifiedParameterDomain
    workflow: CompiledWorkflow
    candidate: OptimizationCandidate
    mutation: ConfigMutation


async def authorized() -> Authorized:
    adapter = ParameterAdapter()
    probe_runner = ProbeRunner(
        AdapterRegistry([adapter]), clock=zero_clock
    )
    certified = await probe_runner.certify(declared_card())
    assert certified.certification_level is CertificationLevel.CERTIFIED
    specs = parameter_domain_suite(
        certified, parameter="temperature", values=(0.1, 0.2)
    )
    certified = await probe_runner.certify(certified, specs)
    library = ComponentLibrary()
    library.add(certified)
    domain = CertifiedParameterDomain(
        domain_id="temperature-domain",
        component="writer",
        key="temperature",
        values=(0.1, 0.2),
        probe_ids=tuple(spec.probe_id for spec in specs),
    )
    term = Atomic(
        component="writer",
        subgoal_id="write",
        config={"temperature": 0.1},
    ).ensure_ids()
    workflow = CompiledWorkflow(
        workflow_id="parent-workflow",
        task_id="task",
        term=term,
        compile_notes=["compile note is not identity"],
        provenance=["compiler"],
    )
    candidate = OptimizationCandidate(
        candidate_id="parent",
        workflow=workflow,
        parent_id=None,
        mutation_id=None,
        generation=0,
        static_estimate=UtilityEstimate(
            candidate_id="parent",
            vector=UtilityVector.of(validity=1.0, evidence=1.0),
            basis={"validity": "static pass"},
        ),
    )
    mutation = ConfigMutation(
        mutation_id="raise-temperature",
        target=term.term_id,
        key="temperature",
        value=0.2,
        domain_id=domain.domain_id,
        rationale="Explore the certified alternative.",
    )
    return Authorized(
        library=library,
        card=certified,
        domain=domain,
        workflow=workflow,
        candidate=candidate,
        mutation=mutation,
    )


class TestConfigMutation:
    async def test_application_is_pure_single_key_and_preserves_evidence(self) -> None:
        setup = await authorized()

        child = apply_config_mutation(
            setup.workflow,
            setup.mutation,
            domain=setup.domain,
            library=setup.library,
        )

        original = find_term(setup.workflow.term, setup.mutation.target)
        changed = find_term(child.term, setup.mutation.target)
        assert isinstance(original, Atomic) and isinstance(changed, Atomic)
        assert original.config == {"temperature": 0.1}
        assert changed.config == {"temperature": 0.2}
        assert child.evidence == setup.workflow.evidence
        assert child.evidence is not setup.workflow.evidence

    async def test_rejects_unknown_non_atomic_noop_and_second_key(self) -> None:
        setup = await authorized()

        with pytest.raises(ValueError, match="target"):
            apply_config_mutation(
                setup.workflow,
                setup.mutation.model_copy(update={"target": "missing"}),
                domain=setup.domain,
                library=setup.library,
            )

        wrapped = setup.workflow.model_copy(
            update={
                "term": Sequence(
                    term_id="root",
                    children_terms=[setup.workflow.term],
                )
            }
        )
        with pytest.raises(ValueError, match="Atomic"):
            apply_config_mutation(
                wrapped,
                setup.mutation.model_copy(update={"target": "root"}),
                domain=setup.domain,
                library=setup.library,
            )

        with pytest.raises(ValueError, match="no-op"):
            apply_config_mutation(
                setup.workflow,
                setup.mutation.model_copy(update={"value": 0.1}),
                domain=setup.domain,
                library=setup.library,
            )

        with pytest.raises(ValueError, match="key"):
            apply_config_mutation(
                setup.workflow,
                setup.mutation.model_copy(update={"key": "other"}),
                domain=setup.domain,
                library=setup.library,
            )

    async def test_fingerprint_includes_config_but_excludes_audit_metadata(self) -> None:
        setup = await authorized()
        same_design = setup.workflow.model_copy(
            update={
                "workflow_id": "different-id",
                "compile_notes": ["different note"],
                "provenance": ["different provenance"],
            },
            deep=True,
        )
        changed = apply_config_mutation(
            setup.workflow,
            setup.mutation,
            domain=setup.domain,
            library=setup.library,
        )

        assert workflow_fingerprint(setup.workflow) == workflow_fingerprint(same_design)
        assert workflow_fingerprint(setup.workflow) != workflow_fingerprint(changed)
        assert len(workflow_fingerprint(changed)) == 64

    async def test_noncanonical_config_and_value_are_rejected(self) -> None:
        setup = await authorized()
        unsafe_workflow = setup.workflow.model_copy(
            update={
                "term": setup.workflow.term.model_copy(
                    update={"config": {"temperature": object()}}
                )
            }
        )
        with pytest.raises(ValueError, match="canonical JSON"):
            workflow_fingerprint(unsafe_workflow)

        with pytest.raises(ValueError, match="canonical JSON|finite"):
            apply_config_mutation(
                setup.workflow,
                setup.mutation.model_copy(update={"value": math.nan}),
                domain=setup.domain,
                library=setup.library,
            )


class TestConfigGridMutationSource:
    async def test_proposals_are_deterministic_deduplicated_and_auditable(self) -> None:
        setup = await authorized()
        duplicate = setup.mutation.model_copy(update={"mutation_id": "duplicate"})
        source = ConfigGridMutationSource(
            setup.library,
            (setup.domain,),
            (duplicate, setup.mutation),
        )

        proposals = source.propose(
            (setup.candidate,), seen_fingerprints=set()
        )
        repeated = source.propose(
            (setup.candidate,), seen_fingerprints=set()
        )

        assert proposals == repeated
        assert len(proposals) == 1
        proposal = proposals[0]
        fingerprint = workflow_fingerprint(proposal.candidate.workflow)
        assert proposal.candidate.candidate_id == f"config::{fingerprint}"
        assert proposal.candidate.workflow.workflow_id == f"ecps::task::{fingerprint}"
        assert proposal.candidate.parent_id == "parent"
        assert proposal.candidate.generation == 1
        assert proposal.candidate.workflow.evidence == setup.workflow.evidence
        assert proposal.candidate.workflow.evidence is not setup.workflow.evidence
        assert proposal.record.child_id == proposal.candidate.candidate_id
        assert proposal.record.domain_values == setup.domain.values
        assert proposal.record.probe_ids == setup.domain.probe_ids

        assert source.propose(
            (setup.candidate,), seen_fingerprints={fingerprint}
        ) == ()

    async def test_rejects_uncertified_unknown_parameter_and_out_of_domain(self) -> None:
        setup = await authorized()
        uncertified_library = ComponentLibrary()
        uncertified_library.add(declared_card())
        with pytest.raises(ValueError, match="CERTIFIED"):
            ConfigGridMutationSource(
                uncertified_library, (setup.domain,), (setup.mutation,)
            )

        unknown = setup.domain.model_copy(update={"key": "unknown"})
        with pytest.raises(ValueError, match="declared"):
            ConfigGridMutationSource(
                setup.library,
                (unknown,),
                (setup.mutation.model_copy(update={"key": "unknown"}),),
            )

        with pytest.raises(ValueError, match="domain"):
            ConfigGridMutationSource(
                setup.library,
                (setup.domain,),
                (setup.mutation.model_copy(update={"value": 0.9}),),
            )

    async def test_rejects_missing_failed_and_wrong_kind_probe(self) -> None:
        setup = await authorized()
        probes = list(setup.card.empirical.probes)
        target_id = setup.domain.probe_ids[0]

        for mode in ("missing", "failed", "wrong-kind"):
            altered = []
            for probe in probes:
                if probe.probe_id != target_id:
                    altered.append(probe)
                elif mode == "failed":
                    altered.append(
                        probe.model_copy(
                            update={
                                "passed": False,
                                "evidence": {
                                    **probe.evidence,
                                    "contract_preserving": False,
                                },
                            }
                        )
                    )
                elif mode == "wrong-kind":
                    altered.append(probe.model_copy(update={"kind": "reachable"}))
            bad_card = setup.card.model_copy(
                update={
                    "empirical": setup.card.empirical.model_copy(
                        update={"probes": altered}
                    )
                }
            )
            library = ComponentLibrary()
            library.add(bad_card)

            with pytest.raises(ValueError, match="probe"):
                ConfigGridMutationSource(
                    library, (setup.domain,), (setup.mutation,)
                )

    async def test_reserved_contract_key_is_never_mutable(self) -> None:
        setup = await authorized()
        for key in (
            "component",
            "output_schema",
            "response_type",
            "runtime_facets",
            "binding_route",
            "merge_strategy",
            "verifier_name",
            "condition_expression",
        ):
            reserved_domain = CertifiedParameterDomain(
                domain_id=f"reserved-{key}",
                component="writer",
                key=key,
                values=("first", "second"),
                probe_ids=("placeholder",),
            )
            with pytest.raises(ValueError, match="reserved"):
                ConfigGridMutationSource(
                    setup.library,
                    (reserved_domain,),
                    (
                        ConfigMutation(
                            mutation_id=f"reserved-{key}",
                            target=setup.mutation.target,
                            key=key,
                            value="second",
                            domain_id=f"reserved-{key}",
                            rationale="must be rejected",
                        ),
                    ),
                )

    def test_domain_rejects_noncanonical_or_duplicate_values(self) -> None:
        with pytest.raises(ValueError, match="canonical JSON"):
            CertifiedParameterDomain(
                domain_id="bad",
                component="writer",
                key="temperature",
                values=(object(),),
                probe_ids=("probe",),
            )
        with pytest.raises(ValueError, match="unique"):
            CertifiedParameterDomain(
                domain_id="duplicate",
                component="writer",
                key="temperature",
                values=(0.1, 0.1),
                probe_ids=("probe",),
            )
