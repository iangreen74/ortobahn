"""Lightweight schema migration system — supports PostgreSQL and SQLite."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.migrations")


def _safe_add_column(db: Database, table: str, column_def: str) -> None:
    """Add a column if it doesn't already exist. Silently ignores duplicates."""
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}", commit=True)
    except Exception as e:
        err = str(e).lower()
        if "duplicate column" in err or "already exists" in err:
            pass
        else:
            raise


def _get_schema_version(db: Database) -> int:
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL DEFAULT 0)", commit=True)
    row = db.fetchone("SELECT version FROM schema_version")
    if row is None:
        db.execute("INSERT INTO schema_version (version) VALUES (0)", commit=True)
        return 0
    return row["version"]


def _set_schema_version(db: Database, version: int) -> None:
    db.execute("UPDATE schema_version SET version = ?", (version,), commit=True)


def _migration_001_add_clients_and_platform(db: Database) -> None:
    """Add clients table and extend posts/strategies/pipeline_runs with client_id/platform."""
    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """INSERT INTO clients (id, name, description, industry, target_audience, brand_voice)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (id) DO NOTHING""",
        (
            "default",
            "Ortobahn",
            "Autonomous AI marketing engine",
            "AI/Technology",
            "tech-savvy professionals, founders, AI enthusiasts",
            "authoritative but approachable",
        ),
        commit=True,
    )

    for col_def in [
        ("strategies", "client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)"),
        ("posts", "client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)"),
        ("posts", "platform TEXT NOT NULL DEFAULT 'generic'"),
        ("posts", "content_type TEXT NOT NULL DEFAULT 'social_post'"),
        ("pipeline_runs", "client_id TEXT NOT NULL DEFAULT 'default' REFERENCES clients(id)"),
    ]:
        _safe_add_column(db, col_def[0], col_def[1])


def _migration_002_add_platform_uri(db: Database) -> None:
    """Add platform-agnostic URI/ID columns to posts."""
    _safe_add_column(db, "posts", "platform_uri TEXT")
    _safe_add_column(db, "posts", "platform_id TEXT")
    # Backfill from bluesky columns
    db.execute(
        "UPDATE posts SET platform_uri = bluesky_uri, platform_id = bluesky_cid "
        "WHERE bluesky_uri IS NOT NULL AND platform_uri IS NULL",
        commit=True,
    )


def _migration_003_add_client_onboarding(db: Database) -> None:
    """Add email and status columns to clients for onboarding support."""
    _safe_add_column(db, "clients", "email TEXT NOT NULL DEFAULT ''")
    _safe_add_column(db, "clients", "status TEXT NOT NULL DEFAULT 'active'")


def _migration_004_add_client_enrichment(db: Database) -> None:
    """Add product/positioning/pillars/story columns to clients."""
    for col in [
        "products TEXT NOT NULL DEFAULT ''",
        "competitive_positioning TEXT NOT NULL DEFAULT ''",
        "key_messages TEXT NOT NULL DEFAULT ''",
        "content_pillars TEXT NOT NULL DEFAULT ''",
        "company_story TEXT NOT NULL DEFAULT ''",
    ]:
        _safe_add_column(db, "clients", col)


def _migration_005_add_monthly_budget(db: Database) -> None:
    """Add monthly_budget column to clients for CFO enforcement."""
    _safe_add_column(db, "clients", "monthly_budget REAL DEFAULT 0")


def _migration_006_add_ab_testing(db: Database) -> None:
    """Add A/B testing columns to posts."""
    _safe_add_column(db, "posts", "ab_group TEXT DEFAULT NULL")
    _safe_add_column(db, "posts", "ab_pair_id TEXT DEFAULT NULL")


def _migration_007_add_auth_and_credentials(db: Database) -> None:
    """Add API keys, sessions, and encrypted platform credentials."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES clients(id),
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'default',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 1
        )
    """,
        commit=True,
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_credentials (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES clients(id),
            platform TEXT NOT NULL,
            credentials_encrypted TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(client_id, platform)
        )
    """,
        commit=True,
    )

    for col in [
        "internal INTEGER NOT NULL DEFAULT 0",
        "stripe_customer_id TEXT",
        "stripe_subscription_id TEXT",
        "subscription_status TEXT NOT NULL DEFAULT 'none'",
        "subscription_plan TEXT NOT NULL DEFAULT ''",
    ]:
        _safe_add_column(db, "clients", col)

    db.execute("UPDATE clients SET internal=1 WHERE id IN ('default', 'vaultscaler', 'ortobahn')", commit=True)


def _migration_008_add_stripe_events(db: Database) -> None:
    """Add stripe events log for idempotent webhook processing."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS stripe_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        commit=True,
    )


def _migration_009_add_engineering_tasks(db: Database) -> None:
    """Add engineering task backlog and CTO agent tracking tables."""
    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS code_changes (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES engineering_tasks(id),
            run_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            change_type TEXT NOT NULL,
            diff_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        commit=True,
    )
    db.execute(
        """
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
        )
    """,
        commit=True,
    )


