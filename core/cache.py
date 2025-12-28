from __future__ import annotations

from time import time
from typing import Any


class Cache:
    def __init__(self, ttl_s: int | None = None) -> None:
        self._ttl_s = ttl_s
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        ts, value = item
        if self._ttl_s is not None and (time() - ts) > self._ttl_s:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time(), value)
