from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetDecision:
    model_hint: str
    reason: str


class BudgetRouter:
    def __init__(self, default_model: str = "mock") -> None:
        self.default_model = default_model

    def choose(self, budget_tokens: int) -> BudgetDecision:
        if budget_tokens <= 2000:
            return BudgetDecision(model_hint=self.default_model, reason="low_token_budget")
        return BudgetDecision(model_hint=self.default_model, reason="default_budget")