def _migration_010_add_intelligence_system(db: Database) -> None:
    """Add agent memory, confidence calibration, A/B experiments, goals, and reflection tables."""
    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_memories_agent_client
        ON agent_memories(agent_name, client_id, active)""",
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_memories_type
        ON agent_memories(memory_type, category)""",
        commit=True,
    )

    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_calibration_client
        ON confidence_calibration(client_id, measured_at)""",
        commit=True,
    )

    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_experiments_client
        ON ab_experiments(client_id, status)""",
        commit=True,
    )

    db.execute(
        """
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
        )
    """,
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_goals_agent
        ON agent_goals(agent_name, client_id)""",
        commit=True,
    )

    db.execute(
        """
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
        )
    """,
        commit=True,
    )


def _migration_011_add_ci_fix_tracking(db: Database) -> None:
    """Add CI fix tracking table for the CI/CD self-healing agent."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ci_fix_attempts (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            gh_run_id INTEGER,
            gh_run_url TEXT,
            job_name TEXT NOT NULL DEFAULT '',
            failure_category TEXT NOT NULL DEFAULT 'unknown',
            error_count INTEGER DEFAULT 0,
            error_codes TEXT,
            fix_strategy TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            files_changed TEXT,
            branch_name TEXT,
            commit_sha TEXT,
            pr_url TEXT,
            llm_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            validation_passed INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_ci_fix_category
        ON ci_fix_attempts(failure_category, status)""",
        commit=True,
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_ci_fix_gh_run
        ON ci_fix_attempts(gh_run_id)""",
        commit=True,
    )


def _migration_012_add_auto_publish(db: Database) -> None:
    """Add per-client auto_publish toggle and target_platforms."""
    _safe_add_column(db, "clients", "auto_publish INTEGER NOT NULL DEFAULT 0")
    _safe_add_column(db, "clients", "target_platforms TEXT NOT NULL DEFAULT 'bluesky'")
    # Enable auto_publish for existing internal clients
    db.execute("UPDATE clients SET auto_publish=1, target_platforms='bluesky' WHERE internal=1", commit=True)


def _migration_013_add_cognito_sub(db: Database) -> None:
    """Add cognito_sub column to clients for Cognito user mapping."""
    _safe_add_column(db, "clients", "cognito_sub TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_clients_cognito_sub ON clients(cognito_sub)", commit=True)


def _migration_014_add_trial_ends_at(db: Database) -> None:
    """Add trial_ends_at column for free-trial tracking."""
    _safe_add_column(db, "clients", "trial_ends_at TIMESTAMP")


def _migration_015_add_client_trends_and_schedule(db: Database) -> None:
    """Add per-client trend config and posting schedule."""
    for col in [
        "news_category TEXT NOT NULL DEFAULT 'technology'",
        "news_keywords TEXT NOT NULL DEFAULT ''",
        "rss_feeds TEXT NOT NULL DEFAULT ''",
        "posting_interval_hours INTEGER NOT NULL DEFAULT 8",
        "timezone TEXT NOT NULL DEFAULT 'UTC'",
    ]:
        _safe_add_column(db, "clients", col)


def _migration_016_add_cache_token_tracking(db: Database) -> None:
    """Add cache token columns for prompt caching cost tracking."""
    _safe_add_column(db, "agent_logs", "cache_creation_input_tokens INTEGER DEFAULT 0")
    _safe_add_column(db, "agent_logs", "cache_read_input_tokens INTEGER DEFAULT 0")
    _safe_add_column(db, "pipeline_runs", "total_cache_creation_tokens INTEGER DEFAULT 0")
    _safe_add_column(db, "pipeline_runs", "total_cache_read_tokens INTEGER DEFAULT 0")


def _migration_017_backfill_client_trials(db: Database) -> None:
    """Grant a 14-day trial to non-internal clients stuck at subscription_status='none'."""
    from datetime import datetime, timedelta, timezone

    trial_end = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    db.execute(
        """UPDATE clients SET subscription_status='trialing', trial_ends_at=?
           WHERE internal=0 AND subscription_status='none'""",
        (trial_end,),
        commit=True,
    )


def _migration_018_add_watchdog_tables(db: Database) -> None:
    """Add error_message to posts, health_checks and watchdog_remediations tables."""
    _safe_add_column(db, "posts", "error_message TEXT")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS health_checks (
            id TEXT PRIMARY KEY,
            probe TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            client_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        commit=True,
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_checks_probe ON health_checks(probe, created_at)",
        commit=True,
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_remediations (
            id TEXT PRIMARY KEY,
            finding_type TEXT NOT NULL,
            client_id TEXT,
            action TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            verified INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        commit=True,
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchdog_remediations_type ON watchdog_remediations(finding_type, created_at)",
        commit=True,
    )


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
    11: _migration_011_add_ci_fix_tracking,
    12: _migration_012_add_auto_publish,
    13: _migration_013_add_cognito_sub,
    14: _migration_014_add_trial_ends_at,
    15: _migration_015_add_client_trends_and_schedule,
    16: _migration_016_add_cache_token_tracking,
    17: _migration_017_backfill_client_trials,
    18: _migration_018_add_watchdog_tables,
}


def run_migrations(db: Database) -> int:
    """Run any pending migrations. Returns the final schema version."""
    current = _get_schema_version(db)
    latest = max(MIGRATIONS.keys()) if MIGRATIONS else 0

    if current >= latest:
        return current

    for version in range(current + 1, latest + 1):
        if version in MIGRATIONS:
            logger.info(f"Running migration {version}...")
            MIGRATIONS[version](db)
            _set_schema_version(db, version)
            logger.info(f"Migration {version} complete.")

    return latest
