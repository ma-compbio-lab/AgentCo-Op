from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omegaconf import OmegaConf

from chat.service import stream_chat_turn
from core.event_bus import EventBus
from run import to_app_config
from runtime import build_runtime
from utils import set_log_config


def build_runtime_from_config(app_cfg):
    from models import build_model_routing, configure_openai, ensure_api_key, validate_backend

    validate_backend(app_cfg.model.backend)
    configure_openai(app_cfg.model.api_key)
    ensure_api_key()
    routing = build_model_routing(app_cfg.model)
    return build_runtime(
        routing,
        log_dir=app_cfg.log_dir,
        repair=app_cfg.repair,
        memory_cfg=app_cfg.memory,
        tool_cfg=app_cfg.tool,
        mcp_cfg=app_cfg.mcp,
    )


_COLOR = {
    "reset": "\033[0m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "gray": "\033[90m",
    "red": "\033[31m",
}


def _colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLOR.get(color, '')}{text}{_COLOR['reset']}"


class _Spinner:
    def __init__(self, label: str, use_color: bool) -> None:
        self._label = label
        self._use_color = use_color
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames = ["|", "/", "-", "\\"]
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, clear_line: bool = True) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)
        if clear_line:
            with self._lock:
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = self._frames[idx % len(self._frames)]
            line = f"{frame} {self._label}"
            line = _colorize(line, "yellow", self._use_color)
            with self._lock:
                sys.stdout.write("\r" + line)
                sys.stdout.flush()
            idx += 1
            time.sleep(0.12)


class _ChatPrinter:
    def __init__(self, use_color: bool) -> None:
        self._use_color = use_color
        self._spinner = _Spinner("[BOT] thinking", use_color)
        self._response_started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._response_started = False
        self._spinner.start()

    def handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "assistant_delta":
            self._ensure_prefix()
            delta = event.get("data", {}).get("delta", "")
            with self._lock:
                sys.stdout.write(delta)
                sys.stdout.flush()
            return
        if event_type == "assistant_final":
            text = event.get("data", {}).get("text", "")
            if not self._response_started:
                self._ensure_prefix()
                with self._lock:
                    sys.stdout.write(text)
                    sys.stdout.flush()
            self._spinner.stop()
            with self._lock:
                sys.stdout.write("\n")
                sys.stdout.flush()
            return
        if event_type == "error":
            self._spinner.stop()
            message = event.get("data", {}).get("message", "")
            line = _colorize(f"[error] {message}", "red", self._use_color)
            with self._lock:
                sys.stdout.write("\n" + line + "\n")
                sys.stdout.flush()
            return
        if event_type in {"log", "section"}:
            # Logs are already formatted by utils; skip duplicate printing.
            return

    def _ensure_prefix(self) -> None:
        if self._response_started:
            return
        self._spinner.stop()
        prefix = _colorize("[BOT] ", "cyan", self._use_color)
        with self._lock:
            sys.stdout.write(prefix)
            sys.stdout.flush()
        self._response_started = True


def _read_prompt() -> str:
    try:
        from prompt_toolkit import prompt
    except Exception:
        return input("You> ").strip()
    return prompt("You> ").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-turn chat REPL")
    parser.add_argument("--config", default="conf/config.yaml")
    parser.add_argument("--session-id", default="default")
    parser.add_argument("overrides", nargs="*", help="Hydra-style overrides (key=value)")
    args = parser.parse_args()

    cfg = OmegaConf.load(args.config)
    if args.overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(args.overrides))
    app_cfg = to_app_config(cfg)

    set_log_config(
        enabled=app_cfg.log.enabled,
        level=app_cfg.log.level,
        use_color=app_cfg.log.use_color,
        use_icons=app_cfg.log.use_icons,
        preview_chars=app_cfg.log.preview_chars,
        show_prompts=app_cfg.log.show_prompts,
        show_outputs=app_cfg.log.show_outputs,
    )
    runtime = build_runtime_from_config(app_cfg)
    printer = _ChatPrinter(use_color=app_cfg.log.use_color)

    print("Agent-Cop chat. Type 'exit' to quit.")
    while True:
        try:
            user_text = _read_prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break
        event_bus = EventBus()
        event_bus.subscribe(printer.handle_event)
        printer.start()
        for _event in stream_chat_turn(
            user_text,
            app_cfg,
            session_id=args.session_id,
            event_bus=event_bus,
            runtime=runtime,
        ):
            pass


if __name__ == "__main__":
    main()
