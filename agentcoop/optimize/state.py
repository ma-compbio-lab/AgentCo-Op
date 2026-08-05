"""Typed, auditable persisted state for preference search."""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentcoop.compile.select import UtilityEstimate
from agentcoop.execute.engine import ExecutionResult
from agentcoop.ir.capability import CostProfile
from agentcoop.ir.dossier import ResourceLimits
from agentcoop.ir.preference import PreferenceArchive, VerbosityPolicy
from agentcoop.ir.utility import UtilityVector
from agentcoop.ir.workflow import CompiledWorkflow
from agentcoop.optimize.packets import CandidateView
from agentcoop.optimize.preference import PreferenceModelSet


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ECPS_SCHEMA_VERSION = "agentcoop.ecps.outcome.v1"
ECPS_ALGORITHM_VERSION = "ecps-v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _content_hash(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8", errors="surrogatepass")
    ).hexdigest()


def _finite_non_negative(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value >= 0
    )


def _valid_cost(cost: CostProfile) -> bool:
    values = cost.model_dump()
    return (
        all(_finite_non_negative(value) for value in values.values())
        and isinstance(cost.tokens, int)
        and not isinstance(cost.tokens, bool)
    )


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OptimizationState(str, Enum):
    INITIALIZING = "initializing"
    EXECUTING = "executing"
    OBJECTIVE_GATING = "objective_gating"
    COMPARING = "comparing"
    MODELING = "modeling"
    MUTATING = "mutating"
    STOPPED = "stopped"


class OptimizationStopReason(str, Enum):
    OBJECTIVE_SINGLETON = "objective_singleton"
    CONFIDENT_PREFERENCE = "confident_preference"
    VERBOSITY_POLICY_SINGLETON = "verbosity_policy_singleton"
    NO_ADMISSIBLE_CANDIDATES = "no_admissible_candidates"
    NO_PREFERENCES = "no_preferences"
    NO_CASES = "no_cases"
    INELIGIBLE_EVALUATOR = "ineligible_evaluator"
    NO_JUDGES = "no_judges"
    INSUFFICIENT_JUDGE_DIVERSITY = "insufficient_judge_diversity"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RESOURCE_ACCOUNTING_INVALID = "resource_accounting_invalid"
    EVALUATOR_UNSTABLE = "evaluator_unstable"
    MUTATION_SOURCE_INVALID = "mutation_source_invalid"
    VERBOSITY_BASELINE_UNAVAILABLE = "verbosity_baseline_unavailable"
    NO_MUTATIONS = "no_mutations"
    FRONT_STABLE_UNRESOLVED = "front_stable_unresolved"


