from __future__ import annotations

import argparse
import inspect
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import ensure_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Memori SQLite storage.")
    parser.add_argument("--db-path", default="memory/agent_cop.db", help="Path to SQLite DB file.")
    args = parser.parse_args()

    try:
        import sqlalchemy  # noqa: F401
    except Exception as exc:
        raise SystemExit(
            f"sqlalchemy is required by memori but is not installed: {exc}\n"
            "Install with: pip install sqlalchemy"
        ) from exc

    try:
        from memori import Memori
    except Exception as exc:
        raise SystemExit(f"memori is not installed or failed to import: {exc}") from exc

    ensure_dir(os.path.dirname(args.db_path) or ".")
    mem = None
    try:
        sig = inspect.signature(Memori.__init__)
    except Exception:
        sig = None

    if sig and "conn" in sig.parameters:
        conn_factory = lambda: sqlite3.connect(args.db_path)
        try:
            mem = Memori(conn=conn_factory)
        except Exception as exc:
            raise SystemExit(f"Failed to initialize Memori (conn): {exc}") from exc
    elif sig and "database_connect" in sig.parameters:
        url = f"sqlite:///{args.db_path}"
        kwargs = {"database_connect": url}
        if "schema_init" in sig.parameters:
            kwargs["schema_init"] = True
        try:
            mem = Memori(**kwargs)
        except Exception as exc:
            raise SystemExit(f"Failed to initialize Memori (database_connect): {exc}") from exc
    else:
        url = f"sqlite:///{args.db_path}"
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
        try:
            mem = Memori()
        except Exception as exc:
            raise SystemExit(f"Failed to initialize Memori: {exc}") from exc

    config = getattr(mem, "config", None)
    storage = getattr(config, "storage", None)
    build = getattr(storage, "build", None)
    if callable(build):
        build()
    print(f"Memori storage initialized at {args.db_path}")


if __name__ == "__main__":
    main()
