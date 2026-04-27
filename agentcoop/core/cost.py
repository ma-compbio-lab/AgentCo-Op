"""Per-node + per-run cost tracker.

The tracker is storage-agnostic; callers feed it `NodeResult` deltas and it
aggregates totals. Pricing is loaded from a simple model → (in, out) USD-per-1k
table. Unknown models are priced at 0 (framework-only default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

DEFAULT_PRICE_TABLE_USD_PER_1K: Dict[str, tuple[float, float]] = {
    # Framework / mock defaults.
    "mock-llm": (0.0, 0.0),
    # OpenAI public pricing (USD per 1k tokens, in/out).
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o-mini-2024-07-18": (0.00015, 0.00060),
    "gpt-4o": (0.00250, 0.01000),
    "gpt-4o-2024-08-06": (0.00250, 0.01000),
    "gpt-4o-2024-11-20": (0.00250, 0.01000),
    "gpt-4-turbo": (0.01000, 0.03000),
    "gpt-3.5-turbo": (0.00050, 0.00150),
}


@dataclass
class CostLedger:
    price_table: Dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICE_TABLE_USD_PER_1K)
    )
    totals_by_node: Dict[str, float] = field(default_factory=dict)
    tokens_by_node: Dict[str, tuple[int, int]] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    def price(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p_in, p_out = self.price_table.get(model, (0.0, 0.0))
        return (tokens_in / 1000.0) * p_in + (tokens_out / 1000.0) * p_out

    def record(self, node_id: str, model: str, tokens_in: int, tokens_out: int) -> float:
        cost = self.price(model, tokens_in, tokens_out)
        self.totals_by_node[node_id] = self.totals_by_node.get(node_id, 0.0) + cost
        prev_in, prev_out = self.tokens_by_node.get(node_id, (0, 0))
        self.tokens_by_node[node_id] = (prev_in + tokens_in, prev_out + tokens_out)
        self.total_cost_usd += cost
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        return cost

    def over_budget(self, max_cost_usd: float | None) -> bool:
        if max_cost_usd is None:
            return False
        return self.total_cost_usd >= max_cost_usd

    def as_dict(self) -> dict:
        return {
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "by_node": {
                nid: {
                    "cost_usd": round(self.totals_by_node.get(nid, 0.0), 6),
                    "tokens_in": self.tokens_by_node.get(nid, (0, 0))[0],
                    "tokens_out": self.tokens_by_node.get(nid, (0, 0))[1],
                }
                for nid in self.totals_by_node.keys() | self.tokens_by_node.keys()
            },
        }


__all__ = ["CostLedger", "DEFAULT_PRICE_TABLE_USD_PER_1K"]
