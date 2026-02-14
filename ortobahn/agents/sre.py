"""SRE Agent - monitors system health and pipeline reliability."""

from __future__ import annotations

import json

from ortobahn.agents.base import BaseAgent
from ortobahn.models import SREAlert, SREReport


class SREAgent(BaseAgent):
    name = "sre"
    prompt_file = "sre.txt"

    def run(self, run_id: str, **kwargs) -> SREReport:
        # Gather operational metrics from DB
        recent_runs = self.db.get_recent_runs(limit=50)

        if not recent_runs:
            self.log_decision(
                run_id=run_id,
                input_summary="No pipeline runs to analyze",
                output_summary="Empty SRE report (no data)",
            )
            return SREReport(health_status="unknown")

        # Calculate metrics
        total_runs = len(recent_runs)
        failed_runs = sum(1 for r in recent_runs if r.get("status") == "failed")
        success_rate = (total_runs - failed_runs) / total_runs if total_runs else 0

        total_input_tokens = sum(r.get("total_input_tokens") or 0 for r in recent_runs)
        total_output_tokens = sum(r.get("total_output_tokens") or 0 for r in recent_runs)
        total_tokens = total_input_tokens + total_output_tokens

        # Estimate cost (Sonnet: $3/M input, $15/M output)
        estimated_cost = (total_input_tokens / 1_000_000 * 3) + (total_output_tokens / 1_000_000 * 15)

        # Get recent post confidence scores
        posts = self.db.get_all_posts(limit=50)
        confidences = [p.get("confidence", 0) for p in posts if p.get("confidence")]

        # Platform health: check last publish status per platform
        platform_health = {}
        for platform in ["bluesky", "twitter", "linkedin"]:
            platform_posts = [
                p for p in posts if p.get("platform") == platform and p.get("status") in ("published", "failed")
            ]
            if platform_posts:
                last = platform_posts[0]
                platform_health[platform] = "healthy" if last["status"] == "published" else "failing"
            else:
                platform_health[platform] = "no_data"

        # Build context for LLM analysis
        avg_conf = f"{sum(confidences) / len(confidences):.2f}" if confidences else "N/A"
        min_conf = f"{min(confidences):.2f}" if confidences else "N/A"
        max_conf = f"{max(confidences):.2f}" if confidences else "N/A"

        user_message = f"""## System Metrics
Pipeline runs analyzed: {total_runs}
Failed runs: {failed_runs}
Success rate: {success_rate:.1%}
Total tokens used: {total_tokens:,}
Estimated cost: ${estimated_cost:.4f}

## Confidence Scores
Recent posts: {len(confidences)}
Average confidence: {avg_conf}
Min confidence: {min_conf}
Max confidence: {max_conf}

## Platform Health
{json.dumps(platform_health, indent=2)}
"""

        response = self.call_llm(user_message)

        # Parse LLM response
        report = SREReport(
            pipeline_success_rate=success_rate,
            total_tokens_24h=total_tokens,
            estimated_cost_24h=estimated_cost,
            platform_health=platform_health,
        )

        try:
            analysis = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
            report.health_status = analysis.get("health_status", "unknown")
            report.avg_confidence_trend = analysis.get("avg_confidence_trend", "stable")
            report.alerts = [SREAlert(**a) for a in analysis.get("alerts", [])]
            report.recommendations = analysis.get("recommendations", [])
        except (json.JSONDecodeError, KeyError, TypeError):
            report.health_status = "healthy" if success_rate > 0.8 else "degraded"

        # Send Slack alert if health is degraded/critical
        slack_url = kwargs.get("slack_webhook_url", "")
        if slack_url and report.health_status in ("degraded", "critical"):
            from ortobahn.integrations.slack import format_sre_alert, send_slack_message

            message = format_sre_alert(report.health_status, report.alerts, report.recommendations)
            send_slack_message(slack_url, message)

        self.log_decision(
            run_id=run_id,
            input_summary=f"{total_runs} runs, {len(confidences)} posts analyzed",
            output_summary=f"Health: {report.health_status}, Success rate: {success_rate:.1%}",
            reasoning=f"Alerts: {len(report.alerts)}, Cost: ${estimated_cost:.4f}",
            llm_response=response,
        )
        return report
