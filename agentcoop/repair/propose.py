"""Proposing repairs from a diagnosis.

The hard rule, enforced here and tested directly: **a patch family may only be
proposed for a fault class that admits it.** ``FAULT_TAXONOMY`` decides what is
admissible, so an ``ARTIFACT_CONTRACT`` fault cannot be answered by
``rewrite_prompt`` and an ``EVALUATOR_FAILURE`` cannot be answered by patching
the generator.

Proposals also have to be *concrete*. A patch family like ``insert_adapter``
needs an actual registered converter to exist; if none does, the proposal is
not emitted, because "insert some converter" is not a repair. Where a family's
required parameters cannot be filled from the library, the family is skipped
and the reason recorded.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.grammar import RuleContext, can_bind
from agentcoop.diagnose.diagnose import DiagnosisOutcome
from agentcoop.ir.artifacts import Compatibility
from agentcoop.ir.faults import FaultClass, FaultHypothesis, RepairTier
from agentcoop.ir.workflow import Atomic, CompiledWorkflow, atomics, find_term, iter_terms
from agentcoop.repair.patches import PATCH_FAMILIES, Patch


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patches: list[Patch] = Field(default_factory=list)
    #: family -> why it could not be proposed. Kept so the loop can explain
    #: itself when it declines to act.
    skipped: dict[str, str] = Field(default_factory=dict)

    def for_tier(self, tier: RepairTier) -> list[Patch]:
        return [p for p in self.patches if p.tier is tier]


def propose(
    outcome: DiagnosisOutcome,
    workflow: CompiledWorkflow,
    ctx: RuleContext,
    *,
    max_per_hypothesis: int = 3,
    top_k: int = 2,
) -> Proposal:
    """Cause-specific patches for the leading hypotheses."""
    proposal = Proposal()

    if outcome.needs_more_evidence:
        proposal.skipped["*"] = (
            "diagnosis entropy exceeds the policy threshold; gathering evidence "
            "is the correct next action, not patching"
        )
        return proposal

    target_term = _target_term(outcome, workflow)
    if target_term is None:
        proposal.skipped["*"] = (
            f"could not map blame subject '{outcome.localization.subject}' onto a "
            "term in this workflow"
        )
        return proposal

    for hypothesis in outcome.diagnosis.top_k(top_k):
        emitted = 0
        for family_name in hypothesis.spec.admissible_patches:
            if emitted >= max_per_hypothesis:
                break
            family = PATCH_FAMILIES.get(family_name)
            if family is None:
                proposal.skipped[family_name] = "family is not implemented"
                continue

            params, reason = _fill_params(family_name, hypothesis, workflow, ctx, target_term)
            if params is None:
                proposal.skipped[family_name] = reason
                continue

            patch = Patch(
                patch_id=f"{hypothesis.fault_class.value}:{family_name}:{target_term}",
                family=family_name,
                tier=family.tier,
                target=target_term,
                description=family.description,
                rationale=(
                    f"{hypothesis.fault_class.value} at "
                    f"{hypothesis.blame_target.value} '{hypothesis.subject}' "
                    f"(p={hypothesis.probability:.2f}); {family.description}"
                ),
                hypothesis=hypothesis,
                params=params,
                config_only=family.config_only,
                terminal=family.terminal,
            )
            proposal.patches.append(patch)
            emitted += 1

    return proposal


def _target_term(outcome: DiagnosisOutcome, workflow: CompiledWorkflow) -> Optional[str]:
    """Map the blame subject onto the term a patch can act on.

    When blame lands on an *artifact*, the term to patch is the one that
    produced it — not the node that failed while consuming it. That mapping is
    the whole payoff of localization.
    """
    subject = outcome.localization.subject
    if subject is None:
        return None

    producer = None
    if outcome.localization.subject_kind.value == "artifact":
        producer = _producer_from_artifact_id(subject)
    elif "->" in subject:
        producer = subject.split("->")[0]
    else:
        producer = subject

    if producer:
        if find_term(workflow.term, producer) is not None:
            return producer
        for atom in atomics(workflow.term):
            if producer.startswith(atom.term_id) or atom.term_id.startswith(producer):
                return atom.term_id
    return None


def _producer_from_artifact_id(artifact_id: str) -> Optional[str]:
    """Artifact ids are ``producer::type::hash`` when a producer is known."""
    parts = artifact_id.split("::")
    return parts[0] if len(parts) >= 3 else None


def _fill_params(
    family: str,
    hypothesis: FaultHypothesis,
    workflow: CompiledWorkflow,
    ctx: RuleContext,
    target_term: str,
) -> tuple[Optional[dict], str]:
    """Concrete parameters for a family, or the reason none could be found."""
    spec = PATCH_FAMILIES[family]
    if not spec.required_params:
        return _default_params(family, hypothesis), ""

    term = find_term(workflow.term, target_term)

    if family == "insert_adapter":
        converter = _find_converter(workflow, ctx, target_term)
        if converter is None:
            return None, (
                "no registered converter bridges the facet mismatch; a repair "
                "cannot invent one"
            )
        return {"converter": converter}, ""

    if family in ("replace_component", "add_specialist", "add_fallback"):
        replacement = _find_alternative_component(workflow, ctx, target_term)
        if replacement is None:
            return None, "no other certified component can serve this subgoal"
        return {"component": replacement}, ""

    if family in ("add_verifier", "diversify_evaluator", "add_deterministic_check"):
        verifier = _find_verifier(ctx)
        if verifier is None:
            return None, "no evaluator is available at a level this task can check"
        return {"verifier": verifier}, ""

    if family == "define_merge":
        merge = _find_merge(workflow, ctx, target_term)
        if merge is None:
            return None, "no registered merge algebra applies to these branches"
        return {"merge": merge}, ""

    if family == "add_human_gate":
        if not ctx.dossier.human_review:
            return None, "the dossier declares no human-review condition"
        return {"condition": ctx.dossier.human_review[0].condition_id}, ""

    return None, f"no parameter filler for family '{family}'"


def _default_params(family: str, hypothesis: FaultHypothesis) -> dict:
    if family in ("retry_node", "backoff_retry"):
        return {"attempts": 1, "backoff_s": 5 if family == "backoff_retry" else 0}
    if family == "tighten_contract":
        return {"declare_facets": True}
    if family == "isolate_state":
        return {"isolate_scratch": True}
    if family == "invalidate_cache":
        return {"cache": "bypass"}
    if family == "pin_environment":
        return {"pin_dependencies": True}
    if family == "isolate_environment":
        return {"isolate_container": True}
    if family == "raise_resource_limit":
        return {"memory_multiplier": 2.0}
    if family == "retune_config":
        return {"retuned": True}
    if family == "rewrite_prompt":
        return {"prompt_revision": 1}
    return {}


def _find_converter(
    workflow: CompiledWorkflow, ctx: RuleContext, target_term: str
) -> Optional[str]:
    """A registered converter that bridges this node's input mismatch."""
    term = find_term(workflow.term, target_term)
    if not isinstance(term, Atomic):
        return None
    producer = ctx.library.get(term.component)
    if producer is None:
        return None

    for consumer_atom in atomics(workflow.term):
        consumer = ctx.library.get(consumer_atom.component)
        if consumer is None or consumer_atom.term_id == target_term:
            continue
        for produced in producer.io.produces:
            consumed = consumer.io.consumed_type(produced.name)
            if consumed is None:
                continue
            have = producer.effective_facets(produced.name)
            want = dict(consumed.facets)
            converter = ctx.types.find_converter(produced.name, have, want)
            if converter is not None:
                return converter.name
    return None


