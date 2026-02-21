"""Security Agent - monitors threats, assesses security posture, recommends hardening."""

from __future__ import annotations

import json
import logging

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import SecurityReport

logger = logging.getLogger("ortobahn.security")


class SecurityAgent(BaseAgent):
    name = "security"
    prompt_file = "security.txt"
    thinking_budget = 8_000

    def run(self, run_id: str, **kwargs) -> SecurityReport:
        # 1. Scan access logs for suspicious patterns
        suspicious = self.db.get_suspicious_access_logs(hours=24)

        # 2. Check credential health across clients
        credential_health = self._check_credential_health()

        # 3. Check security configuration
        security_config = self._get_security_config()

        # Build context for LLM
        parts = ["## Access Log Analysis (last 24h)"]
        if suspicious:
            parts.append(f"Found {len(suspicious)} suspicious requests:")
            # Group by path pattern
            path_counts: dict[str, int] = {}
            ip_counts: dict[str, int] = {}
            for entry in suspicious:
                path = entry.get("path", "unknown")
                ip = entry.get("source_ip", "unknown")
                path_counts[path] = path_counts.get(path, 0) + 1
                ip_counts[ip] = ip_counts.get(ip, 0) + 1
            for path, count in sorted(path_counts.items(), key=lambda x: -x[1])[:10]:
                parts.append(f"  - {path}: {count} requests")
            parts.append("\nTop source IPs:")
            for ip, count in sorted(ip_counts.items(), key=lambda x: -x[1])[:5]:
                parts.append(f"  - {ip}: {count} requests")
        else:
            parts.append("No suspicious requests detected in the last 24 hours.")

        parts.append("\n## Credential Health")
        for platform, status in credential_health.items():
            parts.append(f"  - {platform}: {status}")

        parts.append("\n## Security Configuration")
        parts.append(json.dumps(security_config, indent=2))

        user_message = "\n".join(parts)
        response = self.call_llm(user_message)

        try:
            report = parse_json_response(response.text, SecurityReport)
        except Exception:
            # Fallback: build report from raw data
            report = SecurityReport(
                threat_level="low" if not suspicious else "medium",
                summary=response.text[:500],
            )

        # Ensure credential health is populated
        if not report.credential_health:
            report.credential_health = credential_health

        # Clean up old access logs
        try:
            self.db.cleanup_access_logs(days=7)
        except Exception:
            pass

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(suspicious)} suspicious requests, {len(credential_health)} platforms checked",
            output_summary=f"Threat level: {report.threat_level}, {len(report.threats_detected)} threats, {len(report.recommendations)} recommendations",
            reasoning=report.summary[:200],
            llm_response=response,
        )
        return report

    def _check_credential_health(self) -> dict[str, str]:
        """Check credential status across all active clients."""
        health: dict[str, str] = {}
        for platform in ("bluesky", "twitter", "linkedin"):
            rows = self.db.fetchall(
                "SELECT client_id, created_at, updated_at FROM platform_credentials WHERE platform=?",
                (platform,),
            )
            if rows:
                health[platform] = f"configured ({len(rows)} clients)"
            else:
                health[platform] = "no credentials stored"
        return health

    def _get_security_config(self) -> dict:
        """Return current security configuration summary."""
        return {
            "authentication": "API key (bcrypt hashed) + AWS Cognito",
            "credential_encryption": "Fernet (AES-128-CBC) with ORTOBAHN_SECRET_KEY",
            "rate_limiting": "enabled (sliding window, in-memory)",
            "cors": "restricted to known origins",
            "https": "via ALB termination",
            "data_isolation": "per-client scoping on all queries",
        }
