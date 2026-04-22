from __future__ import annotations

from pathlib import Path

from agentcoop.core.cost import CostLedger
from agentcoop.core.tracing import TraceWriter, read_events


def test_trace_writes_jsonl(tmp_path: Path) -> None:
    w = TraceWriter(tmp_path)
    w.run_start("bp", "t")
    w.node_start("n", input_hash="abc")
    w.node_end("n", ok=True, tokens_in=10, tokens_out=20, cost_usd=0.01, latency_s=0.5, confidence=0.9)
    w.run_end("ok")
    events = list(read_events(w.path))
    assert [e["event"] for e in events] == ["run_start", "node_start", "node_end", "run_end"]
    assert events[2]["tokens_in"] == 10


def test_cost_ledger_tracks_per_node() -> None:
    ledger = CostLedger()
    ledger.price_table["foo"] = (0.01, 0.03)
    cost = ledger.record("n1", "foo", 1000, 500)
    assert abs(cost - 0.025) < 1e-9
    assert ledger.total_cost_usd == cost
    assert ledger.as_dict()["by_node"]["n1"]["tokens_out"] == 500
