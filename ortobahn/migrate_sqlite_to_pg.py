"""One-time migration: copy all data from SQLite to PostgreSQL.

Usage:
    DB_PATH=/app/data/ortobahn.db DATABASE_URL=postgresql://... python -m ortobahn.migrate_sqlite_to_pg
"""

from __future__ import annotations

import sqlite3
import sys

from ortobahn.config import load_settings
from ortobahn.db import Database

# Tables to migrate in dependency order (no foreign keys, but logical order).
TABLES = [
    "clients",
    "strategies",
    "posts",
    "metrics",
    "pipeline_runs",
    "agent_logs",
    "api_keys",
    "platform_credentials",
    "stripe_events",
    "engineering_tasks",
    "code_changes",
    "cto_runs",
    "agent_memories",
    "confidence_calibration",
    "ab_experiments",
    "agent_goals",
    "reflection_reports",
    "ci_fix_attempts",
]


def migrate():
    settings = load_settings()

    if not settings.database_url:
        print("ERROR: DATABASE_URL must be set (target PostgreSQL)")
        sys.exit(1)

    if not settings.db_path.exists():
        print(f"ERROR: SQLite database not found at {settings.db_path}")
        sys.exit(1)

    # Source: raw SQLite connection
    src = sqlite3.connect(str(settings.db_path))
    src.row_factory = sqlite3.Row

    # Target: PostgreSQL via Database abstraction (creates schema automatically)
    dst = Database(database_url=settings.database_url)

    print(f"Source: SQLite at {settings.db_path}")
    print(f"Target: PostgreSQL at {settings.database_url[:50]}...")
    print()

    total_rows = 0

    for table in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        except sqlite3.OperationalError:
            print(f"  {table}: table not found in source, skipping")
            continue

        if not rows:
            print(f"  {table}: 0 rows (empty)")
            continue

        columns = rows[0].keys()
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)

        inserted = 0
        for row in rows:
            values = tuple(row[c] for c in columns)
            try:
                dst.execute(
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values,
                    commit=True,
                )
                inserted += 1
            except Exception as e:
                print(f"    WARN: {table} row failed: {e}")

        print(f"  {table}: {inserted}/{len(rows)} rows migrated")
        total_rows += inserted

    src.close()
    dst.close()

    print(f"\nMigration complete: {total_rows} total rows copied.")
    print("Verify with: DATABASE_URL=... python -m ortobahn status")


if __name__ == "__main__":
    migrate()
