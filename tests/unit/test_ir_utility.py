"""Multi-objective utility: dominance, Pareto fronts, hypervolume, synergy.

These are the mechanics that replace v1's hand-weighted scalar score. The
properties worth protecting are that unmeasured objectives earn no credit,
that incomparable candidates are reported as incomparable rather than
arbitrarily ranked, and that synergy can come out negative.
"""

from __future__ import annotations

import pytest

from agentcoop.ir.utility import (
    Objective,
    SelectionPolicy,
    UtilityVector,
    dominates,
    hypervolume,
    pareto_front,
    select,
    synergy,
)


def u(**kwargs: float) -> UtilityVector:
    return UtilityVector.of(**kwargs)


class TestOrientation:
    def test_cost_and_risk_are_inverted(self) -> None:
        """Raw cost of 0.9 is bad; oriented it must be worse than 0.1."""
        cheap, pricey = u(cost=0.1), u(cost=0.9)
        assert cheap.oriented(Objective.COST) > pricey.oriented(Objective.COST)

    def test_validity_is_not_inverted(self) -> None:
        assert u(validity=0.9).oriented(Objective.VALIDITY) == 0.9

    def test_unmeasured_objective_returns_none(self) -> None:
        assert u(validity=1.0).oriented(Objective.COST) is None
        assert Objective.COST in u(validity=1.0).unavailable


class TestDominance:
    def test_strict_improvement_dominates(self) -> None:
        better = u(validity=1.0, evidence=0.9, cost=0.1)
        worse = u(validity=1.0, evidence=0.5, cost=0.3)
        assert dominates(better, worse)
        assert not dominates(worse, better)

    def test_trade_offs_are_incomparable(self) -> None:
        """Better evidence but higher cost is not 'better'. v1 would have
        resolved this with an invented exchange rate."""
        a = u(evidence=0.9, cost=0.4)
        b = u(evidence=0.6, cost=0.2)
        assert not dominates(a, b) and not dominates(b, a)

    def test_identical_vectors_do_not_dominate_each_other(self) -> None:
        a, b = u(validity=1.0, cost=0.2), u(validity=1.0, cost=0.2)
        assert not dominates(a, b) and not dominates(b, a)

    def test_no_shared_measured_dimension_means_no_dominance(self) -> None:
        """Honest incomparability rather than an invented winner."""
        assert not dominates(u(validity=1.0), u(cost=0.1))

    def test_dominance_is_decided_only_on_shared_dimensions(self) -> None:
        """``a`` measured an extra objective and scored well on it, but the only
        dimension both sides measured is a tie — so neither dominates. Counting
        the unshared dimension would reward measuring less, or more, arbitrarily.
        """
        a = u(validity=1.0, evidence=0.9)
        b = u(validity=1.0)
        assert a.comparable_with(b) == {Objective.VALIDITY}
        assert not dominates(a, b)
        assert not dominates(b, a)

    def test_dominance_holds_when_the_shared_dimension_differs(self) -> None:
        a = u(validity=1.0, evidence=0.9)
        b = u(validity=0.5)
        assert dominates(a, b)
        assert not dominates(b, a)

    def test_restricting_dimensions_changes_the_verdict(self) -> None:
        a = u(evidence=0.9, cost=0.9)
        b = u(evidence=0.5, cost=0.1)
        assert not dominates(a, b)
        assert dominates(a, b, strict_dims={Objective.EVIDENCE})


class TestParetoFront:
    def test_front_keeps_incomparable_and_drops_dominated(self) -> None:
        candidates = [
            ("cheap_weak", u(evidence=0.4, cost=0.1)),
            ("rich_pricey", u(evidence=0.95, cost=0.8)),
            ("dominated", u(evidence=0.3, cost=0.5)),
        ]
        front = pareto_front(candidates)
        assert set(front) == {"cheap_weak", "rich_pricey"}

    def test_single_candidate_is_its_own_front(self) -> None:
        assert pareto_front([("only", u(validity=1.0))]) == ["only"]

    def test_empty_input(self) -> None:
        assert pareto_front([]) == []

    def test_front_preserves_input_order(self) -> None:
        candidates = [("b", u(evidence=0.9, cost=0.9)), ("a", u(evidence=0.1, cost=0.1))]
        assert pareto_front(candidates) == ["b", "a"]


