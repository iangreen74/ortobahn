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


class TestDeployTracking:
    def test_record_and_get_deploy(self, test_db):
        deploy_id = test_db.record_deploy(sha="abc1234", environment="production")
        deploy = test_db.get_current_deploy("production")
        assert deploy is not None
        assert deploy["sha"] == "abc1234"
        assert deploy["status"] == "deployed"
        assert deploy["id"] == deploy_id

    def test_previous_sha_tracking(self, test_db):
        test_db.record_deploy(sha="first123", environment="production")
        test_db.record_deploy(sha="second456", environment="production", previous_sha="first123")

        deploy = test_db.get_current_deploy("production")
        assert deploy["sha"] == "second456"
        assert deploy["previous_sha"] == "first123"

    def test_mark_validated(self, test_db):
        deploy_id = test_db.record_deploy(sha="val123", environment="production")
        test_db.mark_deploy_validated(deploy_id)

        deploy = test_db.fetchone("SELECT * FROM deployments WHERE id=?", (deploy_id,))
        assert deploy["status"] == "validated"
        assert deploy["validated_at"] is not None

    def test_mark_rolled_back(self, test_db):
        deploy_id = test_db.record_deploy(sha="roll123", environment="production")
        test_db.mark_deploy_rolled_back(deploy_id)

        deploy = test_db.fetchone("SELECT * FROM deployments WHERE id=?", (deploy_id,))
        assert deploy["status"] == "rolled_back"
        assert deploy["rolled_back_at"] is not None

    def test_get_recent_deploys(self, test_db):
        for i in range(3):
            test_db.record_deploy(sha=f"sha{i}", environment="production")

        deploys = test_db.get_recent_deploys("production", limit=2)
        assert len(deploys) == 2

    def test_environment_isolation(self, test_db):
        test_db.record_deploy(sha="staging1", environment="staging")
        test_db.record_deploy(sha="prod1", environment="production")

        staging = test_db.get_current_deploy("staging")
        prod = test_db.get_current_deploy("production")
        assert staging["sha"] == "staging1"
        assert prod["sha"] == "prod1"


class TestProbeDeployHealth:
    def test_no_finding_without_deploy(self, test_db):
        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True),
        )
        findings = watchdog.probe_deploy_health()
        assert len(findings) == 0

    def test_no_finding_for_old_deploy(self, test_db):
        # Deploy from 2 hours ago — outside the 30 minute window
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO deployments (id, sha, environment, status, previous_sha, deployed_at) "
            "VALUES (?, ?, 'production', 'deployed', ?, ?)",
            ("old-deploy", "newsha", "oldsha", old_time),
            commit=True,
        )

        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True),
        )
        findings = watchdog.probe_deploy_health()
        assert len(findings) == 0

    def test_no_finding_without_previous_sha(self, test_db):
        # Recent deploy but no previous SHA to rollback to
        test_db.record_deploy(sha="first-ever", environment="production")

        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True),
        )
        findings = watchdog.probe_deploy_health()
        assert len(findings) == 0

    def test_finding_on_critical_health_after_deploy(self, test_db):
        # Record deploy with a slightly older timestamp so health checks are "after" it
        deploy_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
        deploy_id = str(uuid.uuid4())
        test_db.execute(
            "INSERT INTO deployments (id, sha, environment, status, previous_sha, deployed_at) "
            "VALUES (?, ?, 'production', 'deployed', ?, ?)",
            (deploy_id, "bad-sha", "good-sha", deploy_time),
            commit=True,
        )

        # Simulate critical health checks after the deploy
        for i in range(3):
            test_db.save_health_check(
                probe="stale_run",
                status="critical",
                detail=f"Critical issue {i}",
            )

        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True, auto_rollback_health_failures=3),
        )
        findings = watchdog.probe_deploy_health()

        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].auto_fixable
        assert "bad-sha" in findings[0].detail
        assert "good-sh" in findings[0].detail  # Truncated to 7 chars

    def test_finding_on_stale_runs_after_deploy(self, test_db):
        test_db.record_deploy(
            sha="broken-sha",
            environment="production",
            previous_sha="working-sha",
        )

        # Simulate a stale pipeline run
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("stale-after-deploy", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True),
        )
        findings = watchdog.probe_deploy_health()

        assert len(findings) == 1
        assert findings[0].probe == "deploy_health"

    def test_rollback_remediation(self, test_db):
        deploy_id = test_db.record_deploy(
            sha="bad-deploy",
            environment="production",
            previous_sha="good-deploy",
        )

        # Simulate stale run to trigger deploy_health finding
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        test_db.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', 'default')",
            ("stale-for-rollback", "single", old_time),
            commit=True,
        )

        watchdog = Watchdog(
            db=test_db,
            settings=_make_settings(auto_rollback_enabled=True),
        )

        # Mock the gh CLI call
        with patch("ortobahn.watchdog.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stderr": ""})()
            report = watchdog.run()

        # Find the deploy_health remediation
        deploy_remediations = [r for r in report.remediations if r.finding.probe == "deploy_health"]
        assert len(deploy_remediations) == 1
        assert deploy_remediations[0].success
        assert "good-de" in deploy_remediations[0].action

        # Verify deploy was marked as rolled back
        deploy = test_db.fetchone("SELECT status FROM deployments WHERE id=?", (deploy_id,))
        assert deploy["status"] == "rolled_back"


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


