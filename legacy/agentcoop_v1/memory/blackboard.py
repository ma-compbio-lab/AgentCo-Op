"""Typed, visibility-scoped shared blackboard.

The blackboard is the only cross-node channel. It stores typed artifacts,
short summaries, evidence references, and result schemas — never raw
hidden chain-of-thought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Visibility = Literal["private", "shared_read", "shared_write", "artifact_only"]


class BlackboardAccessError(PermissionError):
    pass


@dataclass
class _Entry:
    key: str
    value: Any
    author: str
    visibility: Visibility
    tags: list[str] = field(default_factory=list)


class Blackboard:
    """In-memory blackboard with per-key visibility rules.

    `private` entries are readable only by the author.
    `shared_read` entries are readable by anyone; only author can overwrite.
    `shared_write` entries may be overwritten by any node.
    `artifact_only` entries expose only metadata (path + size), never bytes.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    # -- write / read ---------------------------------------------------------

    def write(
        self,
        author: str,
        key: str,
        value: Any,
        *,
        visibility: Visibility = "shared_read",
        tags: list[str] | None = None,
    ) -> None:
        existing = self._entries.get(key)
        if existing and existing.visibility != "shared_write" and existing.author != author:
            raise BlackboardAccessError(
                f"node '{author}' cannot overwrite '{key}' authored by '{existing.author}'"
            )
        self._entries[key] = _Entry(key, value, author, visibility, list(tags or []))

    def read(self, requester: str, key: str) -> Any:
        e = self._entries.get(key)
        if e is None:
            raise KeyError(key)
        if e.visibility == "private" and e.author != requester:
            raise BlackboardAccessError(
                f"node '{requester}' cannot read private entry '{key}'"
            )
        return e.value

    def get(self, requester: str, key: str, default: Any = None) -> Any:
        try:
            return self.read(requester, key)
        except (KeyError, BlackboardAccessError):
            return default

    def has(self, requester: str, key: str) -> bool:
        e = self._entries.get(key)
        if e is None:
            return False
        if e.visibility == "private" and e.author != requester:
            return False
        return True

    def keys_for(self, requester: str) -> list[str]:
        return [
            k
            for k, e in self._entries.items()
            if e.visibility != "private" or e.author == requester
        ]

    def summarize_for(self, requester: str, max_items: int = 16) -> list[dict[str, Any]]:
        """Return a lightweight summary readable by `requester`.

        We emit shape only (`type`, length / keys) rather than raw values to
        avoid pulling large context into the downstream node.
        """
        out: list[dict[str, Any]] = []
        for key in self.keys_for(requester)[:max_items]:
            e = self._entries[key]
            out.append(
                {
                    "key": e.key,
                    "author": e.author,
                    "visibility": e.visibility,
                    "tags": list(e.tags),
                    "type": type(e.value).__name__,
                    "preview": _shape_preview(e.value),
                }
            )
        return out


def _shape_preview(value: Any) -> Any:
    if isinstance(value, dict):
        return {"dict_keys": list(value.keys())[:10]}
    if isinstance(value, list):
        return {"list_len": len(value)}
    if isinstance(value, str):
        return value[:80] + ("…" if len(value) > 80 else "")
    return type(value).__name__


__all__ = ["Blackboard", "BlackboardAccessError", "Visibility"]