class TestHypervolume:
    def test_empty_is_zero(self) -> None:
        assert hypervolume([]) == 0.0

    def test_shared_context_prevents_everything_scoring_one(self) -> None:
        """Normalizing each vector in isolation makes every candidate score 1.0
        and every comparison a tie. The context argument exists for this."""
        strong = u(validity=1.0, evidence=0.95, cost=0.1)
        weak = u(validity=1.0, evidence=0.30, cost=0.9)
        assert hypervolume([strong]) == hypervolume([weak]) == 1.0

        ctx = [strong, weak]
        assert hypervolume([strong], context=ctx) > hypervolume([weak], context=ctx)

    def test_unmeasured_dimensions_earn_no_credit(self) -> None:
        full = u(validity=1.0, evidence=0.9, cost=0.1)
        partial = u(validity=1.0)
        ctx = [full, partial]
        assert hypervolume([partial], context=ctx) == 0.0
        assert hypervolume([full], context=ctx) > 0.0

    def test_front_of_two_exceeds_either_alone(self) -> None:
        a = u(evidence=0.9, cost=0.9)
        b = u(evidence=0.1, cost=0.1)
        ctx = [a, b]
        both = hypervolume([a, b], context=ctx)
        assert both > hypervolume([a], context=ctx)
        assert both > hypervolume([b], context=ctx)

    def test_is_deterministic_across_repeated_calls(self) -> None:
        """No RNG state; the reported number must be reproducible."""
        vectors = [u(validity=1.0, evidence=0.9, cost=0.2, robustness=0.5)]
        assert hypervolume(vectors) == hypervolume(vectors)

    def test_large_front_uses_sampling_and_stays_deterministic(self) -> None:
        vectors = [
            u(validity=1.0, evidence=i / 20.0, cost=1.0 - i / 20.0, robustness=0.5)
            for i in range(20)
        ]
        first = hypervolume(vectors, samples=4000)
        assert first == hypervolume(vectors, samples=4000)
        assert 0.0 < first <= 1.0

    def test_dominated_points_do_not_inflate_volume(self) -> None:
        best = u(evidence=0.9, cost=0.1)
        worse = u(evidence=0.5, cost=0.5)
        ctx = [best, worse]
        assert hypervolume([best], context=ctx) == pytest.approx(
            hypervolume([best, worse], context=ctx)
        )


class TestSelection:
    def test_lexicographic_respects_declared_priority(self) -> None:
        candidates = [
            ("thorough", u(validity=1.0, evidence=0.9, cost=0.8)),
            ("cheap", u(validity=1.0, evidence=0.4, cost=0.1)),
        ]
        chosen, front, rationale = select(candidates, SelectionPolicy())
        assert chosen == "thorough"
        assert "evidence" in rationale
        assert set(front) == {"thorough", "cheap"}

    def test_floors_exclude_before_selection(self) -> None:
        candidates = [
            ("invalid", u(validity=0.0, evidence=0.99, cost=0.01)),
            ("valid", u(validity=1.0, evidence=0.5, cost=0.5)),
        ]
        policy = SelectionPolicy(floors={Objective.VALIDITY: 1.0})
        chosen, _, _ = select(candidates, policy)
        assert chosen == "valid", "a workflow that violates hard invariants is not eligible"

    def test_all_excluded_reports_why(self) -> None:
        policy = SelectionPolicy(floors={Objective.VALIDITY: 1.0})
        chosen, front, rationale = select([("bad", u(validity=0.2))], policy)
        assert chosen is None and front == []
        assert "floor" in rationale

    def test_expert_mode_declines_to_choose(self) -> None:
        """When only expert preference can decide, the system must hand back the
        front rather than pick for them."""
        candidates = [("a", u(evidence=0.9, cost=0.9)), ("b", u(evidence=0.1, cost=0.1))]
        chosen, front, rationale = select(candidates, SelectionPolicy(mode="expert"))
        assert chosen is None
        assert set(front) == {"a", "b"}
        assert "expert" in rationale

    def test_weighted_mode_is_used_only_when_asked_for(self) -> None:
        candidates = [
            ("thorough", u(validity=1.0, evidence=0.9, cost=0.8)),
            ("cheap", u(validity=1.0, evidence=0.4, cost=0.1)),
        ]
        policy = SelectionPolicy(mode="weighted", weights={Objective.COST: 10.0})
        chosen, _, rationale = select(candidates, policy)
        assert chosen == "cheap"
        assert "declared by user" in rationale

    def test_single_non_dominated_candidate_short_circuits(self) -> None:
        candidates = [("best", u(evidence=0.9, cost=0.1)), ("worse", u(evidence=0.5, cost=0.5))]
        chosen, front, rationale = select(candidates, SelectionPolicy())
        assert chosen == "best" and front == ["best"]
        assert "single non-dominated" in rationale

    def test_no_candidates(self) -> None:
        chosen, front, rationale = select([], SelectionPolicy())
        assert chosen is None and front == [] and rationale == "no candidates"


