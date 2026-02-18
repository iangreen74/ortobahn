"""Support Agent - monitors customer health and takes proactive action."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ortobahn.agents.base import BaseAgent
from ortobahn.models import SupportReport, SupportTicket

logger = logging.getLogger("ortobahn.support")


class SupportAgent(BaseAgent):
    name = "support"
    prompt_file = "support.txt"
    thinking_budget = 8_000

    def run(self, run_id: str, **kwargs) -> SupportReport:
        # Query all active clients
        all_clients = self.db.fetchall(
            "SELECT * FROM clients WHERE status='active' OR active=1 ORDER BY created_at DESC"
        )

        if not all_clients:
            self.log_decision(
                run_id=run_id,
                input_summary="No active clients to check",
                output_summary="Empty support report (no clients)",
            )
            return SupportReport()

        # Gather health data for each client
        client_health_entries: list[str] = []
        total_checked = 0

        for client in all_clients:
            cid = client["id"]
            total_checked += 1

            # Profile completeness
            profile_fields = ["name", "industry", "brand_voice", "website", "products"]
            filled = sum(1 for f in profile_fields if client.get(f))
            profile_pct = filled / len(profile_fields) * 100

            # Credential completeness
            creds = self.db.fetchall(
                "SELECT * FROM platform_credentials WHERE client_id=?", (cid,)
            )
            cred_count = len(creds) if creds else 0

            # Pipeline success rate (recent runs for this client)
            client_runs = self.db.fetchall(
                "SELECT * FROM pipeline_runs WHERE client_id=? ORDER BY started_at DESC LIMIT 20",
                (cid,),
            )
            total_runs = len(client_runs) if client_runs else 0
            failed_runs = (
                sum(1 for r in client_runs if r.get("status") == "failed")
                if client_runs
                else 0
            )
            success_rate = (
                (total_runs - failed_runs) / total_runs if total_runs > 0 else 0
            )

            # Days since last successful run
            successful_runs = (
                [r for r in client_runs if r.get("status") == "completed"]
                if client_runs
                else []
            )
            if successful_runs:
                last_success = successful_runs[0]
                try:
                    last_ts = last_success.get("completed_at") or last_success.get(
                        "started_at"
                    )
                    if last_ts:
                        if isinstance(last_ts, str):
                            last_dt = datetime.fromisoformat(last_ts)
                        else:
                            last_dt = last_ts
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        days_since = (
                            datetime.now(timezone.utc) - last_dt
                        ).days
                    else:
                        days_since = -1
                except (ValueError, TypeError):
                    days_since = -1
            else:
                days_since = -1

            # Trial status
            sub_status = client.get("subscription_status", "none")
            trial_days_remaining = -1
            if sub_status == "trialing" and client.get("trial_ends_at"):
                try:
                    trial_end_str = client["trial_ends_at"]
                    if isinstance(trial_end_str, str):
                        trial_end = datetime.fromisoformat(trial_end_str)
                    else:
                        trial_end = trial_end_str
                    if trial_end.tzinfo is None:
                        trial_end = trial_end.replace(tzinfo=timezone.utc)
                    trial_days_remaining = (
                        trial_end - datetime.now(timezone.utc)
                    ).days
                except (ValueError, TypeError):
                    trial_days_remaining = -1

            # Auto-publish setting
            auto_publish = bool(client.get("auto_publish", 0))

            entry = (
                f"### {client.get('name', cid)} (ID: {cid})\n"
                f"- Profile completeness: {profile_pct:.0f}% ({filled}/{len(profile_fields)} fields)\n"
                f"- Platform credentials: {cred_count}\n"
                f"- Pipeline runs (recent 20): {total_runs} total, {failed_runs} failed, "
                f"success rate: {success_rate:.0%}\n"
                f"- Days since last successful run: {days_since if days_since >= 0 else 'never'}\n"
                f"- Subscription status: {sub_status}\n"
                f"- Trial days remaining: {trial_days_remaining if trial_days_remaining >= 0 else 'N/A'}\n"
                f"- Auto-publish: {'enabled' if auto_publish else 'disabled'}\n"
            )
            client_health_entries.append(entry)

        user_message = f"""## Client Health Report
Total active clients checked: {total_checked}

"""
        user_message += "\n".join(client_health_entries)

        response = self.call_llm(user_message)

        # Parse LLM response
        report = SupportReport(total_clients_checked=total_checked)

        try:
            analysis = json.loads(
                response.text.strip().strip("`").removeprefix("json").strip()
            )
            report.tickets = [
                SupportTicket(**t) for t in analysis.get("tickets", [])
            ]
            report.health_summary = analysis.get("health_summary", "")
            report.at_risk_clients = analysis.get("at_risk_clients", [])
            report.recommendations = analysis.get("recommendations", [])
        except (json.JSONDecodeError, KeyError, TypeError):
            report.health_summary = response.text[:500]

        self.log_decision(
            run_id=run_id,
            input_summary=f"{total_checked} active clients checked",
            output_summary=f"Tickets: {len(report.tickets)}, At-risk: {len(report.at_risk_clients)}",
            reasoning=report.health_summary[:200],
            llm_response=response,
        )
        return report
