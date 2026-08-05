"""Deterministic criterion-wise preference inference and acquisition.

The models in this module consume only normalized judge observations.  They do
not reinterpret policy observations, diagnostic confidence, or judge scores as
scientific evidence.  All fitting and matrix operations use the standard
library so the optimizer remains deterministic and offline.
"""

from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist
from typing import Annotated, Mapping, Optional, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from agentcoop.ir.preference import (
    ObservationStatus,
    PairwisePreferenceObservation,
    PreferenceArchive,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_MIN_BACKTRACK_STEP = 2.0**-20
_PIVOT_EPSILON = 1e-15


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateEstimate(_FrozenModel):
    candidate_id: NonEmptyStr
    theta: float = Field(allow_inf_nan=False)
    standard_error: Optional[float] = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )


class CriterionModel(_FrozenModel):
    preference_id: NonEmptyStr
    estimates: tuple[CandidateEstimate, ...]
    covariance: tuple[tuple[float, ...], ...]
    connected: bool
    cycle_detected: bool
    converged: bool
    iterations: int = Field(ge=0)
    components: tuple[tuple[NonEmptyStr, ...], ...]
    contributing_families: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _validate_shape(self) -> "CriterionModel":
        if not self.estimates:
            raise ValueError("criterion model must contain at least one estimate")
        candidate_ids = tuple(estimate.candidate_id for estimate in self.estimates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("criterion model candidate IDs must be unique")
        if self.covariance:
            if len(self.covariance) != len(candidate_ids) or any(
                len(row) != len(candidate_ids) for row in self.covariance
            ):
                raise ValueError("criterion covariance must be square over candidates")
            if any(
                not math.isfinite(value)
                for row in self.covariance
                for value in row
            ):
                raise ValueError("criterion covariance must be finite")
        elif self.converged:
            raise ValueError("converged criterion model requires covariance")

        flattened = tuple(candidate for component in self.components for candidate in component)
        if (
            any(not component for component in self.components)
            or len(flattened) != len(set(flattened))
            or set(flattened) != set(candidate_ids)
        ):
            raise ValueError("criterion components must partition candidates")
        if tuple(sorted(set(self.contributing_families))) != self.contributing_families:
            raise ValueError("contributing judge families must be canonical and unique")
        return self


class PreferenceModelSet(_FrozenModel):
    models: tuple[CriterionModel, ...]
    candidate_ids: tuple[NonEmptyStr, ...]
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_models(self) -> "PreferenceModelSet":
        if not self.candidate_ids:
            raise ValueError("preference model set requires candidates")
        if tuple(sorted(set(self.candidate_ids))) != self.candidate_ids:
            raise ValueError("candidate IDs must be canonical and unique")
        preference_ids = tuple(model.preference_id for model in self.models)
        if not preference_ids or len(preference_ids) != len(set(preference_ids)):
            raise ValueError("preference IDs must be non-empty and unique")
        for model in self.models:
            if tuple(estimate.candidate_id for estimate in model.estimates) != self.candidate_ids:
                raise ValueError("every criterion model must use the model-set candidates")
        return self


class ComparisonTarget(_FrozenModel):
    candidate_a_id: NonEmptyStr
    candidate_b_id: NonEmptyStr
    case_id: NonEmptyStr
    acquisition: float = Field(ge=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_pair(self) -> "ComparisonTarget":
        if self.candidate_a_id >= self.candidate_b_id:
            raise ValueError("comparison target candidates must be canonical and distinct")
        return self


class _Row(_FrozenModel):
    candidate_a_id: NonEmptyStr
    candidate_b_id: NonEmptyStr
    case_id: NonEmptyStr
    judge_family: NonEmptyStr
    y: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


def _validated_ids(
    values: Sequence[str], *, label: str, sort: bool, allow_empty: bool = False
) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{label} must contain non-empty canonical strings")
        normalized.append(value)
    if not normalized and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    return tuple(sorted(normalized)) if sort else tuple(normalized)


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _collapse_observations(
    candidate_ids: tuple[str, ...],
    preference_ids: tuple[str, ...],
    observations: Sequence[PairwisePreferenceObservation],
) -> tuple[dict[str, tuple[_Row, ...]], tuple[str, ...]]:
    candidates = set(candidate_ids)
    preferences = set(preference_ids)
    collapsed: dict[
        tuple[str, str, str, str, str], set[float]
    ] = {}
    for observation in observations:
        if not isinstance(observation, PairwisePreferenceObservation):
            raise TypeError("observations must be PairwisePreferenceObservation records")
        if observation.preference_id not in preferences:
            continue
        if {
            observation.candidate_a_id,
            observation.candidate_b_id,
        } - candidates:
            continue
        if observation.status not in {
            ObservationStatus.DIRECTIONAL,
            ObservationStatus.TIE,
        }:
            continue
        candidate_a, candidate_b = sorted(
            (observation.candidate_a_id, observation.candidate_b_id)
        )
        if observation.status is ObservationStatus.TIE:
            y = 0.5
        else:
            y = 1.0 if observation.preferred_candidate_id == candidate_a else 0.0
        key = (
            observation.judge_family,
            candidate_a,
            candidate_b,
            observation.case_id,
            observation.preference_id,
        )
        collapsed.setdefault(key, set()).add(y)

    rows: dict[str, list[_Row]] = {preference_id: [] for preference_id in preference_ids}
    notes: list[str] = []
    for key in sorted(collapsed):
        judge_family, candidate_a, candidate_b, case_id, preference_id = key
        outcomes = collapsed[key]
        if len(outcomes) != 1:
            notes.append(
                "inconsistent evaluator key excluded: "
                f"{judge_family}/{candidate_a}/{candidate_b}/{case_id}/{preference_id}"
            )
            continue
        rows[preference_id].append(
            _Row(
                candidate_a_id=candidate_a,
                candidate_b_id=candidate_b,
                case_id=case_id,
                judge_family=judge_family,
                y=next(iter(outcomes)),
            )
        )
    return (
        {preference_id: tuple(rows[preference_id]) for preference_id in preference_ids},
        tuple(notes),
    )


def _components(
    candidate_ids: tuple[str, ...], rows: Sequence[_Row]
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, set[str]] = {
        candidate_id: set() for candidate_id in candidate_ids
    }
    for row in rows:
        adjacency[row.candidate_a_id].add(row.candidate_b_id)
        adjacency[row.candidate_b_id].add(row.candidate_a_id)

    remaining = set(candidate_ids)
    result: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        frontier = [root]
        discovered: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in discovered:
                continue
            discovered.add(current)
            frontier.extend(sorted(adjacency[current] - discovered, reverse=True))
        remaining -= discovered
        result.append(tuple(sorted(discovered)))
    return tuple(result)


def _has_majority_cycle(candidate_ids: tuple[str, ...], rows: Sequence[_Row]) -> bool:
    wins: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        if row.y == 0.5:
            continue
        counts = wins.setdefault((row.candidate_a_id, row.candidate_b_id), [0, 0])
        counts[0 if row.y == 1.0 else 1] += 1

    outgoing: dict[str, set[str]] = {
        candidate_id: set() for candidate_id in candidate_ids
    }
    for (candidate_a, candidate_b), (a_wins, b_wins) in wins.items():
        if a_wins > b_wins:
            outgoing[candidate_a].add(candidate_b)
        elif b_wins > a_wins:
            outgoing[candidate_b].add(candidate_a)

    state = {candidate_id: 0 for candidate_id in candidate_ids}

    def visit(candidate_id: str) -> bool:
        state[candidate_id] = 1
        for successor in sorted(outgoing[candidate_id]):
            if state[successor] == 1:
                return True
            if state[successor] == 0 and visit(successor):
                return True
        state[candidate_id] = 2
        return False

    return any(state[candidate_id] == 0 and visit(candidate_id) for candidate_id in candidate_ids)


def _basis(candidate_count: int) -> list[list[float]]:
    reduced = candidate_count - 1
    return [
        [1.0 if row == column else 0.0 for column in range(reduced)]
        if row < reduced
        else [-1.0 for _ in range(reduced)]
        for row in range(candidate_count)
    ]


def _theta(basis: Sequence[Sequence[float]], beta: Sequence[float]) -> list[float]:
    return [sum(coefficient * value for coefficient, value in zip(row, beta)) for row in basis]


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        negative = math.exp(-value)
        return 1.0 / (1.0 + negative)
    positive = math.exp(value)
    return positive / (1.0 + positive)


def _log_one_plus_exp(value: float) -> float:
    if value > 0.0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


def _row_vectors(
    candidate_ids: tuple[str, ...],
    rows: Sequence[_Row],
    basis: Sequence[Sequence[float]],
) -> tuple[tuple[tuple[float, ...], float], ...]:
    index = {candidate_id: position for position, candidate_id in enumerate(candidate_ids)}
    return tuple(
        (
            tuple(
                left - right
                for left, right in zip(
                    basis[index[row.candidate_a_id]],
                    basis[index[row.candidate_b_id]],
                )
            ),
            row.y,
        )
        for row in rows
    )


def _penalty_information(
    basis: Sequence[Sequence[float]], ridge: float
) -> list[list[float]]:
    reduced = len(basis[0]) if basis else 0
    return [
        [
            ridge * sum(row[i] * row[j] for row in basis)
            for j in range(reduced)
        ]
        for i in range(reduced)
    ]


def _objective(
    beta: Sequence[float],
    row_vectors: Sequence[tuple[tuple[float, ...], float]],
    basis: Sequence[Sequence[float]],
    ridge: float,
) -> float:
    value = 0.0
    for vector, y in row_vectors:
        difference = sum(coefficient * coordinate for coefficient, coordinate in zip(vector, beta))
        value += y * difference - _log_one_plus_exp(difference)
    theta = _theta(basis, beta)
    value -= 0.5 * ridge * sum(coordinate * coordinate for coordinate in theta)
    return value


def _objective_not_decreased(candidate: float, current: float) -> bool:
    """Compare objectives while accounting only for float representation error."""
    if candidate >= current:
        return True
    rounding = 8.0 * max(math.ulp(candidate), math.ulp(current))
    return current - candidate <= rounding


def _gradient_information(
    beta: Sequence[float],
    row_vectors: Sequence[tuple[tuple[float, ...], float]],
    penalty_information: Sequence[Sequence[float]],
) -> tuple[list[float], list[list[float]]]:
    reduced = len(beta)
    gradient = [0.0 for _ in range(reduced)]
    information = [list(row) for row in penalty_information]
    for vector, y in row_vectors:
        difference = sum(coefficient * coordinate for coefficient, coordinate in zip(vector, beta))
        probability = _sigmoid(difference)
        weight = probability * (1.0 - probability)
        for i in range(reduced):
            gradient[i] += (y - probability) * vector[i]
            for j in range(reduced):
                information[i][j] += weight * vector[i] * vector[j]
    for i in range(reduced):
        gradient[i] -= sum(
            penalty_information[i][j] * beta[j] for j in range(reduced)
        )
    return gradient, information


def _projected_theta_gradient_norm(reduced_gradient: Sequence[float]) -> float:
    """Recover the zero-sum tangent gradient from ``B.T @ gradient``."""
    candidate_count = len(reduced_gradient) + 1
    offset = math.fsum(reduced_gradient) / candidate_count
    projected = [value - offset for value in reduced_gradient]
    projected.append(-offset)
    return max(abs(value) for value in projected)


def _gauss_jordan(
    matrix: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> Optional[list[list[float]]]:
    size = len(matrix)
    if size == 0:
        return []
    if (
        any(len(row) != size for row in matrix)
        or len(right) != size
        or not right
    ):
        return None
    width = len(right[0])
    if any(len(row) != width for row in right):
        return None
    augmented = [
        [float(value) for value in matrix[row]]
        + [float(value) for value in right[row]]
        for row in range(size)
    ]
    if any(not math.isfinite(value) for row in augmented for value in row):
        return None

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: (abs(augmented[row][column]), -row))
        pivot = augmented[pivot_row][column]
        if not math.isfinite(pivot) or abs(pivot) <= _PIVOT_EPSILON:
            return None
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        pivot = augmented[column][column]
        augmented[column] = [value / pivot for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    result = [row[size:] for row in augmented]
    if any(not math.isfinite(value) for row in result for value in row):
        return None
    return result


def _solve(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> Optional[list[float]]:
    result = _gauss_jordan(matrix, [[value] for value in vector])
    return None if result is None else [row[0] for row in result]


def _inverse(matrix: Sequence[Sequence[float]]) -> Optional[list[list[float]]]:
    size = len(matrix)
    identity = [[1.0 if row == column else 0.0 for column in range(size)] for row in range(size)]
    return _gauss_jordan(matrix, identity)


def _full_covariance(
    basis: Sequence[Sequence[float]], inverse_information: Sequence[Sequence[float]]
) -> list[list[float]]:
    return [
        [
            sum(
                basis[i][left] * inverse_information[left][right] * basis[j][right]
                for left in range(len(inverse_information))
                for right in range(len(inverse_information))
            )
            for j in range(len(basis))
        ]
        for i in range(len(basis))
    ]


def _fit_criterion(
    candidate_ids: tuple[str, ...],
    preference_id: str,
    rows: tuple[_Row, ...],
    *,
    ridge: float,
    max_iterations: int,
    tolerance: float,
) -> CriterionModel:
    components = _components(candidate_ids, rows)
    connected = len(components) == 1
    cycle_detected = _has_majority_cycle(candidate_ids, rows)
    families = tuple(sorted({row.judge_family for row in rows}))
    if len(candidate_ids) == 1:
        return CriterionModel(
            preference_id=preference_id,
            estimates=(
                CandidateEstimate(
                    candidate_id=candidate_ids[0], theta=0.0, standard_error=0.0
                ),
            ),
            covariance=((0.0,),),
            connected=True,
            cycle_detected=False,
            converged=True,
            iterations=0,
            components=components,
            contributing_families=families,
        )

    basis = _basis(len(candidate_ids))
    row_vectors = _row_vectors(candidate_ids, rows, basis)
    penalty_information = _penalty_information(basis, ridge)
    beta = [0.0 for _ in range(len(candidate_ids) - 1)]
    converged = False
    iterations = 0

    for _ in range(max_iterations + 1):
        gradient, information = _gradient_information(
            beta, row_vectors, penalty_information
        )
        if not all(math.isfinite(value) for value in gradient) or not all(
            math.isfinite(value) for row in information for value in row
        ):
            break
        if _projected_theta_gradient_norm(gradient) <= tolerance:
            converged = True
            break
        if iterations >= max_iterations:
            break
        update = _solve(information, gradient)
        if update is None:
            break
        current_objective = _objective(beta, row_vectors, basis, ridge)
        accepted = False
        step = 1.0
        while step >= _MIN_BACKTRACK_STEP:
            candidate = [value + step * change for value, change in zip(beta, update)]
            candidate_objective = _objective(candidate, row_vectors, basis, ridge)
            if math.isfinite(candidate_objective) and _objective_not_decreased(
                candidate_objective, current_objective
            ):
                beta = candidate
                accepted = True
                iterations += 1
                break
            step *= 0.5
        if not accepted:
            break

    _, final_information = _gradient_information(beta, row_vectors, penalty_information)
    inverse_information = _inverse(final_information)
    if inverse_information is None:
        converged = False
        covariance: tuple[tuple[float, ...], ...] = ()
        standard_errors: list[Optional[float]] = [None for _ in candidate_ids]
    else:
        full_covariance = _full_covariance(basis, inverse_information)
        if any(not math.isfinite(value) for row in full_covariance for value in row):
            converged = False
            covariance = ()
            standard_errors = [None for _ in candidate_ids]
        else:
            covariance = tuple(tuple(value for value in row) for row in full_covariance)
            standard_errors = [math.sqrt(max(covariance[i][i], 0.0)) for i in range(len(candidate_ids))]

    fitted_theta = _theta(basis, beta)
    if any(not math.isfinite(value) for value in fitted_theta):
        fitted_theta = [0.0 for _ in candidate_ids]
        converged = False
    return CriterionModel(
        preference_id=preference_id,
        estimates=tuple(
            CandidateEstimate(
                candidate_id=candidate_id,
                theta=fitted_theta[index],
                standard_error=standard_errors[index],
            )
            for index, candidate_id in enumerate(candidate_ids)
        ),
        covariance=covariance,
        connected=connected,
        cycle_detected=cycle_detected,
        converged=converged,
        iterations=iterations,
        components=components,
        contributing_families=families,
    )


def fit_preference_models(
    candidate_ids: Sequence[str],
    preference_ids: Sequence[str],
    observations: Sequence[PairwisePreferenceObservation],
    *,
    ridge: float = 1.0,
    max_iterations: int = 100,
    tolerance: float = 1e-8,
) -> PreferenceModelSet:
    """Fit one independent, zero-sum Bradley--Terry model per criterion."""
    candidates = _validated_ids(candidate_ids, label="candidate_ids", sort=True)
    preferences = _validated_ids(preference_ids, label="preference_ids", sort=False)
    normalized_ridge = _finite_number(ridge, label="ridge")
    normalized_tolerance = _finite_number(tolerance, label="tolerance")
    if normalized_ridge < 0.0:
        raise ValueError("ridge must be non-negative")
    if normalized_tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 0
    ):
        raise ValueError("max_iterations must be a non-negative integer")

    rows, notes = _collapse_observations(candidates, preferences, observations)
    return PreferenceModelSet(
        models=tuple(
            _fit_criterion(
                candidates,
                preference_id,
                rows[preference_id],
                ridge=normalized_ridge,
                max_iterations=max_iterations,
                tolerance=normalized_tolerance,
            )
            for preference_id in preferences
        ),
        candidate_ids=candidates,
        notes=notes,
    )


def _estimate_index(model: CriterionModel) -> dict[str, int]:
    return {
        estimate.candidate_id: index
        for index, estimate in enumerate(model.estimates)
    }


def pair_probability(
    model: CriterionModel,
    candidate_a: str,
    candidate_b: str,
    margin: float = 0.0,
) -> float:
    """Return the fitted probability that A clears B by ``margin``."""
    normalized_margin = _finite_number(margin, label="margin")
    index = _estimate_index(model)
    if candidate_a not in index or candidate_b not in index:
        raise ValueError("pair candidates must exist in the criterion model")
    theta_a = model.estimates[index[candidate_a]].theta
    theta_b = model.estimates[index[candidate_b]].theta
    return _sigmoid(theta_a - theta_b - normalized_margin)


def _validated_decision_policy(
    models: PreferenceModelSet,
    epsilon: Mapping[str, float],
    delta: float,
) -> tuple[dict[str, float], float]:
    expected = tuple(model.preference_id for model in models.models)
    if not isinstance(epsilon, Mapping) or set(epsilon) != set(expected):
        raise ValueError("epsilon must cover every preference ID exactly")
    normalized_epsilon: dict[str, float] = {}
    for preference_id in expected:
        value = _finite_number(epsilon[preference_id], label="epsilon")
        if value < 0.0:
            raise ValueError("epsilon values must be non-negative")
        normalized_epsilon[preference_id] = value
    normalized_delta = _finite_number(delta, label="delta")
    if not 0.0 < normalized_delta < 1.0:
        raise ValueError("delta must be strictly between zero and one")
    return normalized_epsilon, normalized_delta


def _valid_for_decision(models: PreferenceModelSet) -> bool:
    if any("inconsistent evaluator key" in note for note in models.notes):
        return False
    size = len(models.candidate_ids)
    for model in models.models:
        if (
            not model.connected
            or model.cycle_detected
            or not model.converged
            or len(model.covariance) != size
            or any(len(row) != size for row in model.covariance)
            or any(not math.isfinite(value) for row in model.covariance for value in row)
            or any(not math.isfinite(estimate.theta) for estimate in model.estimates)
        ):
            return False
    return True


def _pair_variance(model: CriterionModel, candidate_a: str, candidate_b: str) -> Optional[float]:
    index = _estimate_index(model)
    if candidate_a not in index or candidate_b not in index:
        return None
    candidate_count = len(model.estimates)
    if len(model.covariance) != candidate_count or any(
        len(row) != candidate_count for row in model.covariance
    ):
        return None
    a = index[candidate_a]
    b = index[candidate_b]
    variance = (
        model.covariance[a][a]
        + model.covariance[b][b]
        - 2.0 * model.covariance[a][b]
    )
    return variance if math.isfinite(variance) else None


def preference_dominates(
    models: PreferenceModelSet,
    candidate_a: str,
    candidate_b: str,
    *,
    epsilon: Mapping[str, float],
    delta: float,
) -> bool:
    """Test simultaneous Bonferroni lower-bound dominance of A over B."""
    normalized_epsilon, normalized_delta = _validated_decision_policy(
        models, epsilon, delta
    )
    if candidate_a == candidate_b:
        return False
    if candidate_a not in models.candidate_ids or candidate_b not in models.candidate_ids:
        raise ValueError("dominance candidates must exist in the model set")
    if not _valid_for_decision(models):
        return False

    criterion_count = len(models.models)
    competitor_count = max(1, len(models.candidate_ids) - 1)
    alpha = normalized_delta / (criterion_count * competitor_count)
    quantile = 1.0 - alpha
    if not 0.0 < quantile < 1.0:
        return False
    z_score = NormalDist().inv_cdf(quantile)
    lower_bounds: list[tuple[float, float]] = []
    for model in models.models:
        index = _estimate_index(model)
        difference = (
            model.estimates[index[candidate_a]].theta
            - model.estimates[index[candidate_b]].theta
        )
        variance = _pair_variance(model, candidate_a, candidate_b)
        if variance is None:
            return False
        lower_bound = difference - z_score * math.sqrt(max(variance, 0.0))
        if not math.isfinite(lower_bound):
            return False
        lower_bounds.append((lower_bound, normalized_epsilon[model.preference_id]))
    return all(bound >= -slack for bound, slack in lower_bounds) and any(
        bound > slack for bound, slack in lower_bounds
    )


def preference_front(
    models: PreferenceModelSet,
    *,
    epsilon: Mapping[str, float],
    delta: float,
) -> tuple[str, ...]:
    """Return candidates not dominated by any current rival."""
    _validated_decision_policy(models, epsilon, delta)
    return tuple(
        candidate
        for candidate in models.candidate_ids
        if not any(
            preference_dominates(
                models,
                rival,
                candidate,
                epsilon=epsilon,
                delta=delta,
            )
            for rival in models.candidate_ids
            if rival != candidate
        )
    )


def _component_lookup(model: CriterionModel) -> dict[str, int]:
    return {
        candidate_id: component_index
        for component_index, component in enumerate(model.components)
        for candidate_id in component
    }


def choose_next_comparison(
    candidate_ids: Sequence[str],
    case_ids: Sequence[str],
    archive: PreferenceArchive,
    models: PreferenceModelSet,
) -> Optional[ComparisonTarget]:
    """Choose from the finite set of pair/case targets not yet attempted."""
    candidates = _validated_ids(candidate_ids, label="candidate_ids", sort=True)
    cases = _validated_ids(
        case_ids, label="case_ids", sort=True, allow_empty=True
    )
    if candidates != models.candidate_ids:
        raise ValueError("acquisition candidates must match fitted model candidates")
    attempted = {
        (
            *sorted((attempt.candidate_a_id, attempt.candidate_b_id)),
            attempt.case_id,
        )
        for attempt in archive.attempts
    }
    available = tuple(
        (candidate_a, candidate_b, case_id)
        for candidate_a, candidate_b in combinations(candidates, 2)
        for case_id in cases
        if (candidate_a, candidate_b, case_id) not in attempted
    )
    if not available:
        return None

    for model in models.models:
        if model.connected:
            continue
        component = _component_lookup(model)
        bridges = tuple(
            target
            for target in available
            if component.get(target[0]) != component.get(target[1])
        )
        if bridges:
            candidate_a, candidate_b, case_id = min(bridges)
            return ComparisonTarget(
                candidate_a_id=candidate_a,
                candidate_b_id=candidate_b,
                case_id=case_id,
                acquisition=0.0,
            )

    scored: list[tuple[float, tuple[str, str, str]]] = []
    for target in available:
        candidate_a, candidate_b, _ = target
        criterion_values: list[float] = []
        for model in models.models:
            variance = _pair_variance(model, candidate_a, candidate_b)
            if variance is None:
                continue
            probability = pair_probability(model, candidate_a, candidate_b)
            value = probability * (1.0 - probability) * max(variance, 0.0) / 4.0
            if math.isfinite(value):
                criterion_values.append(value)
        scored.append((max(criterion_values, default=0.0), target))
    best_value = max(value for value, _ in scored)
    candidate_a, candidate_b, case_id = min(
        target for value, target in scored if value == best_value
    )
    return ComparisonTarget(
        candidate_a_id=candidate_a,
        candidate_b_id=candidate_b,
        case_id=case_id,
        acquisition=best_value,
    )


def _dominates_every_rival(
    models: PreferenceModelSet,
    *,
    epsilon: Mapping[str, float],
    delta: float,
) -> Optional[str]:
    winners = tuple(
        candidate
        for candidate in models.candidate_ids
        if all(
            preference_dominates(
                models,
                candidate,
                rival,
                epsilon=epsilon,
                delta=delta,
            )
            for rival in models.candidate_ids
            if rival != candidate
        )
    )
    return winners[0] if len(winners) == 1 else None


def stable_selected_candidate(
    candidate_ids: Sequence[str],
    preference_ids: Sequence[str],
    archive: PreferenceArchive,
    *,
    epsilon: Mapping[str, float],
    delta: float,
    min_judge_families: int,
    ridge: float,
    max_iterations: int,
    tolerance: float,
) -> Optional[str]:
    """Return a winner only when full and leave-one-family-out fits agree."""
    if (
        isinstance(min_judge_families, bool)
        or not isinstance(min_judge_families, int)
        or min_judge_families <= 0
    ):
        raise ValueError("min_judge_families must be a positive integer")
    full = fit_preference_models(
        candidate_ids,
        preference_ids,
        archive.observations,
        ridge=ridge,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    _validated_decision_policy(full, epsilon, delta)
    if any(
        len(model.contributing_families) < min_judge_families
        for model in full.models
    ):
        return None
    winner = _dominates_every_rival(full, epsilon=epsilon, delta=delta)
    if winner is None:
        return None

    families = tuple(
        sorted(
            {
                family
                for model in full.models
                for family in model.contributing_families
            }
        )
    )
    for family in families:
        leave_one_out = fit_preference_models(
            candidate_ids,
            preference_ids,
            tuple(
                observation
                for observation in archive.observations
                if observation.judge_family != family
            ),
            ridge=ridge,
            max_iterations=max_iterations,
            tolerance=tolerance,
        )
        if _dominates_every_rival(
            leave_one_out, epsilon=epsilon, delta=delta
        ) != winner:
            return None
    return winner


__all__ = [
    "CandidateEstimate",
    "ComparisonTarget",
    "CriterionModel",
    "PreferenceModelSet",
    "choose_next_comparison",
    "fit_preference_models",
    "pair_probability",
    "preference_dominates",
    "preference_front",
    "stable_selected_candidate",
]
