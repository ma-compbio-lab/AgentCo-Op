from __future__ import annotations

import argparse
import os

from utils import ensure_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Memori SQLite storage.")
    parser.add_argument("--db-path", default="memory/agent_cop.db", help="Path to SQLite DB file.")
    args = parser.parse_args()

    try:
        from memori import Memori
    except Exception as exc:
        raise SystemExit(f"memori is not installed or failed to import: {exc}") from exc

    ensure_dir(os.path.dirname(args.db_path) or ".")
    url = f"sqlite:///{args.db_path}"
    mem = None
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
        except Exception as exc:
            raise SystemExit(f"Failed to initialize Memori: {exc}") from exc

    if mem is None:
        mem = Memori()

    config = getattr(mem, "config", None)
    storage = getattr(config, "storage", None)
    build = getattr(storage, "build", None)
    if callable(build):
        build()
    print(f"Memori storage initialized at {args.db_path}")


if __name__ == "__main__":
    main()
