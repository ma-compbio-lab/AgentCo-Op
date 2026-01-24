from __future__ import annotations

import inspect
import os
import sqlite3
import threading
from typing import Any

from utils import ensure_dir


class Memory:
    _LOCKS: dict[str, threading.Lock] = {}

    def __init__(
        self,
        *,
        enabled: bool = False,
        db_path: str = "memory/agent_cop.db",
        entity_id: str = "default",
        session_id: str | None = None,
        top_k: int = 3,
        store_agent_outputs: bool = True,
        store_judge_reports: bool = True,
        auto_build: bool = False,
    ) -> None:
        self.enabled = enabled
        self.db_path = db_path
        self.entity_id = entity_id
        self.session_id = session_id
        self.top_k = top_k
        self.store_agent_outputs = store_agent_outputs
        self.store_judge_reports = store_judge_reports
        self.auto_build = auto_build

        self._working: dict[str, list[dict[str, Any]]] = {}
        self._episodic: list[dict[str, Any]] = []
        self._mem = None
        self._mem_available = False
        self._lock = self._get_lock(self.db_path)

        if self.enabled:
            self._init_memori()

    def add_working(self, run_id: str, item: dict[str, Any]) -> None:
        self._working.setdefault(run_id, []).append(item)

    def get_working(self, run_id: str) -> list[dict[str, Any]]:
        return self._working.get(run_id, [])

    def add_episodic(self, item: dict[str, Any]) -> None:
        self._episodic.append(item)

    def get_episodic(self) -> list[dict[str, Any]]:
        return list(self._episodic)

    def set_session(self, session_id: str | None) -> None:
        self.session_id = session_id
        if not self._mem_available or not session_id:
            return
        with self._lock:
            if hasattr(self._mem, "set_session"):
                try:
                    self._mem.set_session(session_id)
                    return
                except Exception:
                    return
            if hasattr(self._mem, "new_session"):
                try:
                    self._mem.new_session(session_id)
                except Exception:
                    return

    def recall_agent(self, agent_id: str, query: str, k: int | None = None) -> list[dict[str, Any]]:
        return self._recall(query, k, process_id=f"agent:{agent_id}")

    def recall_global(self, query: str, k: int | None = None) -> list[dict[str, Any]]:
        return self._recall(query, k, process_id="global:workflow")

    def write_agent(
        self, agent_id: str, text: str, *, labels: list[str] | None = None, category: str | None = None
    ) -> None:
        self._write(text, process_id=f"agent:{agent_id}", labels=labels, category=category)

    def write_global(self, text: str, *, labels: list[str] | None = None, category: str | None = None) -> None:
        self._write(text, process_id="global:workflow", labels=labels, category=category)

    def _init_memori(self) -> None:
        # Initialize Memori lazily so the rest of the system works without it.
        try:
            from memori import Memori
        except Exception:
            self._log_init_issue("memori import failed")
            return

        ensure_dir(os.path.dirname(self.db_path) or ".")
        mem = None
        try:
            sig = inspect.signature(Memori.__init__)
        except Exception:
            sig = None

        # Memori v3 expects a DBAPI connection factory; v1/v2 accept a SQLite URL.
        if sig and "conn" in sig.parameters:
            conn_factory = lambda: sqlite3.connect(self.db_path)
            try:
                mem = Memori(conn=conn_factory)
            except Exception:
                mem = None
        elif sig and "database_connect" in sig.parameters:
            url = f"sqlite:///{self.db_path}"
            kwargs = {"database_connect": url}
            if "schema_init" in sig.parameters:
                kwargs["schema_init"] = self.auto_build
            try:
                mem = Memori(**kwargs)
            except Exception:
                mem = None
        else:
            url = f"sqlite:///{self.db_path}"
            for kwargs in (
                {"storage_url": url},
                {"storage": {"url": url}},
                {"config": {"storage": {"url": url}}},
            ):
                try:
                    mem = Memori(**kwargs)
                    break
                except TypeError:
                    continue
                except Exception:
                    mem = None
                    break

        if mem is None:
            try:
                mem = Memori()
            except Exception:
                self._log_init_issue("memori init failed")
                return

        self._mem = mem
        self._mem_available = True
        if self.auto_build:
            self._build_storage()
        if self.session_id:
            self.set_session(self.session_id)

    def _build_storage(self) -> None:
        config = getattr(self._mem, "config", None)
        storage = getattr(config, "storage", None)
        build = getattr(storage, "build", None)
        if callable(build):
            try:
                build()
            except Exception:
                return

    def _log_init_issue(self, message: str) -> None:
        try:
            from utils import log_event
        except Exception:
            return
        log_event("RUNTIME", "memory_init", message, level="warn")

    def _apply_attribution(self, process_id: str) -> None:
        if not self._mem_available:
            return
        attr = getattr(self._mem, "attribution", None)
        if not callable(attr):
            return
        try:
            attr(entity_id=self.entity_id, process_id=process_id)
        except TypeError:
            try:
                attr(self.entity_id, process_id)
            except Exception:
                return
        except Exception:
            return

    def _recall(self, query: str, k: int | None, process_id: str) -> list[dict[str, Any]]:
        if not self.enabled or not self._mem_available or not query:
            return []
        with self._lock:
            self._apply_attribution(process_id)
            limit = k or self.top_k
            recall = getattr(self._mem, "recall", None)
            if not callable(recall):
                return []
            try:
                results = recall(query, limit=limit)
            except TypeError:
                try:
                    results = recall(query, limit)
                except Exception:
                    return []
            except Exception:
                return []
            return self._normalize_recall(results)

    def _write(self, text: str, process_id: str, labels: list[str] | None, category: str | None) -> None:
        if not self.enabled or not self._mem_available or not text:
            return
        with self._lock:
            self._apply_attribution(process_id)
            add_memory = getattr(self._mem, "add_memory", None)
            if callable(add_memory):
                try:
                    add_memory(text, labels=labels or [], category=category)
                    return
                except Exception:
                    return
            add = getattr(self._mem, "add", None)
            if callable(add):
                try:
                    add(text, labels=labels or [], category=category)
                except Exception:
                    return

    @classmethod
    def _get_lock(cls, db_path: str) -> threading.Lock:
        key = db_path or ":memory:"
        lock = cls._LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            cls._LOCKS[key] = lock
        return lock

    @staticmethod
    def _normalize_recall(results: Any) -> list[dict[str, Any]]:
        # Normalize Memori recall results into a stable list of dicts.
        items: list[dict[str, Any]] = []
        if results is None:
            return items
        if isinstance(results, dict):
            results = [results]
        for item in results:
            text = None
            score = None
            created_at = None
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("content") or item.get("text") or item.get("memory") or item.get("summary")
                score = item.get("score") or item.get("similarity") or item.get("distance")
                created_at = item.get("created_at") or item.get("timestamp")
            else:
                text = getattr(item, "content", None) or getattr(item, "text", None) or str(item)
                score = getattr(item, "score", None) or getattr(item, "similarity", None)
                created_at = getattr(item, "created_at", None)
            if not text:
                continue
            items.append(
                {
                    "text": text,
                    "score": score,
                    "created_at": str(created_at) if created_at else None,
                }
            )
        return items