class EvaluationCase(_FrozenRecord):
    case_id: NonEmptyStr
    input_fingerprint: NonEmptyStr
    seed: int = Field(ge=0)
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    evaluator_ids: tuple[NonEmptyStr, ...] = ()
    tool_snapshot_id: NonEmptyStr

    @field_validator("seed", mode="before")
    @classmethod
    def _validate_seed(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("seed must be a non-negative integer")
        return value

    @field_validator("evaluator_ids")
    @classmethod
    def _validate_evaluators(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evaluator_ids must be unique")
        return value

    @field_validator("limits")
    @classmethod
    def _validate_limits(cls, value: ResourceLimits) -> ResourceLimits:
        numeric_fields = (
            value.max_usd,
            value.max_wall_time_s,
            value.max_tokens,
            value.max_component_calls,
            value.max_repair_transactions,
        )
        if any(item is not None and not _finite_non_negative(item) for item in numeric_fields):
            raise ValueError("limits must be finite and non-negative")
        for item in (
            value.max_tokens,
            value.max_component_calls,
            value.max_repair_transactions,
        ):
            if item is not None and (
                not isinstance(item, int) or isinstance(item, bool)
            ):
                raise ValueError("integer limits must be integers")
        if (
            not _finite_non_negative(value.shadow_budget_fraction)
            or value.shadow_budget_fraction > 1.0
        ):
            raise ValueError("limits shadow budget fraction must be between zero and one")
        return value

    def fingerprint(self) -> str:
        body = {
            "case_id": self.case_id,
            "input_fingerprint": self.input_fingerprint,
            "seed": self.seed,
            "limits": self.limits.model_dump(mode="json"),
            "evaluator_ids": sorted(self.evaluator_ids),
            "tool_snapshot_id": self.tool_snapshot_id,
        }
        return f"case::{_content_hash(body)}"


class CostUnit(str, Enum):
    USD = "usd"
    TOKENS = "tokens"


class CaseExecution(_FrozenRecord):
    case_fingerprint: NonEmptyStr
    result: ExecutionResult
    cost_unit: Optional[CostUnit] = None
    latency_measured: bool = False


class OptimizationBudget(_FrozenRecord):
    max_candidate_executions: int = Field(default=256, ge=0)
    max_judge_calls: int = Field(default=512, ge=0)
    max_candidates: int = Field(default=64, ge=0)
    max_generations: int = Field(default=4, ge=0)
    soft_max_usd: Optional[float] = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    soft_max_tokens: Optional[int] = Field(default=None, ge=0)
    soft_max_execution_latency_s: Optional[float] = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )

    @field_validator(
        "max_candidate_executions",
        "max_judge_calls",
        "max_candidates",
        "max_generations",
        "soft_max_tokens",
        mode="before",
    )
    @classmethod
    def _validate_integer_budget(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("count budgets must be integers")
        return value

    @field_validator(
        "soft_max_usd", "soft_max_execution_latency_s", mode="before"
    )
    @classmethod
    def _validate_float_budget(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("resource budgets must be numeric")
        return value


class OptimizationLedger(_FrozenRecord):
    candidate_executions: int = Field(default=0, ge=0)
    judge_calls: int = Field(default=0, ge=0)
    execution_cost: CostProfile = Field(default_factory=CostProfile)
    judge_cost: CostProfile = Field(default_factory=CostProfile)
    cost_accounting_complete: bool = True
    soft_limits_exceeded: tuple[NonEmptyStr, ...] = ()

    @field_validator("candidate_executions", "judge_calls", mode="before")
    @classmethod
    def _validate_counts(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("ledger counts must be integers")
        return value

    @model_validator(mode="after")
    def _validate_accounting(self) -> "OptimizationLedger":
        if self.cost_accounting_complete and (
            not _valid_cost(self.execution_cost) or not _valid_cost(self.judge_cost)
        ):
            raise ValueError("complete accounting requires finite non-negative costs")
        if len(self.soft_limits_exceeded) != len(set(self.soft_limits_exceeded)):
            raise ValueError("soft limit names must be unique")
        return self

    def total_cost(self) -> CostProfile:
        return self.execution_cost + self.judge_cost

    @property
    def execution_latency_s(self) -> float:
        return self.execution_cost.latency_s


class OptimizationPolicy(_FrozenRecord):
    budget: OptimizationBudget = Field(default_factory=OptimizationBudget)
    ridge: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    delta: float = Field(default=0.1, gt=0.0, lt=1.0, allow_inf_nan=False)
    epsilon: dict[NonEmptyStr, float] = Field(default_factory=dict)
    max_iterations: int = Field(default=100, gt=0)
    tolerance: float = Field(default=1e-8, gt=0.0, allow_inf_nan=False)
    # The protocol has two required primary families; a third is arbitration
    # only and therefore cannot be configured as required coverage.
    min_judge_families: int = Field(default=2, ge=2, le=2)
    max_payload_chars: int = Field(default=50_000, gt=0)
    verbosity: VerbosityPolicy = Field(default_factory=VerbosityPolicy)

    @field_validator(
        "max_iterations", "min_judge_families", "max_payload_chars", mode="before"
    )
    @classmethod
    def _validate_integer_policy(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("integer policy fields must be integers")
        return value

    @field_validator("ridge", "delta", "tolerance", mode="before")
    @classmethod
    def _validate_float_policy(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("numeric policy fields must be numeric")
        return value

    @field_validator("epsilon")
    @classmethod
    def _validate_epsilon(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not key.strip() for key in value):
            raise ValueError("epsilon keys must be non-empty")
        if any(not _finite_non_negative(slack) for slack in value.values()):
            raise ValueError("epsilon values must be finite and non-negative")
        return value


class OptimizationCandidate(_FrozenRecord):
    candidate_id: NonEmptyStr
    workflow: CompiledWorkflow
    parent_id: Optional[NonEmptyStr] = None
    mutation_id: Optional[NonEmptyStr] = None
    generation: int = Field(ge=0)
    static_estimate: UtilityEstimate

    @field_validator("generation", mode="before")
    @classmethod
    def _validate_generation(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("generation must be an integer")
        return value

    @model_validator(mode="after")
    def _validate_lineage(self) -> "OptimizationCandidate":
        if self.candidate_id != self.static_estimate.candidate_id:
            raise ValueError("candidate and static estimate IDs must match")
        has_lineage = self.parent_id is not None and self.mutation_id is not None
        if self.generation == 0 and (
            self.parent_id is not None or self.mutation_id is not None
        ):
            raise ValueError("initial candidates cannot have mutation lineage")
        if self.generation > 0 and not has_lineage:
            raise ValueError("mutated candidates require parent and mutation IDs")
        return self


class CandidateEvaluation(_FrozenRecord):
    candidate: OptimizationCandidate
    executions: tuple[CaseExecution, ...] = ()
    feasible: bool
    rejection_reasons: tuple[NonEmptyStr, ...] = ()
    utility: UtilityVector = Field(default_factory=UtilityVector)
    utility_basis: dict[str, str] = Field(default_factory=dict)
    packet_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _validate_evaluation(self) -> "CandidateEvaluation":
        case_fingerprints = tuple(
            execution.case_fingerprint for execution in self.executions
        )
        if len(case_fingerprints) != len(set(case_fingerprints)):
            raise ValueError("candidate executions must use unique cases")
        if self.feasible and self.rejection_reasons:
            raise ValueError("feasible candidate cannot have rejection reasons")
        if not self.feasible and not self.rejection_reasons:
            raise ValueError("infeasible candidate requires a rejection reason")
        if len(self.packet_ids) != len(set(self.packet_ids)):
            raise ValueError("candidate packet IDs must be unique")
        return self


class OptimizationEvent(_FrozenRecord):
    index: int = Field(ge=0)
    state: OptimizationState
    detail: str = ""
    candidate_ids: tuple[NonEmptyStr, ...] = ()
    pair_id: Optional[NonEmptyStr] = None

    @field_validator("index", mode="before")
    @classmethod
    def _validate_index(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("event index must be an integer")
        return value

    @field_validator("candidate_ids")
    @classmethod
    def _validate_candidate_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("event candidate IDs must be unique")
        return value


class MutationRecord(_FrozenRecord):
    mutation_id: NonEmptyStr
    parent_id: NonEmptyStr
    child_id: NonEmptyStr
    target: NonEmptyStr
    key: NonEmptyStr
    value: Any
    domain_id: NonEmptyStr
    domain_component: NonEmptyStr
    domain_values: tuple[Any, ...]
    probe_ids: tuple[NonEmptyStr, ...]
    rationale: NonEmptyStr
    generation: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_record(self) -> "MutationRecord":
        if not self.domain_values:
            raise ValueError("mutation record requires domain values")
        if not self.probe_ids or len(self.probe_ids) != len(set(self.probe_ids)):
            raise ValueError("mutation record requires unique probe IDs")
        return self


class OptimizationOutcome(_FrozenRecord):
    selected_candidate_id: Optional[NonEmptyStr] = None
    contender_ids: tuple[NonEmptyStr, ...] = ()
    schema_version: NonEmptyStr = ECPS_SCHEMA_VERSION
    algorithm_version: NonEmptyStr = ECPS_ALGORITHM_VERSION
    dossier_fingerprint: NonEmptyStr
    cases: tuple[EvaluationCase, ...] = ()
    policy: OptimizationPolicy
    candidates: tuple[CandidateEvaluation, ...] = ()
    compiler_rejections: dict[str, str] = Field(default_factory=dict)
    packet_archive: tuple[CandidateView, ...] = ()
    archive: PreferenceArchive = Field(default_factory=PreferenceArchive)
    model_snapshots: tuple[PreferenceModelSet, ...] = ()
    mutation_records: tuple[MutationRecord, ...] = ()
    objective_front: tuple[NonEmptyStr, ...] = ()
    preference_front: tuple[NonEmptyStr, ...] = ()
    events: tuple[OptimizationEvent, ...]
    ledger: OptimizationLedger
    state: OptimizationState
    stop_reason: OptimizationStopReason
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_outcome(self) -> "OptimizationOutcome":
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate case ID in optimization outcome")
        case_fingerprints = tuple(case.fingerprint() for case in self.cases)
        if len(case_fingerprints) != len(set(case_fingerprints)):
            raise ValueError("duplicate case fingerprint in optimization outcome")

        candidate_ids = tuple(
            evaluation.candidate.candidate_id for evaluation in self.candidates
        )
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("duplicate candidate ID in optimization outcome")
        known_candidates = set(candidate_ids)
        for label, values in (
            ("contender", self.contender_ids),
            ("objective front", self.objective_front),
            ("preference front", self.preference_front),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} candidate")
            if set(values) - known_candidates:
                raise ValueError(f"{label} contains an unknown candidate")
        if self.selected_candidate_id is not None and (
            self.selected_candidate_id not in known_candidates
            or self.selected_candidate_id not in self.contender_ids
        ):
            raise ValueError("selected candidate must be a recorded contender")

        packet_ids = tuple(packet.packet_id for packet in self.packet_archive)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("duplicate packet ID in optimization outcome")
        known_packets = set(packet_ids)
        packets_by_id = {
            packet.packet_id: packet for packet in self.packet_archive
        }
        known_case_ids = set(case_ids)
        known_case_fingerprints = set(case_fingerprints)
        if any(
            packet.case_id not in known_case_ids
            for packet in self.packet_archive
        ):
            raise ValueError("packet archive references an unknown case")
        for evaluation in self.candidates:
            execution_fingerprints = {
                execution.case_fingerprint for execution in evaluation.executions
            }
            if execution_fingerprints - known_case_fingerprints:
                raise ValueError("candidate execution references an unknown case")
            if evaluation.feasible and execution_fingerprints != known_case_fingerprints:
                raise ValueError(
                    "feasible candidate must contain the complete matched case set"
                )
            if set(evaluation.packet_ids) - known_packets:
                raise ValueError("candidate references an unknown packet")
            packet_case_ids = {
                packets_by_id[packet_id].case_id
                for packet_id in evaluation.packet_ids
                if packet_id in packets_by_id
            }
            if (
                evaluation.candidate.candidate_id in self.objective_front
                and packet_case_ids != known_case_ids
            ):
                raise ValueError(
                    "objective-front candidate packets must cover the matched case set"
                )

        successful_reasons = {
            OptimizationStopReason.OBJECTIVE_SINGLETON,
            OptimizationStopReason.CONFIDENT_PREFERENCE,
            OptimizationStopReason.VERBOSITY_POLICY_SINGLETON,
        }
        if self.stop_reason in successful_reasons:
            if self.selected_candidate_id is None:
                raise ValueError("successful stop reason requires a selected candidate")
        elif self.selected_candidate_id is not None:
            raise ValueError("stop reason does not permit a selected candidate")
        if self.selected_candidate_id is not None and (
            self.selected_candidate_id not in self.objective_front
            or self.selected_candidate_id not in self.preference_front
        ):
            raise ValueError("selected candidate must belong to both final fronts")
        feasible_ids = {
            evaluation.candidate.candidate_id
            for evaluation in self.candidates
            if evaluation.feasible
        }
        if set(self.objective_front) - feasible_ids:
            raise ValueError("objective front contains an infeasible candidate")
        if set(self.preference_front) - set(self.objective_front):
            raise ValueError("preference front must be a subset of the objective front")

        known_preferences = set(self.policy.epsilon)
        for attempt in self.archive.attempts:
            if attempt.case_id not in known_case_ids:
                raise ValueError("panel attempt references an unknown case")
            if {
                attempt.candidate_a_id,
                attempt.candidate_b_id,
            } - known_candidates:
                raise ValueError("panel attempt references an unknown candidate")
        for observation in self.archive.observations:
            if observation.case_id not in known_case_ids:
                raise ValueError("judge observation references an unknown case")
            if {
                observation.candidate_a_id,
                observation.candidate_b_id,
            } - known_candidates:
                raise ValueError("judge observation references an unknown candidate")
            if observation.preference_id not in known_preferences:
                raise ValueError("judge observation references an unknown preference")
        for observation in self.archive.policy_observations:
            if observation.case_id not in known_case_ids:
                raise ValueError("policy observation references an unknown case")
            if {
                observation.candidate_a_id,
                observation.candidate_b_id,
            } - known_candidates:
                raise ValueError("policy observation references an unknown candidate")
            if set(observation.preference_ids) != known_preferences:
                raise ValueError(
                    "policy observation preferences must match frozen policy"
                )
        for snapshot in self.model_snapshots:
            if set(snapshot.candidate_ids) - known_candidates:
                raise ValueError("preference model references an unknown candidate")
            if {
                model.preference_id for model in snapshot.models
            } != known_preferences:
                raise ValueError(
                    "preference model criteria must match frozen policy"
                )

        mutation_keys = tuple(
            (record.mutation_id, record.parent_id, record.child_id)
            for record in self.mutation_records
        )
        if len(mutation_keys) != len(set(mutation_keys)):
            raise ValueError("duplicate mutation record in optimization outcome")
        candidates_by_id = {
            evaluation.candidate.candidate_id: evaluation.candidate
            for evaluation in self.candidates
        }
        for record in self.mutation_records:
            if record.parent_id not in known_candidates or record.child_id not in known_candidates:
                raise ValueError("mutation record references an unknown candidate")
            child = candidates_by_id[record.child_id]
            if (
                child.parent_id != record.parent_id
                or child.mutation_id != record.mutation_id
                or child.generation != record.generation
            ):
                raise ValueError("mutation record differs from child lineage")

        expected_indices = tuple(range(len(self.events)))
        actual_indices = tuple(event.index for event in self.events)
        if actual_indices != expected_indices:
            raise ValueError("optimization event indices must be contiguous from zero")
        if not self.events or self.events[-1].state is not self.state:
            raise ValueError("final optimization event must match outcome state")
        if self.state is not OptimizationState.STOPPED:
            raise ValueError("optimization outcome must be stopped")
        return self


__all__ = [
    "CandidateEvaluation",
    "CaseExecution",
    "CostUnit",
    "ECPS_ALGORITHM_VERSION",
    "ECPS_SCHEMA_VERSION",
    "EvaluationCase",
    "MutationRecord",
    "OptimizationBudget",
    "OptimizationCandidate",
    "OptimizationEvent",
    "OptimizationLedger",
    "OptimizationOutcome",
    "OptimizationPolicy",
    "OptimizationState",
    "OptimizationStopReason",
]