class TestSynergy:
    def test_reports_negative_when_a_baseline_dominates(self) -> None:
        """The number that answers 'why not just use one strong agent', and it
        must be able to come out against composition."""
        composed = u(validity=1.0, evidence=0.5, cost=0.9, robustness=0.4)
        single = u(validity=1.0, evidence=0.8, cost=0.2, robustness=0.7)
        report = synergy("composed", composed, [("best_single", single)])
        assert report.dominated_by_baseline
        assert report.hypervolume_synergy < 0
        assert "NEGATIVE" in report.verdict

    def test_reports_positive_when_composition_genuinely_wins(self) -> None:
        composed = u(validity=1.0, evidence=0.9, cost=0.3, robustness=0.8)
        single = u(validity=1.0, evidence=0.4, cost=0.5, robustness=0.3)
        report = synergy("composed", composed, [("best_single", single)])
        assert not report.dominated_by_baseline
        assert report.hypervolume_synergy > 0
        assert "positive synergy" in report.verdict

    def test_symmetric_trade_off_yields_no_synergy(self) -> None:
        composed = u(evidence=0.9, cost=0.4)
        single = u(evidence=0.6, cost=0.2)
        report = synergy("composed", composed, [("best_single", single)])
        assert report.hypervolume_synergy == pytest.approx(0.0, abs=1e-9)
        assert "no measurable synergy" in report.verdict

    def test_margins_are_per_objective_and_oriented(self) -> None:
        composed = u(evidence=0.9, cost=0.2)
        single = u(evidence=0.6, cost=0.5)
        report = synergy("composed", composed, [("s", single)])
        assert report.margins[Objective.EVIDENCE] == pytest.approx(0.3)
        assert report.margins[Objective.COST] == pytest.approx(0.3), "cheaper must be a positive margin"

    def test_strongest_of_several_baselines_is_used(self) -> None:
        composed = u(validity=1.0, evidence=0.7, cost=0.3)
        weak = u(validity=1.0, evidence=0.2, cost=0.9)
        strong = u(validity=1.0, evidence=0.95, cost=0.1)
        report = synergy("composed", composed, [("weak", weak), ("strong", strong)])
        assert report.best_baseline == "strong"
        assert report.hypervolume_synergy < 0

    def test_missing_baselines_is_undefined_not_zero(self) -> None:
        report = synergy("composed", u(validity=1.0), [])
        assert "undefined" in report.verdict
        assert report.best_baseline is None


class TestVectorMutation:
    def test_with_value_clears_unavailability(self) -> None:
        v = u(validity=1.0)
        assert Objective.COST in v.unavailable
        v2 = v.with_value(Objective.COST, 0.3)
        assert Objective.COST not in v2.unavailable
        assert v2.get(Objective.COST) == 0.3

    def test_mark_unavailable_removes_the_value(self) -> None:
        v = u(validity=1.0, cost=0.3).mark_unavailable(Objective.COST)
        assert v.get(Objective.COST) is None
        assert Objective.COST in v.unavailable

    def test_updates_are_pure(self) -> None:
        v = u(validity=1.0)
        v.with_value(Objective.COST, 0.3)
        assert v.get(Objective.COST) is None
