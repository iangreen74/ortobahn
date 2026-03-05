"""Tests for schema migration system."""

from __future__ import annotations

import pytest

from ortobahn.db import Database
from ortobahn.migrations import (
    EXPECTED_SCHEMA,
    _get_schema_version,
    _set_schema_version,
    get_schema_version,
    run_migrations,
    validate_schema,
)


class TestSchemaVersion:
    def test_version_after_init(self, test_db):
        assert _get_schema_version(test_db) == 42

    def test_set_and_get_version(self, test_db):
        _set_schema_version(test_db, 5)
        assert _get_schema_version(test_db) == 5

    def test_get_schema_version_public(self, test_db):
        """get_schema_version() is the public wrapper for _get_schema_version()."""
        assert get_schema_version(test_db) == _get_schema_version(test_db)

    def test_get_schema_version_returns_correct_value(self, tmp_path):
        """get_schema_version() returns the latest migration number on a fresh DB."""
        db = Database(tmp_path / "ver.db")
        assert get_schema_version(db) == 42
        db.close()


class TestMigrations:
    def test_all_tables_and_columns_exist(self, test_db):
        """Database constructor creates tables and runs all migrations."""
        # Verify clients table exists (migration 001)
        row = test_db.fetchone("SELECT * FROM clients WHERE id='default'")
        assert row is not None
        assert row["name"] == "Ortobahn"

        # Verify new columns on posts (migration 001)
        test_db.fetchall("SELECT client_id, platform, content_type FROM posts LIMIT 1")

        # Verify platform_uri columns (migration 002)
        test_db.fetchall("SELECT platform_uri, platform_id FROM posts LIMIT 1")

        # Verify email/status columns on clients (migration 003)
        test_db.fetchall("SELECT email, status FROM clients LIMIT 1")

        # Verify enrichment columns on clients (migration 004)
        test_db.fetchall(
            "SELECT products, competitive_positioning, key_messages, content_pillars, company_story FROM clients LIMIT 1"
        )

        # Verify new column on strategies
        test_db.fetchall("SELECT client_id FROM strategies LIMIT 1")

        # Verify new column on pipeline_runs
        test_db.fetchall("SELECT client_id FROM pipeline_runs LIMIT 1")

        # Verify auth tables (migration 007)
        test_db.fetchall("SELECT id, client_id, key_hash, key_prefix, name, active FROM api_keys LIMIT 1")
        test_db.fetchall("SELECT id, client_id, platform, credentials_encrypted FROM platform_credentials LIMIT 1")
        test_db.fetchall("SELECT internal, stripe_customer_id, subscription_status FROM clients LIMIT 1")

        # Verify default client is marked internal
        default_client = test_db.fetchone("SELECT internal FROM clients WHERE id='default'")
        assert default_client["internal"] == 1

        # Verify stripe_events table (migration 008)
        test_db.fetchall("SELECT id, event_type, processed_at FROM stripe_events LIMIT 1")

        # Verify engineering tables (migration 009)
        test_db.fetchall("SELECT id, title, description, priority, status, category FROM engineering_tasks LIMIT 1")
        test_db.fetchall("SELECT id, task_id, run_id, file_path, change_type FROM code_changes LIMIT 1")
        test_db.fetchall("SELECT id, task_id, status, commit_sha, total_input_tokens FROM cto_runs LIMIT 1")

        # Verify intelligence tables (migration 010)
        test_db.fetchall(
            "SELECT id, agent_name, client_id, memory_type, category, content, confidence, active FROM agent_memories LIMIT 1"
        )
        test_db.fetchall(
            "SELECT id, post_id, client_id, predicted_confidence, actual_engagement, calibration_error FROM confidence_calibration LIMIT 1"
        )
        test_db.fetchall(
            "SELECT id, client_id, hypothesis, variable, status, winner, pair_count FROM ab_experiments LIMIT 1"
        )
        test_db.fetchall(
            "SELECT id, agent_name, client_id, metric_name, target_value, current_value, trend FROM agent_goals LIMIT 1"
        )
        test_db.fetchall("SELECT id, run_id, client_id, period, confidence_accuracy FROM reflection_reports LIMIT 1")

        # Verify ci_fix_attempts table (migration 011)
        test_db.fetchall(
            "SELECT id, run_id, gh_run_id, failure_category, fix_strategy, status, validation_passed FROM ci_fix_attempts LIMIT 1"
        )

        # Verify watchdog tables (migration 018)
        test_db.fetchall("SELECT error_message FROM posts LIMIT 1")
        test_db.fetchall("SELECT id, probe, status, detail, client_id FROM health_checks LIMIT 1")
        test_db.fetchall(
            "SELECT id, finding_type, client_id, action, success, verified FROM watchdog_remediations LIMIT 1"
        )

        # Verify chat_messages table (migration 020)
        test_db.fetchall("SELECT id, client_id, role, content, created_at FROM chat_messages LIMIT 1")

        # Verify deployments table (migration 022)
        test_db.fetchall(
            "SELECT id, sha, environment, status, previous_sha, deployed_at, validated_at, rolled_back_at "
            "FROM deployments LIMIT 1"
        )

        # Verify intelligence upgrades (migration 023)
        test_db.fetchall("SELECT preferred_posting_hours FROM clients LIMIT 1")
        test_db.fetchall("SELECT last_rotated_at FROM platform_credentials LIMIT 1")

        # Verify engagement, serialization, timing tables (migration 024)
        test_db.fetchall(
            "SELECT id, run_id, client_id, notification_uri, reply_text, reply_uri, confidence "
            "FROM engagement_replies LIMIT 1"
        )
        test_db.fetchall(
            "SELECT id, client_id, series_title, current_part, max_parts, status FROM content_series LIMIT 1"
        )
        test_db.fetchall("SELECT series_id, series_part FROM posts LIMIT 1")
        test_db.fetchall(
            "SELECT id, topic_title, source, mention_count, velocity_score, peak_detected FROM topic_velocity LIMIT 1"
        )

        # Verify failure_category column on posts (migration 025)
        test_db.fetchall("SELECT failure_category FROM posts LIMIT 1")

        # Verify articles tables (migration 026)
        test_db.fetchall(
            "SELECT id, client_id, run_id, title, subtitle, body_markdown, tags, "
            "meta_description, topic_used, confidence, word_count, status FROM articles LIMIT 1"
        )
        test_db.fetchall(
            "SELECT id, article_id, platform, published_url, platform_id, status, error FROM article_publications LIMIT 1"
        )
        test_db.fetchall(
            "SELECT article_enabled, article_frequency, article_voice, article_platforms, "
            "article_topics, article_length, last_article_at FROM clients LIMIT 1"
        )

        # Verify webhooks table (migration 027)
        test_db.fetchall(
            "SELECT id, client_id, url, events, secret, active, created_at, "
            "last_triggered_at, failure_count FROM webhooks LIMIT 1"
        )

        # Verify article_publications recovery columns (migration 028)
        test_db.fetchall("SELECT failure_category, retry_count FROM article_publications LIMIT 1")

        # Verify platform_schedule column (migration 031)
        test_db.fetchall("SELECT platform_schedule FROM clients LIMIT 1")

        # Verify pipeline phase columns (migration 032)
        test_db.fetchall("SELECT current_phase, completed_phases, failed_phase, phase_data FROM pipeline_runs LIMIT 1")

    def test_idempotent(self, test_db):
        v1 = _get_schema_version(test_db)
        v2 = run_migrations(test_db)
        assert v1 == v2 == 42

    def test_database_constructor_runs_migrations(self, tmp_path):
        db = Database(tmp_path / "test.db")
        row = db.fetchone("SELECT * FROM clients WHERE id='default'")
        assert row is not None
        db.close()

    def test_idempotent_double_run(self, tmp_path):
        """Running migrations twice on the same DB should not error."""
        db = Database(tmp_path / "idem.db")
        v1 = run_migrations(db)
        v2 = run_migrations(db)
        assert v1 == v2
        db.close()

    def test_fresh_db_produces_expected_schema(self, tmp_path):
        """A fresh DB after all migrations should pass validate_schema cleanly."""
        db = Database(tmp_path / "fresh.db")
        problems = validate_schema(db)
        assert problems == [], f"Schema validation problems: {problems}"
        db.close()


