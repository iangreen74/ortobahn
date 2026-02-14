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
            sev_emoji = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}.get(sev, "")
            lines.append(f"  {sev_emoji} [{comp}] {msg}")

    if recommendations:
        lines.append("\n*Recommendations:*")
        for r in recommendations:
            lines.append(f"  - {r}")

    return "\n".join(lines)
