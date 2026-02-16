"""Preflight Intelligence — validate environment before running the pipeline."""

from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse

from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.models import PreflightIssue, PreflightResult, PreflightSeverity

logger = logging.getLogger("ortobahn.preflight")


# ---------------------------------------------------------------------------
# DNS helpers
# ---------------------------------------------------------------------------


def resolve_host(hostname: str, timeout: float = 5.0) -> bool:
    """Return True if *hostname* resolves via DNS within *timeout* seconds."""
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return True
    except (socket.gaierror, OSError):
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def check_dns_for_urls(urls: list[str]) -> list[PreflightIssue]:
    """Check DNS for a list of URLs. Deduplicates hosts, skips localhost."""
    issues: list[PreflightIssue] = []
    seen_hosts: set[str] = set()

    for url in urls:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)

        # Skip localhost / loopback
        if host in ("localhost", "127.0.0.1", "::1"):
            continue

        if not resolve_host(host):
            issues.append(
                PreflightIssue(
                    severity=PreflightSeverity.WARNING,
                    component="dns",
                    message=f"Cannot resolve host: {host}",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Budget headroom
# ---------------------------------------------------------------------------


def check_budget_headroom(
    db: Database,
    client_id: str,
    monthly_budget: float,
) -> list[PreflightIssue]:
    """Check whether the client still has budget headroom.

    * monthly_budget <= 0  -> unlimited, no issues.
    * spend >= budget       -> BLOCKING
    * spend >= 90% budget   -> WARNING
    """
    issues: list[PreflightIssue] = []
    if monthly_budget <= 0:
        return issues

    try:
        spend = db.get_current_month_spend(client_id)
    except Exception as exc:
        logger.warning("Could not query month spend: %s", exc)
        return issues

    if spend >= monthly_budget:
        issues.append(
            PreflightIssue(
                severity=PreflightSeverity.BLOCKING,
                component="budget",
                message=f"Monthly budget exhausted: ${spend:.2f} / ${monthly_budget:.2f}",
            )
        )
    elif spend >= monthly_budget * 0.9:
        issues.append(
            PreflightIssue(
                severity=PreflightSeverity.WARNING,
                component="budget",
                message=f"Budget nearly exhausted: ${spend:.2f} / ${monthly_budget:.2f} (>{90}%)",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Platform credentials
# ---------------------------------------------------------------------------


def check_platform_credentials(settings: Settings) -> list[PreflightIssue]:
    """Delegate to healthcheck functions.  Config / Anthropic failure = BLOCKING,
    platform failures = WARNING."""
    from ortobahn.healthcheck import (
        check_bluesky,
        check_config,
        check_linkedin,
        check_twitter,
    )

    issues: list[PreflightIssue] = []

    config_result = check_config(settings, require_bluesky=False)
    if not config_result.ok:
        issues.append(
            PreflightIssue(
                severity=PreflightSeverity.BLOCKING,
                component="config",
                message=config_result.message,
            )
        )

    for name, check_fn in [
        ("bluesky", check_bluesky),
        ("twitter", check_twitter),
        ("linkedin", check_linkedin),
    ]:
        result = check_fn(settings)
        if not result.ok and "not configured" not in result.message.lower():
            issues.append(
                PreflightIssue(
                    severity=PreflightSeverity.WARNING,
                    component=name,
                    message=result.message,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# API reachability (DNS-only, no auth)
# ---------------------------------------------------------------------------


def check_api_reachability(settings: Settings) -> list[PreflightIssue]:
    """DNS-resolve critical API hosts.  Anthropic = BLOCKING, platforms = WARNING."""
    issues: list[PreflightIssue] = []

    if not resolve_host("api.anthropic.com"):
        issues.append(
            PreflightIssue(
                severity=PreflightSeverity.BLOCKING,
                component="api_reachability",
                message="Cannot resolve api.anthropic.com",
            )
        )

    platform_hosts: list[tuple[str, bool]] = [
        ("bsky.social", bool(settings.bluesky_handle)),
        ("api.x.com", settings.has_twitter()),
        ("api.linkedin.com", settings.has_linkedin()),
    ]
    for host, configured in platform_hosts:
        if configured and not resolve_host(host):
            issues.append(
                PreflightIssue(
                    severity=PreflightSeverity.WARNING,
                    component="api_reachability",
                    message=f"Cannot resolve {host}",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Pipeline-level runner
# ---------------------------------------------------------------------------


def run_pipeline_preflight(
    settings: Settings,
    db: Database,
    client_id: str,
    check_apis: bool = True,
) -> PreflightResult:
    """Execute all preflight checks and return an aggregate result."""
    start = time.monotonic()
    all_issues: list[PreflightIssue] = []

    # 1. Credentials
    all_issues.extend(check_platform_credentials(settings))

    # 2. Budget
    client_data = db.get_client(client_id)
    monthly_budget = (client_data.get("monthly_budget", 0.0) if client_data else 0.0) or settings.default_monthly_budget
    all_issues.extend(check_budget_headroom(db, client_id, monthly_budget))

    # 3. API reachability (optional — can be slow)
    if check_apis:
        all_issues.extend(check_api_reachability(settings))

    elapsed_ms = (time.monotonic() - start) * 1000
    passed = not any(i.severity == PreflightSeverity.BLOCKING for i in all_issues)
    return PreflightResult(
        passed=passed,
        issues=all_issues,
        duration_ms=round(elapsed_ms, 1),
    )
