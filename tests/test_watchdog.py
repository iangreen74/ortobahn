"""Tests for Watchdog self-monitoring system."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ortobahn.config import Settings
from ortobahn.watchdog import Finding, RemediationResult, Watchdog, WatchdogReport


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        anthropic_api_key="sk-ant-test-key",
        secret_key="test-secret-key-for-fernet-derive",
        slack_webhook_url="",
        watchdog_enabled=True,
        watchdog_stale_run_minutes=60,
        watchdog_post_verify_hours=6,
        watchdog_credential_check=False,
        watchdog_max_verify_posts=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestWatchdogReport:
    def test_has_issues_false_when_all_ok(self):
        report = WatchdogReport(findings=[Finding(probe="test", severity="ok", detail="fine")])
        assert not report.has_issues

    def test_has_issues_true_on_warning(self):
        report = WatchdogReport(findings=[Finding(probe="test", severity="warning", detail="bad")])
        assert report.has_issues

    def test_summary_all_clear(self):
        report = WatchdogReport(findings=[Finding(probe="test", severity="ok", detail="fine")])
        assert report.summary == "all clear"

    def test_summary_with_issues(self):
        report = WatchdogReport(
            findings=[
                Finding(probe="a", severity="critical", detail="x"),
                Finding(probe="b", severity="warning", detail="y"),
            ],
            remediations=[
                RemediationResult(
                    finding=Finding(probe="a", severity="critical", detail="x"),
                    action="fixed",
                    success=True,
                )
            ],
        )
        assert "1 critical" in report.summary
        assert "1 warning" in report.summary
        assert "1 auto-fixed" in report.summary


class TestProbeStaleRuns:
    def test_detects_stuck_run(self, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("stale-run-1", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_stale_runs()

        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].auto_fixable
        assert findings[0].ref_id == "stale-run-1"

    def test_ignores_recent_run(self, test_db):
        recent_time = datetime.now(timezone.utc).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("recent-run-1", "single", recent_time),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_stale_runs()

        assert len(findings) == 0

    def test_ignores_completed_run(self, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'completed', 'default')",
            ("completed-run-1", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_stale_runs()

        assert len(findings) == 0


class TestProbeClientHealth:
    def test_finds_missing_subscription(self, test_db):
        test_db.create_client({"name": "NoSub Corp", "id": "nosub-1"}, start_trial=False)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_client_health()

        matching = [f for f in findings if f.client_id == "nosub-1"]
        assert len(matching) == 1
        assert matching[0].auto_fixable
        assert "no subscription" in matching[0].detail.lower()

    def test_skips_internal_clients(self, test_db):
        # The 'default' client is internal — should not appear
        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_client_health()

        default_findings = [f for f in findings if f.client_id == "default"]
        assert len(default_findings) == 0

    def test_no_finding_for_trialing_client(self, test_db):
        test_db.create_client({"name": "Trial Corp", "id": "trial-1"}, start_trial=True)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_client_health()

        matching = [f for f in findings if f.client_id == "trial-1"]
        assert len(matching) == 0


class TestProbeFailureRate:
    def _insert_post(self, db, status, client_id="default"):
        pid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO posts (id, text, status, run_id, client_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "test", status, "run-1", client_id, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )
        return pid

    def test_high_failure_rate(self, test_db):
        for _ in range(4):
            self._insert_post(test_db, "failed")
        self._insert_post(test_db, "published")

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_failure_rate()

        assert len(findings) >= 1
        assert findings[0].severity == "warning"

    def test_low_failure_rate_no_finding(self, test_db):
        self._insert_post(test_db, "failed")
        for _ in range(4):
            self._insert_post(test_db, "published")

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_failure_rate()

        # failure rate is 1/5 = 20%, below 50% threshold
        default_findings = [f for f in findings if f.client_id == "default"]
        assert len(default_findings) == 0

    def test_too_few_posts_no_finding(self, test_db):
        self._insert_post(test_db, "failed")
        self._insert_post(test_db, "published")

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_failure_rate()

        # Only 2 posts, below threshold of 3
        default_findings = [f for f in findings if f.client_id == "default"]
        assert len(default_findings) == 0


class TestFixStaleRun:
    def test_marks_run_failed(self, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("stale-fix-1", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        watchdog._fix_stale_run("stale-fix-1")

        row = test_db.fetchone("SELECT status, errors FROM pipeline_runs WHERE id='stale-fix-1'")
        assert row["status"] == "failed"
        errors = json.loads(row["errors"])
        assert any("Watchdog" in e for e in errors)


class TestFixClientSubscription:
    def test_sets_trialing(self, test_db):
        test_db.create_client({"name": "Fix Corp", "id": "fix-1"}, start_trial=False)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        watchdog._fix_client_subscription("fix-1")

        row = test_db.fetchone("SELECT subscription_status, trial_ends_at FROM clients WHERE id='fix-1'")
        assert row["subscription_status"] == "trialing"
        assert row["trial_ends_at"] is not None


class TestFixPhantomPost:
    def test_marks_post_failed(self, test_db):
        pid = str(uuid.uuid4())
        test_db.execute(
            "INSERT INTO posts (id, text, status, run_id, client_id) VALUES (?, ?, 'published', 'run-1', 'default')",
            (pid, "phantom post"),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        watchdog._fix_phantom_post(pid)

        row = test_db.fetchone(f"SELECT status, error_message FROM posts WHERE id='{pid}'")
        assert row["status"] == "failed"
        assert "Watchdog" in row["error_message"]


class TestFullCycle:
    def test_end_to_end(self, test_db):
        """Full watchdog cycle: seed issues, run, verify fixes."""
        # Seed a stale run
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("e2e-stale-1", "single", old_time),
            commit=True,
        )

        # Seed a client with no subscription
        test_db.create_client({"name": "E2E Corp", "id": "e2e-client"}, start_trial=False)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        report = watchdog.run()

        assert report.has_issues
        assert len(report.remediations) >= 2

        # Verify stale run was fixed
        run_row = test_db.fetchone("SELECT status FROM pipeline_runs WHERE id='e2e-stale-1'")
        assert run_row["status"] == "failed"

        # Verify client subscription was fixed
        client_row = test_db.fetchone("SELECT subscription_status FROM clients WHERE id='e2e-client'")
        assert client_row["subscription_status"] == "trialing"

        # Verify all remediations were verified
        for r in report.remediations:
            assert r.verified is True

    def test_records_health_checks(self, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("hc-stale-1", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        watchdog.run()

        checks = test_db.fetchall("SELECT * FROM health_checks")
        assert len(checks) > 0

    def test_records_remediations(self, test_db):
        test_db.create_client({"name": "Remediation Corp", "id": "rem-1"}, start_trial=False)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        watchdog.run()

        rems = test_db.fetchall("SELECT * FROM watchdog_remediations")
        assert len(rems) >= 1
        assert rems[0]["success"] == 1

    def test_slack_alert_sent_on_issues(self, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("slack-stale-1", "single", old_time),
            commit=True,
        )

        settings = _make_settings(slack_webhook_url="https://hooks.slack.com/test")
        watchdog = Watchdog(db=test_db, settings=settings)

        with patch("ortobahn.integrations.slack.send_slack_message", return_value=True) as mock_send:
            watchdog.run()
            mock_send.assert_called_once()


class TestDBUpdatePostFailed:
    def test_stores_error_message(self, test_db):
        pid = str(uuid.uuid4())
        test_db.execute(
            "INSERT INTO posts (id, text, status, run_id, client_id) VALUES (?, ?, 'published', 'run-1', 'default')",
            (pid, "will fail"),
            commit=True,
        )

        test_db.update_post_failed(pid, "Something went wrong")

        row = test_db.fetchone(f"SELECT status, error_message FROM posts WHERE id='{pid}'")
        assert row["status"] == "failed"
        assert row["error_message"] == "Something went wrong"
