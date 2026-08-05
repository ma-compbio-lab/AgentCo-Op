"""Probe-authorized, bounded mutations of Atomic component configuration."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Any, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentcoop.ir.capability import (
    CapabilityCard,
    CertificationLevel,
    ComponentLibrary,
    ProbeOutcome,
)
from agentcoop.ir.workflow import (
    Atomic,
    CompiledWorkflow,
    Fallback,
    HumanGate,
    Join,
    Parallel,
    Sequence as WorkflowSequence,
    Verify,
    WorkflowTerm,
    find_term,
    replace_term,
)
from agentcoop.optimize.state import (
    MutationRecord,
    OptimizationCandidate,
)
from agentcoop.probe.suite import (
    _canonical_parameter_values,
    _strict_canonical_json,
    _strict_json_value,
    parameter_domain_suite,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_RESERVED_CONFIG_KEYS = frozenset(
    {
        "component",
        "subgoal_id",
        "term_id",
        "node_id",
        "kind",
        "output",
        "outputs",
        "output_type",
        "output_types",
        "type",
        "types",
        "facet",
        "facets",
        "required_facets",
        "binding",
        "merge",
        "merge_config",
        "verifier",
        "verifier_config",
        "condition",
        "condition_config",
    }
)
_RESERVED_KEY_TOKENS = frozenset(
    {
        "component",
        "subgoal",
        "node",
        "output",
        "outputs",
        "type",
        "types",
        "facet",
        "facets",
        "binding",
        "merge",
        "verifier",
        "condition",
    }
)


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfigMutation(_FrozenRecord):
    mutation_id: NonEmptyStr
    target: NonEmptyStr
    key: NonEmptyStr
    value: Any
    domain_id: NonEmptyStr
    rationale: NonEmptyStr

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        try:
            return _strict_json_value(value, seen=set())
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("mutation value must be canonical JSON") from exc


class CertifiedParameterDomain(_FrozenRecord):
    domain_id: NonEmptyStr
    component: NonEmptyStr
    key: NonEmptyStr
    values: tuple[Any, ...]
    probe_ids: tuple[NonEmptyStr, ...]

    @field_validator("values")
    @classmethod
    def _validate_values(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            return _canonical_parameter_values(value)
        except (TypeError, ValueError, UnicodeError) as exc:
            message = str(exc)
            if "unique" in message:
                raise ValueError("parameter domain values must be unique") from exc
            raise ValueError("parameter domain values must be canonical JSON") from exc

    @field_validator("probe_ids")
    @classmethod
    def _validate_probes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("parameter domain requires unique probe IDs")
        return value


class MutationProposal(_FrozenRecord):
    candidate: OptimizationCandidate
    record: MutationRecord

    @model_validator(mode="after")
    def _validate_pairing(self) -> "MutationProposal":
        if (
            self.candidate.candidate_id != self.record.child_id
            or self.candidate.parent_id != self.record.parent_id
            or self.candidate.mutation_id != self.record.mutation_id
            or self.candidate.generation != self.record.generation
        ):
            raise ValueError("mutation proposal candidate and record must match")
        return self


def _canonical_value(value: Any, *, label: str) -> str:
    try:
        return _strict_canonical_json(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{label} must be canonical JSON") from exc


def _term_identity(term: WorkflowTerm) -> dict[str, Any]:
    body = term.model_dump(mode="python")
    body.pop("term_id", None)
    body.pop("notes", None)
    if isinstance(term, (WorkflowSequence, Parallel, Join)):
        child_field = "children_terms" if isinstance(term, WorkflowSequence) else "branches"
        children = term.children_terms if isinstance(term, WorkflowSequence) else term.branches
        body[child_field] = [_term_identity(child) for child in children]
    elif isinstance(term, (Verify, HumanGate)):
        body["body"] = _term_identity(term.body)
    elif isinstance(term, Fallback):
        body["primary"] = _term_identity(term.primary)
        body["alternate"] = _term_identity(term.alternate)
    return body


def workflow_fingerprint(workflow: CompiledWorkflow) -> str:
    """Hash the complete ID-free term, including every Atomic config value."""
    canonical = _canonical_value(
        _term_identity(workflow.term), label="workflow term/config"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_reserved(key: str) -> bool:
    normalized = key.strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", normalized.replace("_", " ")))
    return normalized in _RESERVED_CONFIG_KEYS or bool(tokens & _RESERVED_KEY_TOKENS)


def _domain_specs(
    card: CapabilityCard, domain: CertifiedParameterDomain
) -> tuple[Any, ...]:
    return tuple(
        parameter_domain_suite(
            card,
            parameter=domain.key,
            values=domain.values,
        )
    )


def _validate_domain(
    domain: CertifiedParameterDomain, library: ComponentLibrary
) -> CapabilityCard:
    if _is_reserved(domain.key):
        raise ValueError(f"configuration key '{domain.key}' is reserved")
    card = library.get(domain.component)
    if card is None:
        raise ValueError(f"domain component '{domain.component}' is not in the library")
    if domain.key not in card.io.parameters:
        raise ValueError(
            f"parameter '{domain.key}' is not declared by component '{card.name}'"
        )

    scoped = [
        probe
        for probe in card.empirical.probes
        if probe.evidence.get("probe_scope") == "parameter_domain"
    ]
    if card.certification_level < CertificationLevel.CERTIFIED and not scoped:
        raise ValueError(
            f"component '{card.name}' must be CERTIFIED before mutation"
        )

    expected_specs = _domain_specs(card, domain)
    expected_ids = tuple(spec.probe_id for spec in expected_specs)
    if domain.probe_ids != expected_ids:
        raise ValueError("parameter domain probe IDs do not match the executable suite")
    outcomes_by_id: dict[str, ProbeOutcome] = {}
    for outcome in card.empirical.probes:
        if outcome.probe_id in outcomes_by_id:
            raise ValueError("duplicate probe ID in component empirical record")
        outcomes_by_id[outcome.probe_id] = outcome
    for spec in expected_specs:
        matched_outcome = outcomes_by_id.get(spec.probe_id)
        if matched_outcome is None:
            raise ValueError(f"parameter-domain probe '{spec.probe_id}' is missing")
        if matched_outcome.kind != spec.kind:
            raise ValueError(f"parameter-domain probe '{spec.probe_id}' has wrong kind")
        if (
            not matched_outcome.passed
            or matched_outcome.evidence.get("contract_preserving") is not True
        ):
            raise ValueError(f"parameter-domain probe '{spec.probe_id}' did not pass")
        for key in (
            "probe_scope",
            "parameter",
            "allowed_values_hash",
            "value_hash",
            "contract_hash",
            "output_context_id",
        ):
            if matched_outcome.evidence.get(key) != spec.expectations.get(key):
                raise ValueError(
                    f"parameter-domain probe '{spec.probe_id}' has invalid {key}"
                )
    if card.certification_level < CertificationLevel.CERTIFIED:
        raise ValueError(
            f"component '{card.name}' must remain CERTIFIED for mutation"
        )
    return card


def _validate_mutation(
    mutation: ConfigMutation, domain: CertifiedParameterDomain
) -> None:
    if mutation.domain_id != domain.domain_id:
        raise ValueError("mutation references the wrong parameter domain")
    if mutation.key != domain.key:
        raise ValueError("mutation key must match its certified domain key")
    if _is_reserved(mutation.key):
        raise ValueError(f"configuration key '{mutation.key}' is reserved")
    proposed = _canonical_value(mutation.value, label="mutation value")
    allowed = {
        _canonical_value(value, label="domain value") for value in domain.values
    }
    if proposed not in allowed:
        raise ValueError("mutation value is outside the certified domain")


def apply_config_mutation(
    workflow: CompiledWorkflow,
    mutation: ConfigMutation,
    *,
    domain: CertifiedParameterDomain,
    library: ComponentLibrary,
) -> CompiledWorkflow:
    """Return a deep, one-key Atomic config update after probe authorization."""
    _validate_domain(domain, library)
    _validate_mutation(mutation, domain)
    target = find_term(workflow.term, mutation.target)
    if target is None:
        raise ValueError(f"mutation target '{mutation.target}' does not exist")
    if not isinstance(target, Atomic):
        raise ValueError("configuration mutation target must be an Atomic term")
    if target.component != domain.component:
        raise ValueError("mutation target component does not match certified domain")

    _canonical_value(target.config, label="Atomic config")
    proposed = _canonical_value(mutation.value, label="mutation value")
    if mutation.key in target.config and (
        _canonical_value(target.config[mutation.key], label="existing config value")
        == proposed
    ):
        raise ValueError("configuration mutation is a no-op")

    config = dict(target.config)
    config[mutation.key] = _strict_json_value(mutation.value, seen=set())
    changed_keys = {
        key
        for key in set(target.config) | set(config)
        if key not in target.config
        or key not in config
        or _canonical_value(target.config[key], label="existing config value")
        != _canonical_value(config[key], label="new config value")
    }
    if changed_keys != {mutation.key}:
        raise ValueError("configuration mutation must change exactly one top-level key")

    replacement = target.model_copy(update={"config": config}, deep=True)
    term, changed = replace_term(workflow.term, mutation.target, replacement)
    if not changed:  # defensive: find_term and replace_term must agree
        raise ValueError("mutation target could not be replaced")
    child = workflow.model_copy(update={"term": term}, deep=True)
    workflow_fingerprint(child)
    return child


def validate_mutation_proposal(
    proposal: MutationProposal,
    *,
    parent: OptimizationCandidate,
    library: ComponentLibrary,
) -> str:
    """Rebuild and verify a proposal at the optimization trust boundary.

    Mutation sources are discovery mechanisms, not authorization authorities.
    The loop calls this function before accepting any proposal, including one
    from a custom source.
    """

    record = proposal.record
    candidate = proposal.candidate
    if record.parent_id != parent.candidate_id:
        raise ValueError("mutation proposal parent does not match")

    domain = CertifiedParameterDomain(
        domain_id=record.domain_id,
        component=record.domain_component,
        key=record.key,
        values=record.domain_values,
        probe_ids=record.probe_ids,
    )
    mutation = ConfigMutation(
        mutation_id=record.mutation_id,
        target=record.target,
        key=record.key,
        value=record.value,
        domain_id=record.domain_id,
        rationale=record.rationale,
    )
    expected_workflow = apply_config_mutation(
        parent.workflow,
        mutation,
        domain=domain,
        library=library,
    )
    fingerprint = workflow_fingerprint(expected_workflow)
    child_id = f"config::{fingerprint}"
    expected_workflow = expected_workflow.model_copy(
        update={
            "workflow_id": f"ecps::{expected_workflow.task_id}::{fingerprint}"
        },
        deep=True,
    )
    expected_candidate = OptimizationCandidate(
        candidate_id=child_id,
        workflow=expected_workflow,
        parent_id=parent.candidate_id,
        mutation_id=record.mutation_id,
        generation=parent.generation + 1,
        static_estimate=parent.static_estimate.model_copy(
            update={"candidate_id": child_id}, deep=True
        ),
    )
    if candidate != expected_candidate:
        raise ValueError(
            "mutation proposal is not the exact probe-authorized one-key child"
        )
    if record.child_id != child_id or record.generation != parent.generation + 1:
        raise ValueError("mutation record identity or generation is invalid")
    return fingerprint


class ConfigGridMutationSource:
    """Finite deterministic source over probe-certified configuration values."""

    def __init__(
        self,
        library: ComponentLibrary,
        domains: Sequence[CertifiedParameterDomain],
        mutations: Sequence[ConfigMutation],
    ) -> None:
        domain_ids = tuple(domain.domain_id for domain in domains)
        if len(domain_ids) != len(set(domain_ids)):
            raise ValueError("parameter domain IDs must be unique")
        mutation_ids = tuple(mutation.mutation_id for mutation in mutations)
        if len(mutation_ids) != len(set(mutation_ids)):
            raise ValueError("configuration mutation IDs must be unique")
        self._library = library
        self._domains = {
            domain.domain_id: domain for domain in domains
        }
        for domain in self._domains.values():
            _validate_domain(domain, library)
        self._mutations = tuple(mutations)
        for mutation in self._mutations:
            mutation_domain = self._domains.get(mutation.domain_id)
            if mutation_domain is None:
                raise ValueError("configuration mutation references an unknown domain")
            _validate_mutation(mutation, mutation_domain)

    def propose(
        self,
        parents: Sequence[OptimizationCandidate],
        *,
        seen_fingerprints: set[str],
    ) -> tuple[MutationProposal, ...]:
        known = set(seen_fingerprints)
        proposals: list[MutationProposal] = []
        ordered_mutations = sorted(
            self._mutations,
            key=lambda mutation: (
                mutation.target,
                mutation.key,
                _canonical_value(mutation.value, label="mutation value"),
                mutation.mutation_id,
            ),
        )
        for parent in sorted(parents, key=lambda candidate: candidate.candidate_id):
            for mutation in ordered_mutations:
                domain = self._domains[mutation.domain_id]
                target = find_term(parent.workflow.term, mutation.target)
                if target is None:
                    continue
                if not isinstance(target, Atomic):
                    raise ValueError("configuration mutation target must be an Atomic term")
                if target.component != domain.component:
                    continue
                if mutation.key in target.config and (
                    _canonical_value(
                        target.config[mutation.key], label="existing config value"
                    )
                    == _canonical_value(mutation.value, label="mutation value")
                ):
                    continue
                workflow = apply_config_mutation(
                    parent.workflow,
                    mutation,
                    domain=domain,
                    library=self._library,
                )
                fingerprint = workflow_fingerprint(workflow)
                if fingerprint in known:
                    continue
                known.add(fingerprint)
                child_id = f"config::{fingerprint}"
                workflow = workflow.model_copy(
                    update={
                        "workflow_id": f"ecps::{workflow.task_id}::{fingerprint}"
                    },
                    deep=True,
                )
                generation = parent.generation + 1
                candidate = OptimizationCandidate(
                    candidate_id=child_id,
                    workflow=workflow,
                    parent_id=parent.candidate_id,
                    mutation_id=mutation.mutation_id,
                    generation=generation,
                    static_estimate=parent.static_estimate.model_copy(
                        update={"candidate_id": child_id}, deep=True
                    ),
                )
                record = MutationRecord(
                    mutation_id=mutation.mutation_id,
                    parent_id=parent.candidate_id,
                    child_id=child_id,
                    target=mutation.target,
                    key=mutation.key,
                    value=_strict_json_value(mutation.value, seen=set()),
                    domain_id=domain.domain_id,
                    domain_component=domain.component,
                    domain_values=domain.values,
                    probe_ids=domain.probe_ids,
                    rationale=mutation.rationale,
                    generation=generation,
                )
                proposals.append(MutationProposal(candidate=candidate, record=record))
        return tuple(proposals)


__all__ = [
    "CertifiedParameterDomain",
    "ConfigGridMutationSource",
    "ConfigMutation",
    "MutationProposal",
    "apply_config_mutation",
    "validate_mutation_proposal",
    "workflow_fingerprint",
]
