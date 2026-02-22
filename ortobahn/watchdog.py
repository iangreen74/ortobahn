"""Watchdog — closed-loop self-monitoring and self-healing system.

Runs outside the pipeline (in the scheduler loop) so it can detect failures
in the pipeline itself. Follows a Sense → Decide → Act → Verify loop.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.config import Settings
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.watchdog")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    probe: str
    severity: str  # "ok", "warning", "critical"
    detail: str
    client_id: str | None = None
    auto_fixable: bool = False
    ref_id: str | None = None  # e.g. pipeline run ID, post ID


@dataclass
class RemediationResult:
    finding: Finding
    action: str
    success: bool
    verified: bool | None = None  # None = not yet checked


@dataclass
class WatchdogReport:
    findings: list[Finding] = field(default_factory=list)
    remediations: list[RemediationResult] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any(f.severity != "ok" for f in self.findings)

    @property
    def summary(self) -> str:
        critical = sum(1 for f in self.findings if f.severity == "critical")
        warnings = sum(1 for f in self.findings if f.severity == "warning")
        fixed = sum(1 for r in self.remediations if r.success)
        parts = []
        if critical:
            parts.append(f"{critical} critical")
        if warnings:
            parts.append(f"{warnings} warning")
        if fixed:
            parts.append(f"{fixed} auto-fixed")
        return ", ".join(parts) if parts else "all clear"


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


class Watchdog:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    def run(self) -> WatchdogReport:
        """Full sense-decide-act-verify cycle."""
        findings = self._sense()
        fixable = [f for f in findings if f.auto_fixable]
        remediations = self._act(fixable)
        self._verify(remediations)
        self._alert(findings, remediations)
        self._record(findings, remediations)
        return WatchdogReport(findings=findings, remediations=remediations)

    # --- Sense phase ---

    def _sense(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self.probe_stale_runs())
        findings.extend(self.probe_post_delivery())
        findings.extend(self.probe_client_health())
        findings.extend(self.probe_failure_rate())
        findings.extend(self.probe_failure_rate_trend())
        findings.extend(self.probe_credential_expiry())
        findings.extend(self.probe_engagement_decline())
        if self.settings.watchdog_credential_check:
            findings.extend(self.probe_credential_health())
        if self.settings.auto_rollback_enabled:
            findings.extend(self.probe_deploy_health())
        return findings

    def probe_stale_runs(self) -> list[Finding]:
        """Detect pipeline runs stuck in 'running' state."""
        stale = self.db.get_stale_runs(self.settings.watchdog_stale_run_minutes)
        findings = []
        for run in stale:
            findings.append(
                Finding(
                    probe="stale_run",
                    severity="critical",
                    detail=f"Pipeline run {run['id']} stuck in 'running' since {run['started_at']}",
                    client_id=run.get("client_id"),
                    auto_fixable=True,
                    ref_id=run["id"],
                )
            )
        return findings

    def probe_post_delivery(self) -> list[Finding]:
        """Verify recent 'published' posts actually exist on the platform."""
        from ortobahn.credentials import build_platform_clients

        posts = self.db.get_recent_posts_by_status(
            hours=self.settings.watchdog_post_verify_hours,
            status="published",
        )
        findings = []
        checked = 0

        # Group by client to reuse platform clients
        client_posts: dict[str, list[dict]] = {}
        for p in posts:
            cid = p.get("client_id", "default")
            client_posts.setdefault(cid, []).append(p)

        for client_id, client_post_list in client_posts.items():
            try:
                clients = build_platform_clients(self.db, client_id, self.settings.secret_key, self.settings)
            except Exception as e:
                logger.warning(f"Watchdog: cannot build clients for {client_id}: {e}")
                continue

            bluesky = clients.get("bluesky")
            if not bluesky:
                continue

            for p in client_post_list:
                if checked >= self.settings.watchdog_max_verify_posts:
                    break
                uri = p.get("platform_uri") or p.get("bluesky_uri")
                if not uri:
                    continue
                if p.get("platform", "bluesky") != "bluesky":
                    continue

                try:
                    result = bluesky.verify_post_exists(uri)
                    if result is False:
                        findings.append(
                            Finding(
                                probe="post_delivery",
                                severity="critical",
                                detail=f"Post {p['id']} claims published but not found at {uri}",
                                client_id=client_id,
                                auto_fixable=True,
                                ref_id=p["id"],
                            )
                        )
                    checked += 1
                except Exception as e:
                    logger.warning(f"Watchdog: delivery check failed for {uri}: {e}")

        return findings

    def probe_credential_health(self) -> list[Finding]:
        """Check that saved credentials can authenticate."""
        from ortobahn.credentials import get_all_platform_credentials

        findings = []
        clients = self.db.fetchall("SELECT id, name FROM clients WHERE active=1 AND internal=0")
        for client in clients:
            try:
                all_creds = get_all_platform_credentials(self.db, client["id"], self.settings.secret_key)
            except Exception:
                continue

            bs_creds = all_creds.get("bluesky")
            if bs_creds and bs_creds.get("handle") and bs_creds.get("app_password"):
                from ortobahn.integrations.bluesky import BlueskyClient

                try:
                    test_client = BlueskyClient(bs_creds["handle"], bs_creds["app_password"])
                    test_client.login()
                except Exception as e:
                    findings.append(
                        Finding(
                            probe="credential_health",
                            severity="warning",
                            detail=f"Bluesky login failed for {client['name']}: {e}",
                            client_id=client["id"],
                            auto_fixable=True,
                            ref_id=client["id"],
                        )
                    )

        return findings

    def probe_client_health(self) -> list[Finding]:
        """Detect non-internal clients with broken subscription state."""
        findings = []
        rows = self.db.fetchall(
            "SELECT id, name, subscription_status, trial_ends_at FROM clients WHERE active=1 AND internal=0"
        )
        for c in rows:
            if c.get("subscription_status") in (None, "none", ""):
                findings.append(
                    Finding(
                        probe="client_health",
                        severity="warning",
                        detail=f"Client {c['name']} has no subscription — pipeline blocked",
                        client_id=c["id"],
                        auto_fixable=True,
                        ref_id=c["id"],
                    )
                )
            elif c.get("subscription_status") == "expired":
                findings.append(
                    Finding(
                        probe="client_health",
                        severity="warning",
                        detail=f"Client {c['name']} trial expired",
                        client_id=c["id"],
                        auto_fixable=False,
                    )
                )

        return findings

    def probe_failure_rate(self) -> list[Finding]:
        """Check if recent post failure rate is too high."""
        findings = []
        clients = self.db.fetchall("SELECT id, name FROM clients WHERE active=1")
        for client in clients:
            failed, total = self.db.get_post_failure_rate(hours=24, client_id=client["id"])
            if total >= 3 and failed / total > 0.5:
                rate = failed / total * 100
                findings.append(
                    Finding(
                        probe="failure_rate",
                        severity="warning",
                        detail=f"Client {client['name']} failure rate {rate:.0f}% ({failed}/{total} posts)",
                        client_id=client["id"],
                        auto_fixable=False,
                    )
                )

        return findings

    # --- Predictive probes ---

    def _get_post_failure_rate_window(
        self, client_id: str, start_hours_ago: int, end_hours_ago: int
    ) -> tuple[int, int]:
        """Return (failed_count, total_count) for posts in a time window."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=start_hours_ago)).isoformat()
        end = (now - timedelta(hours=end_hours_ago)).isoformat()
        total_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM posts"
            " WHERE client_id=? AND created_at >= ? AND created_at < ?"
            " AND status IN ('published', 'failed')",
            (client_id, start, end),
        )
        failed_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM posts"
            " WHERE client_id=? AND created_at >= ? AND created_at < ?"
            " AND status='failed'",
            (client_id, start, end),
        )
        total = total_row["cnt"] if total_row else 0
        failed = failed_row["cnt"] if failed_row else 0
        return failed, total

    def probe_failure_rate_trend(self) -> list[Finding]:
        """Compare post failure rate in last 24h vs previous 24h per client."""
        findings = []
        clients = self.db.fetchall("SELECT id, name FROM clients WHERE active=1")
        for client in clients:
            cid = client["id"]
            failed_recent, total_recent = self._get_post_failure_rate_window(cid, 24, 0)
            failed_prev, total_prev = self._get_post_failure_rate_window(cid, 48, 24)

            if total_recent == 0 or total_prev == 0:
                continue

            rate_recent = failed_recent / total_recent * 100
            rate_prev = failed_prev / total_prev * 100

            if rate_recent - rate_prev > 25:
                findings.append(
                    Finding(
                        probe="failure_rate_trend",
                        severity="warning",
                        detail=(
                            f"Client {client['name']} failure rate rose from "
                            f"{rate_prev:.0f}% to {rate_recent:.0f}% "
                            f"(prev 24h: {failed_prev}/{total_prev}, "
                            f"last 24h: {failed_recent}/{total_recent})"
                        ),
                        client_id=cid,
                    )
                )
        return findings

    def probe_credential_expiry(self) -> list[Finding]:
        """Warn if platform credentials haven't been rotated in >60 days."""
        findings = []
        rows = self.db.fetchall(
            "SELECT pc.client_id, pc.platform, pc.last_rotated_at, c.name"
            " FROM platform_credentials pc"
            " JOIN clients c ON c.id = pc.client_id"
            " WHERE c.active=1 AND c.internal=0 AND c.status NOT IN ('paused')"
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            last_rotated = row.get("last_rotated_at")
            if not last_rotated:
                findings.append(
                    Finding(
                        probe="credential_expiry",
                        severity="warning",
                        detail=(
                            f"Client {row['name']} has {row['platform']} credentials with no rotation date recorded"
                        ),
                        client_id=row["client_id"],
                    )
                )
                continue

            try:
                rotated_dt = datetime.fromisoformat(str(last_rotated).replace(" ", "T"))
                if rotated_dt.tzinfo is None:
                    rotated_dt = rotated_dt.replace(tzinfo=timezone.utc)
                days_since = (now - rotated_dt).days
                if days_since > 60:
                    findings.append(
                        Finding(
                            probe="credential_expiry",
                            severity="warning",
                            detail=(
                                f"Client {row['name']} {row['platform']} credentials last rotated {days_since} days ago"
                            ),
                            client_id=row["client_id"],
                        )
                    )
            except (ValueError, TypeError):
                findings.append(
                    Finding(
                        probe="credential_expiry",
                        severity="warning",
                        detail=(
                            f"Client {row['name']} has {row['platform']} credentials with unparseable rotation date"
                        ),
                        client_id=row["client_id"],
                    )
                )
        return findings

    def probe_engagement_decline(self) -> list[Finding]:
        """Warn if average engagement this week vs last week declined >30%."""
        findings = []
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()
        two_weeks_ago = (now - timedelta(days=14)).isoformat()

        clients = self.db.fetchall("SELECT id, name FROM clients WHERE active=1")
        for client in clients:
            cid = client["id"]

            this_week = self.db.fetchall(
                "SELECT COALESCE(m.like_count, 0) AS like_count,"
                " COALESCE(m.repost_count, 0) AS repost_count,"
                " COALESCE(m.reply_count, 0) AS reply_count"
                " FROM posts p LEFT JOIN metrics m ON p.id = m.post_id"
                " WHERE p.client_id=? AND p.status='published'"
                " AND p.published_at >= ?",
                (cid, week_ago),
            )
            last_week = self.db.fetchall(
                "SELECT COALESCE(m.like_count, 0) AS like_count,"
                " COALESCE(m.repost_count, 0) AS repost_count,"
                " COALESCE(m.reply_count, 0) AS reply_count"
                " FROM posts p LEFT JOIN metrics m ON p.id = m.post_id"
                " WHERE p.client_id=? AND p.status='published'"
                " AND p.published_at >= ? AND p.published_at < ?",
                (cid, two_weeks_ago, week_ago),
            )

            if len(this_week) < 3 or len(last_week) < 3:
                continue

            def _avg_engagement(rows: list[dict]) -> float:
                total = sum(
                    (r.get("like_count") or 0) + (r.get("repost_count") or 0) + (r.get("reply_count") or 0)
                    for r in rows
                )
                return total / len(rows)

            avg_this = _avg_engagement(this_week)
            avg_last = _avg_engagement(last_week)

            if avg_last > 0 and (avg_last - avg_this) / avg_last > 0.30:
                pct_drop = (avg_last - avg_this) / avg_last * 100
                findings.append(
                    Finding(
                        probe="engagement_decline",
                        severity="warning",
                        detail=(
                            f"Client {client['name']} avg engagement dropped "
                            f"{pct_drop:.0f}% (last week: {avg_last:.1f}, "
                            f"this week: {avg_this:.1f})"
                        ),
                        client_id=cid,
                    )
                )
        return findings

    def probe_deploy_health(self) -> list[Finding]:
        """Detect health degradation after a recent deployment."""
        findings: list[Finding] = []
        deploy = self.db.get_current_deploy("production")
        if not deploy:
            return findings

        # Only check deploys within the rollback window
        deployed_at = datetime.fromisoformat(deploy["deployed_at"].replace(" ", "T"))
        if deployed_at.tzinfo is None:
            deployed_at = deployed_at.replace(tzinfo=timezone.utc)
        window = timedelta(minutes=self.settings.auto_rollback_window_minutes)
        if datetime.now(timezone.utc) - deployed_at > window:
            return findings

        if not deploy.get("previous_sha"):
            return findings

        # Check for health signals within the rollback window
        stale = self.db.get_stale_runs(self.settings.watchdog_stale_run_minutes)
        cutoff = (datetime.now(timezone.utc) - window).strftime("%Y-%m-%d %H:%M:%S")
        recent_checks = self.db.fetchall(
            "SELECT * FROM health_checks WHERE status='critical' AND created_at > ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, self.settings.auto_rollback_health_failures),
        )

        has_critical_health = len(recent_checks) >= self.settings.auto_rollback_health_failures
        has_stale_runs = len(stale) > 0

        if has_critical_health or has_stale_runs:
            findings.append(
                Finding(
                    probe="deploy_health",
                    severity="critical",
                    detail=(
                        f"Health degradation detected after deploy {deploy['sha'][:7]}. "
                        f"Critical checks: {len(recent_checks)}, stale runs: {len(stale)}. "
                        f"Previous known-good SHA: {deploy['previous_sha'][:7]}"
                    ),
                    auto_fixable=True,
                    ref_id=deploy["id"],
                )
            )

        return findings

    # --- Act phase ---

    def _act(self, fixable: list[Finding]) -> list[RemediationResult]:
        results = []
        for f in fixable:
            if not f.ref_id:
                continue
            ref_id: str = f.ref_id
            try:
                if f.probe == "stale_run":
                    self._fix_stale_run(ref_id)
                    results.append(
                        RemediationResult(
                            finding=f,
                            action=f"Marked stale run {ref_id} as failed",
                            success=True,
                        )
                    )
                elif f.probe == "post_delivery":
                    self._fix_phantom_post(ref_id)
                    results.append(
                        RemediationResult(
                            finding=f,
                            action=f"Marked phantom post {ref_id} as failed",
                            success=True,
                        )
                    )
                elif f.probe == "client_health":
                    self._fix_client_subscription(ref_id)
                    results.append(
                        RemediationResult(
                            finding=f,
                            action=f"Granted 14-day trial to client {f.client_id}",
                            success=True,
                        )
                    )
                elif f.probe == "credential_health":
                    success = self._fix_credential_issue(f)
                    results.append(
                        RemediationResult(
                            finding=f,
                            action=f"Set client {f.client_id} status to credential_issue",
                            success=success,
                        )
                    )
                elif f.probe == "deploy_health":
                    deploy = self.db.fetchone("SELECT * FROM deployments WHERE id=?", (ref_id,))
                    if deploy and deploy.get("previous_sha"):
                        rolled_back = self._fix_deploy_rollback(ref_id, deploy["previous_sha"])
                        results.append(
                            RemediationResult(
                                finding=f,
                                action=f"Auto-rollback triggered: {deploy['sha'][:7]} → {deploy['previous_sha'][:7]}",
                                success=rolled_back,
                            )
                        )
            except Exception as e:
                logger.error(f"Watchdog remediation failed for {f.probe}: {e}")
                results.append(
                    RemediationResult(
                        finding=f,
                        action=f"Attempted fix for {f.probe}",
                        success=False,
                    )
                )
        return results

    def _fix_stale_run(self, run_id: str) -> None:
        self.db.fail_pipeline_run(run_id, ["Watchdog: timed out — run exceeded maximum duration"])
        logger.info(f"Watchdog: marked stale run {run_id} as failed")

    def _fix_phantom_post(self, post_id: str) -> None:
        self.db.update_post_failed(post_id, "Watchdog: post verification failed — not found on platform")
        logger.info(f"Watchdog: marked phantom post {post_id} as failed")

    def _fix_client_subscription(self, client_id: str) -> None:
        trial_end = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        self.db.execute(
            "UPDATE clients SET subscription_status='trialing', trial_ends_at=? WHERE id=?",
            (trial_end, client_id),
            commit=True,
        )
        logger.info(f"Watchdog: granted trial to client {client_id}")

    def _fix_credential_issue(self, finding: Finding) -> bool:
        """Pause a client whose credentials are failing."""
        if not finding.ref_id:
            return False
        self.db.update_client(finding.ref_id, {"status": "credential_issue"})
        logger.info(f"Watchdog: set client {finding.ref_id} status to credential_issue")
        return True

    def _verify_credential_fix(self, finding: Finding) -> bool:
        """Check that the client was set to credential_issue status."""
        if not finding.ref_id:
            return False
        client = self.db.get_client(finding.ref_id)
        return client is not None and client.get("status") == "credential_issue"

    def _fix_deploy_rollback(self, deploy_id: str, previous_sha: str) -> bool:
        """Trigger rollback via GitHub Actions workflow dispatch."""
        self.db.mark_deploy_rolled_back(deploy_id)
        logger.warning(f"Watchdog: triggering auto-rollback to {previous_sha[:7]}")

        try:
            result = subprocess.run(
                [
                    "gh",
                    "workflow",
                    "run",
                    "rollback.yml",
                    "-f",
                    f"sha={previous_sha}",
                    "-f",
                    "service=all",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info(f"Watchdog: rollback workflow dispatched for {previous_sha[:7]}")
                return True
            else:
                logger.error(f"Watchdog: rollback dispatch failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"Watchdog: rollback dispatch error: {e}")
            return False

    # --- Verify phase ---

    def _verify(self, remediations: list[RemediationResult]) -> None:
        for r in remediations:
            if not r.success:
                continue
            try:
                if r.finding.probe == "stale_run":
                    row = self.db.fetchone(
                        "SELECT status FROM pipeline_runs WHERE id=?",
                        (r.finding.ref_id,),
                    )
                    r.verified = row is not None and row["status"] == "failed"
                elif r.finding.probe == "post_delivery":
                    row = self.db.fetchone(
                        "SELECT status FROM posts WHERE id=?",
                        (r.finding.ref_id,),
                    )
                    r.verified = row is not None and row["status"] == "failed"
                elif r.finding.probe == "client_health":
                    row = self.db.fetchone(
                        "SELECT subscription_status FROM clients WHERE id=?",
                        (r.finding.ref_id,),
                    )
                    r.verified = row is not None and row["subscription_status"] == "trialing"
                elif r.finding.probe == "credential_health":
                    r.verified = self._verify_credential_fix(r.finding)
                elif r.finding.probe == "deploy_health":
                    row = self.db.fetchone(
                        "SELECT status FROM deployments WHERE id=?",
                        (r.finding.ref_id,),
                    )
                    r.verified = row is not None and row["status"] == "rolled_back"
            except Exception as e:
                logger.warning(f"Watchdog: verification check failed: {e}")
                r.verified = False

    # --- Alert phase ---

    def _alert(self, findings: list[Finding], remediations: list[RemediationResult]) -> None:
        actionable = [f for f in findings if f.severity != "ok"]
        failed_verifications = [r for r in remediations if r.verified is False]

        if not actionable and not failed_verifications:
            return

        if not self.settings.slack_webhook_url:
            return

        from ortobahn.integrations.slack import format_watchdog_alert, send_slack_message

        text = format_watchdog_alert(actionable, remediations)
        send_slack_message(self.settings.slack_webhook_url, text)

    # --- Record phase ---

    def _record(self, findings: list[Finding], remediations: list[RemediationResult]) -> None:
        for f in findings:
            try:
                self.db.save_health_check(
                    probe=f.probe,
                    status=f.severity,
                    detail=f.detail,
                    client_id=f.client_id,
                )
            except Exception as e:
                logger.warning(f"Watchdog: failed to record health check: {e}")

        for r in remediations:
            try:
                self.db.save_remediation(
                    finding_type=r.finding.probe,
                    action=r.action,
                    success=r.success,
                    client_id=r.finding.client_id,
                    verified=r.verified,
                )
            except Exception as e:
                logger.warning(f"Watchdog: failed to record remediation: {e}")
