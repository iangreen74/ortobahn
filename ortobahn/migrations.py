"""Lightweight schema migration system for SQLite."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("ortobahn.migrations")


def _get_schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL DEFAULT 0)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()
        return 0
    return row[0] if isinstance(row, (tuple, list)) else row["version"]


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()


def _migration_001_add_clients_and_platform(conn: sqlite3.Connection) -> None:
    """Add clients table and extend posts/strategies/pipeline_runs with client_id/platform."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            industry TEXT NOT NULL DEFAULT '',
            target_audience TEXT NOT NULL DEFAULT '',
            brand_voice TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO clients (id, name, description, industry, target_audience, brand_voice)
        VALUES ('default', 'Ortobahn', 'Autonomous AI marketing engine', 'AI/Technology',
                'tech-savvy professionals, founders, AI enthusiasts', 'authoritative but approachable');
    """)

    # Add columns to existing tables (SQLite requires one ALTER per column)
    for stmt in [
        "ALTER TABLE strategies ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)",
        "ALTER TABLE posts ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)",
        "ALTER TABLE posts ADD COLUMN platform TEXT NOT NULL DEFAULT 'generic'",
        "ALTER TABLE posts ADD COLUMN content_type TEXT NOT NULL DEFAULT 'social_post'",
        "ALTER TABLE pipeline_runs ADD COLUMN client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.commit()


def _migration_002_add_platform_uri(conn: sqlite3.Connection) -> None:
    """Add platform-agnostic URI/ID columns to posts."""
    for stmt in [
        "ALTER TABLE posts ADD COLUMN platform_uri TEXT",
        "ALTER TABLE posts ADD COLUMN platform_id TEXT",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    # Backfill from bluesky columns
    conn.execute(
        "UPDATE posts SET platform_uri = bluesky_uri, platform_id = bluesky_cid "
        "WHERE bluesky_uri IS NOT NULL AND platform_uri IS NULL"
    )
    conn.commit()


def _migration_003_add_client_onboarding(conn: sqlite3.Connection) -> None:
    """Add email and status columns to clients for onboarding support."""
    for stmt in [
        "ALTER TABLE clients ADD COLUMN email TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.commit()


def _migration_004_add_client_enrichment(conn: sqlite3.Connection) -> None:
    """Add product/positioning/pillars/story columns to clients."""
    for stmt in [
        "ALTER TABLE clients ADD COLUMN products TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN competitive_positioning TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN key_messages TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN content_pillars TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN company_story TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.commit()


def _migration_005_add_monthly_budget(conn: sqlite3.Connection) -> None:
    """Add monthly_budget column to clients for CFO enforcement."""
    for stmt in [
        "ALTER TABLE clients ADD COLUMN monthly_budget REAL DEFAULT 0",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.commit()


def _migration_006_add_ab_testing(conn: sqlite3.Connection) -> None:
    """Add A/B testing columns to posts."""
    for stmt in [
        "ALTER TABLE posts ADD COLUMN ab_group TEXT DEFAULT NULL",
        "ALTER TABLE posts ADD COLUMN ab_pair_id TEXT DEFAULT NULL",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.commit()


def _migration_007_add_auth_and_credentials(conn: sqlite3.Connection) -> None:
    """Add API keys, sessions, and encrypted platform credentials."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES clients(id),
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'default',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS platform_credentials (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES clients(id),
            platform TEXT NOT NULL,
            credentials_encrypted TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(client_id, platform)
        );
    """)
    for stmt in [
        "ALTER TABLE clients ADD COLUMN internal INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN stripe_customer_id TEXT",
        "ALTER TABLE clients ADD COLUMN stripe_subscription_id TEXT",
        "ALTER TABLE clients ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'none'",
        "ALTER TABLE clients ADD COLUMN subscription_plan TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    conn.execute("UPDATE clients SET internal=1 WHERE id IN ('default', 'vaultscaler', 'ortobahn')")
    conn.commit()


def _migration_008_add_stripe_events(conn: sqlite3.Connection) -> None:
    """Add stripe events log for idempotent webhook processing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stripe_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def _migration_009_add_engineering_tasks(conn: sqlite3.Connection) -> None:
    """Add engineering task backlog and CTO agent tracking tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS engineering_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'backlog',
            category TEXT NOT NULL DEFAULT 'feature',
            estimated_complexity TEXT NOT NULL DEFAULT 'medium',
            created_by TEXT NOT NULL DEFAULT 'human',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            assigned_run_id TEXT,
            branch_name TEXT,
            files_changed TEXT,
            error TEXT,
            blocked_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS code_changes (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES engineering_tasks(id),
            run_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            change_type TEXT NOT NULL,
            diff_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cto_runs (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES engineering_tasks(id),
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'running',
            thinking_summary TEXT,
            files_read TEXT,
            files_written TEXT,
            tests_passed INTEGER,
            tests_failed INTEGER,
            commit_sha TEXT,
            error TEXT,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def _migration_010_add_intelligence_system(conn: sqlite3.Connection) -> None:
    """Add agent memory, confidence calibration, A/B experiments, goals, and reflection tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_memories (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT 'default',
            memory_type TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            source_run_id TEXT,
            source_post_ids TEXT,
            times_reinforced INTEGER DEFAULT 1,
            times_contradicted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            superseded_by TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_memories_agent_client
            ON agent_memories(agent_name, client_id, active);
        CREATE INDEX IF NOT EXISTS idx_memories_type
            ON agent_memories(memory_type, category);

        CREATE TABLE IF NOT EXISTS confidence_calibration (
            id TEXT PRIMARY KEY,
            post_id TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT 'default',
            predicted_confidence REAL NOT NULL,
            actual_engagement INTEGER DEFAULT 0,
            engagement_percentile REAL,
            calibration_error REAL,
            measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            run_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_calibration_client
            ON confidence_calibration(client_id, measured_at);

        CREATE TABLE IF NOT EXISTS ab_experiments (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL DEFAULT 'default',
            hypothesis TEXT NOT NULL,
            variable TEXT NOT NULL,
            variant_a_description TEXT NOT NULL,
            variant_b_description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            winner TEXT,
            pair_count INTEGER DEFAULT 0,
            min_pairs_required INTEGER DEFAULT 5,
            result_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            concluded_at TIMESTAMP,
            created_by_run_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_experiments_client
            ON ab_experiments(client_id, status);

        CREATE TABLE IF NOT EXISTS agent_goals (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT 'default',
            metric_name TEXT NOT NULL,
            target_value REAL NOT NULL,
            current_value REAL DEFAULT 0.0,
            trend TEXT DEFAULT 'stable',
            measurement_window_days INTEGER DEFAULT 7,
            last_measured_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_goals_agent
            ON agent_goals(agent_name, client_id);

        CREATE TABLE IF NOT EXISTS reflection_reports (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT 'default',
            period TEXT NOT NULL DEFAULT 'last_cycle',
            confidence_accuracy REAL,
            strategy_effectiveness TEXT,
            content_patterns TEXT,
            ab_test_results TEXT,
            goal_progress TEXT,
            new_memories TEXT,
            recommendations TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


MIGRATIONS = {
    1: _migration_001_add_clients_and_platform,
    2: _migration_002_add_platform_uri,
    3: _migration_003_add_client_onboarding,
    4: _migration_004_add_client_enrichment,
    5: _migration_005_add_monthly_budget,
    6: _migration_006_add_ab_testing,
    7: _migration_007_add_auth_and_credentials,
    8: _migration_008_add_stripe_events,
    9: _migration_009_add_engineering_tasks,
    10: _migration_010_add_intelligence_system,
}


def run_migrations(conn: sqlite3.Connection) -> int:
    """Run any pending migrations. Returns the final schema version."""
    current = _get_schema_version(conn)
    latest = max(MIGRATIONS.keys()) if MIGRATIONS else 0

    if current >= latest:
        return current

    for version in range(current + 1, latest + 1):
        if version in MIGRATIONS:
            logger.info(f"Running migration {version}...")
            MIGRATIONS[version](conn)
            _set_schema_version(conn, version)
            logger.info(f"Migration {version} complete.")

    return latest
