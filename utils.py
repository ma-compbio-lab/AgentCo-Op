from __future__ import annotations

import json
import os
from datetime import datetime


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def append_jsonl(path: str, obj: dict) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=True) + "\n")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


_LOG_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_LOG_CONFIG = {
    "enabled": True,
    "level": "info",
    "use_color": True,
    "use_icons": True,
}


_COLORS = {
    "reset": "\033[0m",
    "gray": "\033[90m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


_MODULE_STYLE = {
    "RUN": ("blue", "[RUN]"),
    "ORCH": ("magenta", "[ORCH]"),
    "ENGINE": ("cyan", "[ENG]"),
    "JUDGE": ("magenta", "[JUDGE]"),
    "METHOD": ("yellow", "[METHOD]"),
    "RUNTIME": ("green", "[RT]"),
    "IO": ("gray", "[IO]"),
    "TOOLS": ("gray", "[TOOL]"),
}


def set_log_config(enabled: bool, level: str, use_color: bool = True, use_icons: bool = True) -> None:
    _LOG_CONFIG["enabled"] = enabled
    _LOG_CONFIG["level"] = level if level in _LOG_LEVELS else "info"
    _LOG_CONFIG["use_color"] = use_color
    _LOG_CONFIG["use_icons"] = use_icons


def _colorize(text: str, color: str) -> str:
    if not _LOG_CONFIG["use_color"]:
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def _format_data(data: dict | None) -> str:
    if not data:
        return ""
    payload = json.dumps(data, ensure_ascii=True)
    if len(payload) > 600:
        payload = payload[:600] + "..."
    return payload


def log_event(module: str, step: str, message: str, *, level: str = "info", data: dict | None = None) -> None:
    if not _LOG_CONFIG["enabled"]:
        return
    if _LOG_LEVELS.get(level, 20) < _LOG_LEVELS.get(_LOG_CONFIG["level"], 20):
        return
    color, icon = _MODULE_STYLE.get(module, ("gray", "[LOG]"))
    prefix = icon if _LOG_CONFIG["use_icons"] else module
    head = f"{prefix} {module}/{step}"
    line = f"{head} | {message}"
    payload = _format_data(data)
    if payload:
        line = f"{line} | {payload}"
    print(_colorize(line, color))


def log_section(module: str, title: str) -> None:
    if not _LOG_CONFIG["enabled"]:
        return
    color, icon = _MODULE_STYLE.get(module, ("gray", "[LOG]"))
    prefix = icon if _LOG_CONFIG["use_icons"] else module
    line = f"{prefix} {title}"
    print(_colorize(line, color))
