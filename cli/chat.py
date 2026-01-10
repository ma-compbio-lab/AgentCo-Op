from __future__ import annotations

import argparse
import sys
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


def _print_event(event: dict) -> None:
    if event.get("type") == "assistant_delta":
        print(event["data"]["delta"], end="", flush=True)
        return
    if event.get("type") == "assistant_final":
        print()
        return
    if event.get("type") == "error":
        message = event.get("data", {}).get("message", "")
        print(f"\n[error] {message}")
    if event.get("type") in {"log", "section"}:
        # Logs are already formatted by utils; skip duplicate printing.
        return


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
        event_bus.subscribe(_print_event)
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
