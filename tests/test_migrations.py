"""Tests for schema migration system."""

from __future__ import annotations

import sqlite3

import pytest

from ortobahn.migrations import _get_schema_version, _set_schema_version, run_migrations


@pytest.fixture
def raw_conn(tmp_path):
    """Raw SQLite connection without Database wrapper."""
    conn = sqlite3.connect(str(tmp_path / "migration_test.db"))
    conn.row_factory = sqlite3.Row
    return conn


class TestSchemaVersion:
    def test_initial_version_is_zero(self, raw_conn):
        assert _get_schema_version(raw_conn) == 0

    def test_set_and_get_version(self, raw_conn):
        _get_schema_version(raw_conn)  # init table
        _set_schema_version(raw_conn, 5)
        assert _get_schema_version(raw_conn) == 5


class TestMigrations:
    def test_run_on_fresh_db(self, raw_conn):
        # Create base tables first (like Database._create_tables does)
        raw_conn.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY, themes TEXT, tone TEXT, goals TEXT,
                content_guidelines TEXT, posting_frequency TEXT,
                created_at TIMESTAMP, valid_until TIMESTAMP, run_id TEXT, raw_llm_response TEXT
            );
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY, text TEXT, source_idea TEXT, reasoning TEXT,
                confidence REAL, status TEXT, bluesky_uri TEXT, bluesky_cid TEXT,
                published_at TIMESTAMP, created_at TIMESTAMP, run_id TEXT, strategy_id TEXT
            );
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY, mode TEXT, started_at TIMESTAMP,
                completed_at TIMESTAMP, status TEXT, posts_published INTEGER,
                errors TEXT, total_input_tokens INTEGER, total_output_tokens INTEGER
            );
        """)
        version = run_migrations(raw_conn)
        assert version == 9

        # Verify clients table exists (migration 001)
        row = raw_conn.execute("SELECT * FROM clients WHERE id='default'").fetchone()
        assert row is not None
        assert row["name"] == "Ortobahn"

        # Verify new columns on posts (migration 001)
        raw_conn.execute("SELECT client_id, platform, content_type FROM posts LIMIT 1")

        # Verify platform_uri columns (migration 002)
        raw_conn.execute("SELECT platform_uri, platform_id FROM posts LIMIT 1")

        # Verify email/status columns on clients (migration 003)
        raw_conn.execute("SELECT email, status FROM clients LIMIT 1")

        # Verify enrichment columns on clients (migration 004)
        raw_conn.execute(
            "SELECT products, competitive_positioning, key_messages, content_pillars, company_story FROM clients LIMIT 1"
        )

        # Verify new column on strategies
        raw_conn.execute("SELECT client_id FROM strategies LIMIT 1")

        # Verify new column on pipeline_runs
        raw_conn.execute("SELECT client_id FROM pipeline_runs LIMIT 1")

        # Verify auth tables (migration 007)
        raw_conn.execute("SELECT id, client_id, key_hash, key_prefix, name, active FROM api_keys LIMIT 1")
        raw_conn.execute("SELECT id, client_id, platform, credentials_encrypted FROM platform_credentials LIMIT 1")
        raw_conn.execute("SELECT internal, stripe_customer_id, subscription_status FROM clients LIMIT 1")

        # Verify default client is marked internal
        default_client = raw_conn.execute("SELECT internal FROM clients WHERE id='default'").fetchone()
        assert default_client["internal"] == 1

        # Verify stripe_events table (migration 008)
        raw_conn.execute("SELECT id, event_type, processed_at FROM stripe_events LIMIT 1")

        # Verify engineering tables (migration 009)
        raw_conn.execute("SELECT id, title, description, priority, status, category FROM engineering_tasks LIMIT 1")
        raw_conn.execute("SELECT id, task_id, run_id, file_path, change_type FROM code_changes LIMIT 1")
        raw_conn.execute("SELECT id, task_id, status, commit_sha, total_input_tokens FROM cto_runs LIMIT 1")

    def test_idempotent(self, raw_conn):
        raw_conn.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY, themes TEXT, tone TEXT, goals TEXT,
                content_guidelines TEXT, posting_frequency TEXT,
                created_at TIMESTAMP, valid_until TIMESTAMP, run_id TEXT, raw_llm_response TEXT
            );
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY, text TEXT, source_idea TEXT, reasoning TEXT,
                confidence REAL, status TEXT, bluesky_uri TEXT, bluesky_cid TEXT,
                published_at TIMESTAMP, created_at TIMESTAMP, run_id TEXT, strategy_id TEXT
            );
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY, mode TEXT, started_at TIMESTAMP,
                completed_at TIMESTAMP, status TEXT, posts_published INTEGER,
                errors TEXT, total_input_tokens INTEGER, total_output_tokens INTEGER
            );
        """)
        v1 = run_migrations(raw_conn)
        v2 = run_migrations(raw_conn)
        assert v1 == v2 == 9

    def test_database_constructor_runs_migrations(self, tmp_path):
        from ortobahn.db import Database

        db = Database(tmp_path / "test.db")
        # Verify clients table exists after construction
        row = db.conn.execute("SELECT * FROM clients WHERE id='default'").fetchone()
        assert row is not None
        db.close()
