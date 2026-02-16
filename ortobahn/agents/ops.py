"""Ops Agent - manages day-to-day operations, onboarding, and scheduling."""

from __future__ import annotations

import json
import logging

from ortobahn.agents.base import BaseAgent
from ortobahn.models import OpsAction, OpsReport

logger = logging.getLogger("ortobahn.ops")


class OpsAgent(BaseAgent):
    name = "ops"
    prompt_file = "ops.txt"

    def run(self, run_id: str, **kwargs) -> OpsReport:
        # Get client data
        all_clients = self.db.conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
        all_clients = [dict(r) for r in all_clients]

        pending_clients = [c for c in all_clients if c.get("status") == "pending"]
        active_clients = [c for c in all_clients if c.get("status") == "active" or c.get("active")]

        # Get recent runs per client
        recent_runs = self.db.get_recent_runs(limit=50)
        client_run_counts: dict[str, int] = {}
        for r in recent_runs:
            cid = r.get("client_id", "default")
            client_run_counts[cid] = client_run_counts.get(cid, 0) + 1

        # Auto-action: activate pending clients that have complete profiles
        actions: list[OpsAction] = []
        for client in pending_clients:
            has_profile = all(
                [
                    client.get("name"),
                    client.get("industry"),
                    client.get("brand_voice"),
                ]
            )
            if has_profile:
                # Auto-enrich profile if key fields are empty
                needs_enrichment = not client.get("products") and not client.get("content_pillars")
                if needs_enrichment:
                    try:
                        from ortobahn.agents.enrichment import EnrichmentAgent

                        enrichment_agent = EnrichmentAgent(db=self.db, api_key=self.api_key, model=self.model)
                        enrichment = enrichment_agent.run(run_id=run_id, client_data=client)
                        if enrichment:
                            self.db.update_client(client["id"], enrichment)
                            actions.append(
                                OpsAction(
                                    action="enrich_client",
                                    target=client["name"],
                                    status="completed",
                                    detail=f"Auto-enriched {len(enrichment)} profile fields for {client['name']}",
                                )
                            )
                    except Exception as e:
                        logger.warning(f"Failed to enrich client {client.get('name')}: {e}")

                self.db.conn.execute(
                    "UPDATE clients SET status='active', active=1 WHERE id=?",
                    (client["id"],),
                )
                self.db.conn.commit()
                actions.append(
                    OpsAction(
                        action="activate_client",
                        target=client["name"],
                        status="completed",
                        detail=f"Auto-activated client with complete profile: {client['name']}",
                    )
                )

        # Build context for LLM
        user_message = f"""## Operations Status
Total clients: {len(all_clients)}
Active clients: {len(active_clients)}
Pending onboarding: {len(pending_clients)}

## Client Details
"""
        for c in all_clients:
            runs = client_run_counts.get(c["id"], 0)
            user_message += f"- {c['name']} (status: {c.get('status', 'active')}, industry: {c.get('industry', 'unknown')}, runs: {runs})\n"

        user_message += "\n## Auto-Actions Taken This Cycle\n"
        for a in actions:
            user_message += f"- {a.action}: {a.detail}\n"

        response = self.call_llm(user_message)

        report = OpsReport(
            pending_clients=len(pending_clients),
            active_clients=len(active_clients),
            actions_taken=actions,
        )

        try:
            analysis = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
            report.recommendations = analysis.get("recommendations", [])
            report.summary = analysis.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            report.summary = response.text[:500]

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(all_clients)} clients, {len(pending_clients)} pending",
            output_summary=f"Actions: {len(actions)}, Active: {len(active_clients)}",
            reasoning=report.summary[:200],
            llm_response=response,
        )
        return report