class TestProbeFailureRateTrend:
    def _insert_post(self, db, status, client_id, hours_ago):
        pid = str(uuid.uuid4())
        created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        db.execute(
            "INSERT INTO posts (id, text, status, run_id, client_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "test", status, "run-trend", client_id, created),
            commit=True,
        )

    def test_detects_rising_failure_rate(self, test_db):
        """Failure rate jumped from 0% (prev 24h) to 50% (last 24h) = +50 pp."""
        cid = "trend-client"
        test_db.create_client({"name": "Trend Corp", "id": cid}, start_trial=True)

        # Previous 24h (25-48h ago): 4 published, 0 failed = 0% failure rate
        for _ in range(4):
            self._insert_post(test_db, "published", cid, hours_ago=30)

        # Last 24h (0-24h ago): 2 failed, 2 published = 50% failure rate
        for _ in range(2):
            self._insert_post(test_db, "failed", cid, hours_ago=6)
        for _ in range(2):
            self._insert_post(test_db, "published", cid, hours_ago=6)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_failure_rate_trend()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 1
        assert matching[0].probe == "failure_rate_trend"
        assert matching[0].severity == "warning"

    def test_no_finding_when_stable(self, test_db):
        """Same failure rate in both periods should not trigger."""
        cid = "stable-client"
        test_db.create_client({"name": "Stable Corp", "id": cid}, start_trial=True)

        # Both windows: 1 failed, 3 published = 25% each
        for hours_ago in [6, 30]:
            self._insert_post(test_db, "failed", cid, hours_ago=hours_ago)
            for _ in range(3):
                self._insert_post(test_db, "published", cid, hours_ago=hours_ago)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_failure_rate_trend()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 0


