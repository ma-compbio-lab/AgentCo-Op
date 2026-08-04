"""The bounded repair loop.

    Detect -> Localize -> Diagnose -> Propose -> Shadow validate -> Commit/Rollback

Each attempt is a transaction against a rollback point. The loop stops when the
run is healthy, when the diagnosis is too uncertain to act on, when every tier's
budget is spent, or when the only admissible response is terminal (report the
uncertainty, or escalate a specification defect to a human).

What the loop deliberately will *not* do: patch on a coin flip, adopt a change
that fixed the symptom while regressing something else, or keep applying
contract repairs to a structural fault.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentcoop.compile.grammar import RuleContext
from agentcoop.diagnose.detectors import detect_all
from agentcoop.diagnose.diagnose import Diagnoser, DiagnosisOutcome
from agentcoop.diagnose.localize import localize
from agentcoop.execute.engine import ExecutionResult
from agentcoop.ir.capability import ComponentLibrary
from agentcoop.ir.workflow import CompiledWorkflow, RepairPolicy
from agentcoop.repair.patches import Patch, PatchError, apply
from agentcoop.repair.policy import decide_tier, total_budget_exhausted
from agentcoop.repair.propose import propose
from agentcoop.repair.shadow import ShadowHarness, ShadowValidator
from agentcoop.repair.transaction import (
    RepairLog,
    RepairTransaction,
    TransactionOutcome,
)


class RepairOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: CompiledWorkflow
    log: RepairLog = Field(default_factory=RepairLog)
    repaired: bool = False
    attempts: int = 0
    #: Set when the loop stopped because the correct action was not a patch.
    terminal_reason: str = ""
    diagnoses: list[DiagnosisOutcome] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RepairLoop:
    def __init__(
        self,
        ctx: RuleContext,
        library: ComponentLibrary,
        *,
        diagnoser: Optional[Diagnoser] = None,
        validator: Optional[ShadowValidator] = None,
        policy: Optional[RepairPolicy] = None,
    ) -> None:
        self.ctx = ctx
        self.library = library
        self.policy = policy or RepairPolicy()
        self.diagnoser = diagnoser or Diagnoser(
            entropy_threshold=self.policy.max_diagnosis_entropy_bits
        )
        self.validator = validator or ShadowValidator(library)

    async def run(
        self,
        workflow: CompiledWorkflow,
        execution: ExecutionResult,
        harness: ShadowHarness,
        *,
        max_attempts: int = 6,
    ) -> RepairOutcome:
        outcome = RepairOutcome(workflow=workflow)
        current = workflow
        baseline = execution

        for attempt in range(1, max_attempts + 1):
            outcome.attempts = attempt

            signals = detect_all(
                baseline.trace, baseline.report, current, self.ctx.dossier
            )
            if not signals.blocking():
                outcome.notes.append("no blocking signal remains; run is healthy")
                break

            localization = localize(
                baseline.trace, signals, current, self.ctx.dossier, self.ctx.types
            )
            diagnosis = self.diagnoser.diagnose(
                signals, localization, current, self.ctx.dossier, self.library
            )
            outcome.diagnoses.append(diagnosis)

            if diagnosis.needs_more_evidence:
                outcome.terminal_reason = diagnosis.evidence_gathering_hint
                outcome.notes.append(
                    "stopped: the diagnosis is too uncertain to act on"
                )
                break

            top = diagnosis.diagnosis.top
            if top is None:
                outcome.notes.append("stopped: no hypothesis to act on")
                break

            decision = decide_tier(top.fault_class, top.tier, outcome.log, self.policy)
            if not decision.allowed:
                outcome.terminal_reason = decision.reason
                outcome.notes.append(f"stopped: {decision.reason}")
                break
            if decision.escalated_from is not None:
                outcome.notes.append(decision.reason)

            if top.spec.escalate:
                outcome.terminal_reason = (
                    f"'{top.fault_class.value}' is not automatically repairable: "
                    f"{top.spec.description}"
                )
                outcome.notes.append("stopped: escalated to a human")
                break

            proposal = propose(diagnosis, current, self.ctx)
            candidates = [
                p
                for p in proposal.patches
                if p.patch_id not in outcome.log.attempted_patch_ids()
            ]
            if not candidates:
                reasons = "; ".join(f"{k}: {v}" for k, v in sorted(proposal.skipped.items()))
                outcome.terminal_reason = reasons or "no admissible patch remains"
                outcome.notes.append(f"stopped: {outcome.terminal_reason}")
                break

            committed, current, baseline = await self._try_patches(
                candidates, current, baseline, harness, outcome, attempt
            )
            if committed:
                outcome.repaired = True
                outcome.workflow = current
                if not baseline.report.failures():
                    outcome.notes.append("repair validated and committed; run is healthy")
                    break
            else:
                outcome.notes.append(
                    f"attempt {attempt}: every proposed patch was rejected"
                )
                if total_budget_exhausted(outcome.log, self.policy):
                    break

        return outcome

    async def _try_patches(
        self,
        candidates: list[Patch],
        current: CompiledWorkflow,
        baseline: ExecutionResult,
        harness: ShadowHarness,
        outcome: RepairOutcome,
        attempt: int,
    ) -> tuple[bool, CompiledWorkflow, ExecutionResult]:
        for patch in candidates:
            transaction = RepairTransaction(
                transaction_id=f"tx{attempt}:{patch.patch_id}",
                pre_state_key=current.structural_key(),
                patch=patch,
                attempt=attempt,
            )

            if patch.terminal:
                transaction.outcome = TransactionOutcome.TERMINAL
                transaction.reason = patch.description
                outcome.log.append(transaction)
                outcome.terminal_reason = patch.description
                return False, current, baseline

            try:
                patched = apply(patch, current)
            except PatchError as exc:
                transaction.outcome = TransactionOutcome.NOT_APPLICABLE
                transaction.reason = str(exc)
                outcome.log.append(transaction)
                continue

            if not self.policy.require_shadow_validation:
                transaction.outcome = TransactionOutcome.COMMITTED
                transaction.post_state_key = patched.structural_key()
                transaction.reason = "committed without shadow validation (policy)"
                outcome.log.append(transaction)
                return True, patched, baseline

            shadow = await self.validator.validate(
                patch, current, patched, baseline, harness
            )
            transaction.shadow = shadow

            if shadow.notes and any("not shadow-safe" in n for n in shadow.notes):
                transaction.outcome = TransactionOutcome.ESCALATED
                transaction.reason = shadow.summary
                outcome.log.append(transaction)
                outcome.terminal_reason = shadow.notes[0]
                return False, current, baseline

            if not shadow.ok:
                transaction.outcome = TransactionOutcome.ROLLED_BACK
                transaction.reason = shadow.summary
                outcome.log.append(transaction)
                continue

            transaction.outcome = TransactionOutcome.COMMITTED
            transaction.post_state_key = patched.structural_key()
            transaction.reason = shadow.summary
            outcome.log.append(transaction)

            try:
                new_baseline = await harness(patched)
            except Exception:
                new_baseline = baseline
            return True, patched, new_baseline

        return False, current, baseline


__all__ = ["RepairLoop", "RepairOutcome"]
