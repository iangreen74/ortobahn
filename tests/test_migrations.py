"""Tests for schema migration system."""

from __future__ import annotations

from ortobahn.db import Database
from ortobahn.migrations import _get_schema_version, _set_schema_version, run_migrations


class TestSchemaVersion:
    def test_version_after_init(self, test_db):
        assert _get_schema_version(test_db) == 17

    def test_set_and_get_version(self, test_db):
        _set_schema_version(test_db, 5)
        assert _get_schema_version(test_db) == 5


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

    def test_idempotent(self, test_db):
        v1 = _get_schema_version(test_db)
        v2 = run_migrations(test_db)
        assert v1 == v2 == 17

    def test_database_constructor_runs_migrations(self, tmp_path):
        db = Database(tmp_path / "test.db")
        row = db.fetchone("SELECT * FROM clients WHERE id='default'")
        assert row is not None
        db.close()
