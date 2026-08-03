"""Token-bounded summarizer.

Framework-only implementation: concatenate + truncate by character budget.
A real LLM-based summarizer can be plugged in by replacing `summarize()`.
"""

from __future__ import annotations

from typing import Iterable


def approx_tokens(text: str) -> int:
    """Rough token estimate (chars / 4)."""
    return max(1, len(text) // 4)


def summarize(chunks: Iterable[str], max_tokens: int = 1000, join: str = "\n") -> str:
    budget_chars = max_tokens * 4
    buf: list[str] = []
    used = 0
    for c in chunks:
        if not c:
            continue
        take = c[: max(0, budget_chars - used)]
        if not take:
            break
        buf.append(take)
        used += len(take) + len(join)
        if used >= budget_chars:
            break
    return join.join(buf)


__all__ = ["summarize", "approx_tokens"]