def _find_alternative_component(
    workflow: CompiledWorkflow, ctx: RuleContext, target_term: str
) -> Optional[str]:
    term = find_term(workflow.term, target_term)
    if not isinstance(term, Atomic):
        return None
    subgoal = ctx.dossier.subgoal(term.subgoal_id)
    if subgoal is None:
        return None
    for name in ctx.library.names():
        if name == term.component:
            continue
        if can_bind(name, subgoal, ctx).allowed:
            return name
    return None


def _find_verifier(ctx: RuleContext) -> Optional[str]:
    from agentcoop.ir.dossier import EvaluatorAvailability

    for level, availability in sorted(
        ctx.dossier.evaluators.items(), key=lambda kv: kv[0].value
    ):
        if availability is not EvaluatorAvailability.UNAVAILABLE:
            return f"{level.value}_available_check"
    return None


def _find_merge(
    workflow: CompiledWorkflow, ctx: RuleContext, target_term: str
) -> Optional[str]:
    from agentcoop.compile.grammar import term_io
    from agentcoop.ir.workflow import Join, Parallel

    term = find_term(workflow.term, target_term)
    branches = None
    if isinstance(term, (Parallel, Join)):
        branches = term.branches
    if not branches:
        return None
    produced = [term_io(b, ctx.library)[1] for b in branches]
    common = set.intersection(*produced) if produced else set()
    if not common:
        return None
    applicable = [
        s
        for s in ctx.merges.for_type(sorted(common)[0])
        if s.accepts_arity(len(branches))
    ]
    return applicable[0].name if applicable else None


__all__ = ["Proposal", "propose"]
