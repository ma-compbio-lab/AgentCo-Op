from __future__ import annotations

from dataclasses import dataclass

from models import ModelRouting


@dataclass
class BudgetDecision:
    model_hint: str
    reason: str


class BudgetRouter:
    def __init__(self, routing: ModelRouting) -> None:
        self._routing = routing

    def choose(self, budget_tokens: int) -> BudgetDecision:
        if budget_tokens <= 2000:
            return BudgetDecision(model_hint=self._routing.worker, reason="low_token_budget")
        return BudgetDecision(model_hint=self._routing.worker, reason="default_budget")

    def model_for(self, role: str) -> str:
        if role == "planner":
            return self._routing.planner
        if role == "judge":
            return self._routing.judge
        if role == "aggregator":
            return self._routing.aggregator
        return self._routing.worker