class TestValidateSchema:
    def test_validate_schema_no_problems(self, test_db):
        """A fully migrated DB should have zero validation problems."""
        problems = validate_schema(test_db)
        assert problems == []

    def test_validate_schema_detects_missing_table(self, test_db):
        """Dropping a table should be reported by validate_schema."""
        test_db.execute("DROP TABLE IF EXISTS webhooks", commit=True)
        problems = validate_schema(test_db)
        assert any("webhooks" in p for p in problems)

    def test_validate_schema_detects_missing_column(self, test_db):
        """validate_schema detects a missing column when a table exists but
        is recreated without an expected column.

        SQLite doesn't support DROP COLUMN on older versions, so we simulate
        a missing column by checking against the schema expectation directly.
        We drop and recreate the table with a subset of columns.
        """
        # Recreate stripe_events without the expected 'event_type' column
        test_db.execute("DROP TABLE IF EXISTS stripe_events", commit=True)
        test_db.execute(
            "CREATE TABLE stripe_events (id TEXT PRIMARY KEY, processed_at TIMESTAMP)",
            commit=True,
        )
        problems = validate_schema(test_db)
        assert any("stripe_events.event_type" in p for p in problems)

    @pytest.mark.sqlite_only
    def test_validate_schema_expected_tables_complete(self, test_db):
        """Every table in EXPECTED_SCHEMA should exist in a migrated DB."""
        for table in EXPECTED_SCHEMA:
            rows = test_db.fetchall(f"PRAGMA table_info({table})")
            assert len(rows) > 0, f"Table {table!r} from EXPECTED_SCHEMA does not exist"