class TestProbeCredentialExpiry:
    def test_detects_old_credentials(self, test_db):
        cid = "expiry-client"
        test_db.create_client({"name": "Expiry Corp", "id": cid}, start_trial=True)

        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        test_db.execute(
            "INSERT INTO platform_credentials (id, client_id, platform, credentials_encrypted, last_rotated_at) "
            "VALUES (?, ?, 'bluesky', 'encrypted-data', ?)",
            (str(uuid.uuid4()), cid, old_date),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_credential_expiry()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 1
        assert matching[0].probe == "credential_expiry"
        assert "90 days" in matching[0].detail

    def test_detects_null_rotation_date(self, test_db):
        cid = "null-rot-client"
        test_db.create_client({"name": "NullRot Corp", "id": cid}, start_trial=True)

        test_db.execute(
            "INSERT INTO platform_credentials (id, client_id, platform, credentials_encrypted) "
            "VALUES (?, ?, 'bluesky', 'encrypted-data')",
            (str(uuid.uuid4()), cid),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_credential_expiry()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 1
        assert "no rotation date" in matching[0].detail

    def test_skips_paused_clients(self, test_db):
        cid = "paused-cred-client"
        test_db.create_client({"name": "Paused Corp", "id": cid}, start_trial=True)
        test_db.update_client(cid, {"status": "paused"})

        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        test_db.execute(
            "INSERT INTO platform_credentials (id, client_id, platform, credentials_encrypted, last_rotated_at) "
            "VALUES (?, ?, 'bluesky', 'encrypted-data', ?)",
            (str(uuid.uuid4()), cid, old_date),
            commit=True,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_credential_expiry()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 0


class TestProbeEngagementDecline:
    def _insert_published_post(self, db, client_id, days_ago, likes=0, reposts=0, replies=0):
        pid = str(uuid.uuid4())
        published_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        db.execute(
            "INSERT INTO posts (id, text, status, run_id, client_id, published_at) VALUES (?, ?, 'published', ?, ?, ?)",
            (pid, "test post", "run-eng", client_id, published_at),
            commit=True,
        )
        db.save_metrics(pid, like_count=likes, repost_count=reposts, reply_count=replies)

    def test_insufficient_data_no_finding(self, test_db):
        """Probe requires >=3 posts in EACH period; <3 should produce no findings."""
        cid = "low-data-client"
        test_db.create_client({"name": "LowData Corp", "id": cid}, start_trial=True)

        for d in [1, 2]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=10)
        for d in [8, 9]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=20)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_engagement_decline()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 0

    def test_detects_decline(self, test_db):
        """Engagement drop of >30% should be detected."""
        cid = "decline-client"
        test_db.create_client({"name": "Decline Corp", "id": cid}, start_trial=True)

        for d in [8, 9, 10]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=20, reposts=5, replies=5)

        for d in [1, 2, 3]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=5, reposts=3, replies=2)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_engagement_decline()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 1
        assert matching[0].probe == "engagement_decline"
        assert matching[0].severity == "warning"

    def test_no_decline_no_finding(self, test_db):
        """Stable engagement should produce no findings."""
        cid = "stable-eng-client"
        test_db.create_client({"name": "StableEng Corp", "id": cid}, start_trial=True)

        for d in [8, 9, 10]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=10, reposts=5)
        for d in [1, 2, 3]:
            self._insert_published_post(test_db, cid, days_ago=d, likes=10, reposts=5)

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        findings = watchdog.probe_engagement_decline()

        matching = [f for f in findings if f.client_id == cid]
        assert len(matching) == 0


class TestAutoRemediation:
    def test_fix_credential_issue_sets_status(self, test_db):
        """_fix_credential_issue should set client status to 'credential_issue'."""
        cid = "cred-fix-client"
        test_db.create_client({"name": "CredFix Corp", "id": cid}, start_trial=True)

        finding = Finding(
            probe="credential_health",
            severity="warning",
            detail="Bluesky login failed",
            client_id=cid,
            auto_fixable=True,
            ref_id=cid,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        result = watchdog._fix_credential_issue(finding)

        assert result is True
        client = test_db.get_client(cid)
        assert client["status"] == "credential_issue"

    def test_verify_credential_fix(self, test_db):
        """_verify_credential_fix should confirm the status change."""
        cid = "cred-verify-client"
        test_db.create_client({"name": "CredVerify Corp", "id": cid}, start_trial=True)
        test_db.update_client(cid, {"status": "credential_issue"})

        finding = Finding(
            probe="credential_health",
            severity="warning",
            detail="Bluesky login failed",
            client_id=cid,
            auto_fixable=True,
            ref_id=cid,
        )

        watchdog = Watchdog(db=test_db, settings=_make_settings())
        assert watchdog._verify_credential_fix(finding) is True

    def test_credential_issue_blocks_pipeline(self, test_db):
        """Client with credential_issue status should be skipped by orchestrator."""
        cid = "blocked-client"
        test_db.create_client({"name": "Blocked Corp", "id": cid}, start_trial=True)
        test_db.update_client(cid, {"status": "credential_issue"})

        client = test_db.get_client(cid)
        assert client["status"] == "credential_issue"
        assert client["status"] in ("paused", "credential_issue")
