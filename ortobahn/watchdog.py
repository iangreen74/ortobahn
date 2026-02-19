"""Watchdog — closed-loop self-monitoring and self-healing system.

Runs outside the pipeline (in the scheduler loop) so it can detect failures
in the pipeline itself. Follows a Sense → Decide → Act → Verify loop.
"""

from __future__ import annotations

import logging
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
        if self.settings.watchdog_credential_check:
            findings.extend(self.probe_credential_health())
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
                            auto_fixable=False,
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
