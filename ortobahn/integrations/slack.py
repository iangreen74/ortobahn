"""Slack webhook integration for alerts."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("ortobahn.slack")


def send_slack_message(webhook_url: str, text: str) -> bool:
    """Send a message to Slack via webhook. Returns True on success."""
    if not webhook_url:
        return False
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Failed to send Slack alert: {e}")
        return False


def format_sre_alert(health_status: str, alerts: list, recommendations: list) -> str:
    """Format an SRE report as a Slack message."""
    status_emoji = {
        "healthy": ":white_check_mark:",
        "degraded": ":warning:",
        "critical": ":rotating_light:",
    }.get(health_status, ":question:")

    lines = [f"{status_emoji} *Ortobahn SRE Report: {health_status.upper()}*"]

    if alerts:
        lines.append("\n*Alerts:*")
        for a in alerts:
            sev = a.severity if hasattr(a, "severity") else a.get("severity", "")
            comp = a.component if hasattr(a, "component") else a.get("component", "")
            msg = a.message if hasattr(a, "message") else a.get("message", "")
            sev_emoji = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}.get(
                sev, ""
            )
            lines.append(f"  {sev_emoji} [{comp}] {msg}")

    if recommendations:
        lines.append("\n*Recommendations:*")
        for r in recommendations:
            lines.append(f"  - {r}")

    return "\n".join(lines)


def format_watchdog_alert(findings: list, remediations: list) -> str:
    """Format a Watchdog report as a Slack message."""
    critical = [f for f in findings if getattr(f, "severity", "") == "critical"]
    warnings = [f for f in findings if getattr(f, "severity", "") == "warning"]

    if critical:
        emoji = ":rotating_light:"
        status = "CRITICAL"
    elif warnings:
        emoji = ":warning:"
        status = "WARNING"
    else:
        emoji = ":white_check_mark:"
        status = "OK"

    lines = [f"{emoji} *Ortobahn Watchdog: {status}*"]

    for f in findings:
        if f.severity == "ok":
            continue
        sev_emoji = {
            "critical": ":rotating_light:",
            "warning": ":warning:",
        }.get(f.severity, ":information_source:")
        client_tag = f" (client: {f.client_id})" if f.client_id else ""
        lines.append(f"  {sev_emoji} [{f.probe}]{client_tag} {f.detail}")

    if remediations:
        lines.append("\n*Auto-Remediations:*")
        for r in remediations:
            status_icon = ":white_check_mark:" if r.success else ":x:"
            verified_tag = ""
            if r.verified is True:
                verified_tag = " (verified)"
            elif r.verified is False:
                verified_tag = " (verification failed)"
            lines.append(f"  {status_icon} {r.action}{verified_tag}")

    return "\n".join(lines)


def format_deploy_alert(sha: str, environment: str, status: str, detail: str = "") -> str:
    """Format a deployment event as a Slack message."""
    emoji = {
        "deployed": ":rocket:",
        "validated": ":white_check_mark:",
        "rolled_back": ":rotating_light:",
        "smoke_failed": ":x:",
    }.get(status, ":gear:")

    lines = [f"{emoji} *Ortobahn Deploy: {status.upper()}*"]
    lines.append(f"  Environment: {environment}")
    lines.append(f"  SHA: `{sha[:7]}`")
    if detail:
        lines.append(f"  {detail}")
    return "\n".join(lines)
