from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TABLES = [
    "memori_conversation",
    "memori_conversation_message",
    "memori_session",
    "memori_entity_fact",
    "memori_process_attribute",
    "memori_knowledge_graph",
]


def clear_rows(conn: sqlite3.Connection, table: str, where: str | None, params: tuple) -> None:
    try:
        if where:
            conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        else:
            conn.execute(f"DELETE FROM {table}")
    except sqlite3.OperationalError:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear Memori SQLite storage.")
    parser.add_argument("--db-path", default="memory/agent_cop.db", help="Path to SQLite DB file.")
    parser.add_argument("--all", action="store_true", help="Clear all memory records.")
    parser.add_argument("--entity", help="Entity id to clear.")
    parser.add_argument("--process", help="Process id to clear.")
    parser.add_argument("--session", help="Session id to clear.")
    args = parser.parse_args()

    if args.all:
        if os.path.exists(args.db_path):
            os.remove(args.db_path)
            print(f"Removed {args.db_path}")
        else:
            print(f"No database found at {args.db_path}")
        return

    if not os.path.exists(args.db_path):
        raise SystemExit(f"No database found at {args.db_path}")

    clauses = []
    params: list[str] = []
    if args.entity:
        clauses.append("entity_id = ?")
        params.append(args.entity)
    if args.process:
        clauses.append("process_id = ?")
        params.append(args.process)
    if args.session:
        clauses.append("session_id = ?")
        params.append(args.session)
    where = " AND ".join(clauses) if clauses else None

    conn = sqlite3.connect(args.db_path)
    try:
        for table in TABLES:
            clear_rows(conn, table, where, tuple(params))
        conn.commit()
    finally:
        conn.close()

    print("Memory cleared.")


if __name__ == "__main__":
    main()
