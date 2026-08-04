"""Human review as a first-class, non-blocking workflow node.

A `HumanGate` term lowers to a node that runs through this adapter. By default
it *records a request* and returns ``deferred``: the run continues, and the
open request travels with the artifacts.

The value that carries the weight here is ``approved``, which is ``None`` while
a review is deferred — not ``True``. A pending review is not an approval, in
exactly the same way that an unavailable evaluator is not a passing evaluator.
Both are places where a system that optimizes for "the workflow completed" will
quietly convert an absence of judgment into a positive verdict, and both are
represented here as an explicit third value so that downstream code has to
handle them.

The other deliberate omission: when a reviewer rejects a result, the error line
this adapter emits carries **no** :class:`FaultClass` tag. The adapter knows
that a human was unhappy; it does not know whether the cause was a bad
component, a bad contract, or an ambiguous task specification. Tagging a class
here would be the "symptom straight to canned action" reflex, one layer earlier
than usual and harder to spot.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.ir.capability import BehaviorContract, CostProfile

from agentcoop.components.base import (
    Clock,
    Invocation,
    InvocationResult,
    draft_artifact,
    finalize_outputs,
    input_ids,
    perf_clock,
    stable_digest,
)

ReviewStatus = Literal["deferred", "approved", "rejected"]


class ReviewDecision(BaseModel):
    """A recorded human judgment. Supplied out of band; never synthesized."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    reviewer: str = ""
    comment: str = ""


class ReviewRequest(BaseModel):
    """The artifact a human gate emits."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    condition: str
    question: str
    subgoal_id: Optional[str] = None
    subject: Optional[str] = None
    #: Artifact ids the reviewer is being asked about.
    artifacts_under_review: list[str] = Field(default_factory=list)
    status: ReviewStatus = "deferred"
    #: ``None`` while deferred. Never defaults to True.
    approved: Optional[bool] = None
    reviewer: str = ""
    comment: str = ""


def request_id_for(inv: Invocation, condition: str) -> str:
    """Deterministic request id.

    Derived from the invocation rather than from a UUID or a timestamp so that
    re-running a workflow addresses the *same* review request instead of
    orphaning the reviewer's earlier answer.
    """
    return "hr_" + stable_digest(
        inv.component,
        inv.subgoal_id or "",
        condition,
        *sorted(art.artifact_id for art in inv.inputs.values()),
    )


class HumanReviewAdapter:
    """Emit a review request; resolve it only from recorded decisions."""

    def __init__(
        self,
        name: str = "human_review",
        *,
        decisions: Optional[Mapping[str, Any]] = None,
        output_type: str = "HumanReviewRequest",
        write_to_workdir: bool = True,
        default_question: str = "Review the artifacts produced by this step.",
        clock: Clock = perf_clock,
    ) -> None:
        """``decisions`` maps a request id *or* a condition id to a
        :class:`ReviewDecision`. Benchmarks pre-load it to replay a reviewer;
        live runs leave it empty and the gate defers."""
        self.name = name
        self.output_type = output_type
        self._write_to_workdir = write_to_workdir
        self._default_question = default_question
        self._clock = clock
        self._decisions: dict[str, ReviewDecision] = {
            str(key): value
            if isinstance(value, ReviewDecision)
            else ReviewDecision.model_validate(
                {"status": value} if isinstance(value, str) else value
            )
            for key, value in (decisions or {}).items()
        }

    def behavior_contract(self) -> BehaviorContract:
        """Not shadow-safe: speculatively asking a human to review a throwaway
        patched run would spend the one resource this system cannot replenish."""
        return BehaviorContract(
            deterministic=True,
            idempotent=True,
            retry_safe=True,
            side_effects=["requests attention from a human reviewer"],
            shadow_safe=False,
        )

    def lookup(self, request_id: str, condition: str) -> Optional[ReviewDecision]:
        return self._decisions.get(request_id) or self._decisions.get(condition)

    async def invoke(self, inv: Invocation) -> InvocationResult:
        started = self._clock()
        condition = str(inv.config.get("condition", ""))
        question = str(inv.config.get("question", self._default_question))
        subject = inv.config.get("subject")
        request_id = request_id_for(inv, condition)

        decision = self.lookup(request_id, condition)
        status: ReviewStatus = decision.status if decision else "deferred"
        request = ReviewRequest(
            request_id=request_id,
            condition=condition,
            question=question,
            subgoal_id=inv.subgoal_id,
            subject=str(subject) if subject is not None else None,
            artifacts_under_review=sorted(art.artifact_id for art in inv.inputs.values()),
            status=status,
            approved={"approved": True, "rejected": False, "deferred": None}[status],
            reviewer=decision.reviewer if decision else "",
            comment=decision.comment if decision else "",
        )

        path: Optional[str] = None
        logs: list[str] = []
        if self._write_to_workdir and inv.workdir is not None:
            target_dir = inv.workdir / "human_review"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{request_id}.json"
            target.write_text(
                json.dumps(request.model_dump(mode="json"), sort_keys=True, indent=2),
                encoding="utf-8",
            )
            path = str(target)
            logs.append(f"review request written to {path}")

        artifact = draft_artifact(
            type_name=self.output_type,
            payload=request.model_dump(mode="json"),
            path=path,
            notes=[f"review_status={status}"],
        )
        finalized = finalize_outputs(
            {self.output_type: artifact},
            producer=inv.producer_id,
            derived_from=input_ids(inv),
        )

        errors: list[str] = []
        if status == "rejected":
            # Untagged on purpose: see the module docstring. A rejection is a
            # signal, and turning a signal into a fault class here would let
            # the repair layer act on a symptom it never diagnosed.
            errors.append(
                f"human reviewer rejected this step "
                f"(request {request_id}): {request.comment or 'no comment given'}"
            )
        elif status == "deferred":
            logs.append(
                f"review {request_id} is DEFERRED — not an approval; "
                "downstream consumers must treat approved=null as undecided"
            )

        observed = dict(finalized.observed_facets)
        observed[self.output_type] = {
            **observed.get(self.output_type, {}),
            "review_status": status,
            "decided": "true" if status != "deferred" else "false",
        }

        return InvocationResult(
            ok=status != "rejected",
            outputs=finalized.outputs,
            cost=CostProfile(latency_s=self._clock() - started),
            logs="\n".join([*logs, *finalized.notes]),
            errors=errors,
            exit_code=0 if status != "rejected" else 1,
            observed_facets=observed,
        )


def review_from_artifact(payload: Any) -> Optional[ReviewRequest]:
    """Parse a review request back out of an artifact payload."""
    if not isinstance(payload, Mapping):
        return None
    try:
        return ReviewRequest.model_validate(dict(payload))
    except Exception:  # noqa: BLE001 - a malformed payload is simply not a review
        return None


def unresolved_reviews(payloads: list[Any]) -> list[str]:
    """Request ids still awaiting a human. Used by the resource/claim checks.

    Exists so no caller has to re-derive "deferred means undecided" and get it
    subtly wrong by testing ``if review.get("approved")``.
    """
    out: list[str] = []
    for payload in payloads:
        review = review_from_artifact(payload)
        if review is not None and review.approved is None:
            out.append(review.request_id)
    return sorted(out)


__all__ = [
    "ReviewStatus",
    "ReviewDecision",
    "ReviewRequest",
    "request_id_for",
    "HumanReviewAdapter",
    "review_from_artifact",
    "unresolved_reviews",
]
