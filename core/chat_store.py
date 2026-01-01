from __future__ import annotations

from typing import Any

from utils import ensure_dir

try:
    from agents import SQLiteSession
except Exception:  # noqa: BLE001 - optional dependency
    SQLiteSession = None


class ChatStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        ensure_dir(db_path.rsplit("/", 1)[0] if "/" in db_path else ".")

    def append_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        session = self._session(session_id)
        if session is None:
            return
        session.add_items(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        )

    def get_recent(self, session_id: str, limit_turns: int = 8) -> list[dict[str, Any]]:
        session = self._session(session_id)
        if session is None:
            return []
        items = session.get_items() or []
        normalized: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(item)
            elif hasattr(item, "model_dump"):
                normalized.append(item.model_dump())
            elif hasattr(item, "to_dict"):
                normalized.append(item.to_dict())
            else:
                role = getattr(item, "role", None)
                content = getattr(item, "content", None)
                if role and content is not None:
                    normalized.append({"role": role, "content": content})
        if limit_turns <= 0:
            return normalized
        return normalized[-limit_turns * 2 :]

    def undo_last_turn(self, session_id: str) -> None:
        session = self._session(session_id)
        if session is None:
            return
        for _ in range(2):
            try:
                session.pop_item()
            except Exception:
                break

    def clear(self, session_id: str) -> None:
        session = self._session(session_id)
        if session is None:
            return
        session.clear_session()

    def _session(self, session_id: str):
        if SQLiteSession is None:
            return None
        return SQLiteSession(session_id, self.db_path)
