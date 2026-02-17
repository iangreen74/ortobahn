"""CFO Agent - tracks costs, ROI, and makes budget recommendations."""

from __future__ import annotations

import json
import logging

from ortobahn.agents.base import BaseAgent
from ortobahn.models import CFOReport

logger = logging.getLogger("ortobahn.cfo")


class CFOAgent(BaseAgent):
    name = "cfo"
    prompt_file = "cfo.txt"

    def run(self, run_id: str, **kwargs) -> CFOReport:
        recent_runs = self.db.get_recent_runs(limit=50)
        if not recent_runs:
            self.log_decision(
                run_id=run_id,
                input_summary="No pipeline runs to analyze",
                output_summary="Empty CFO report (no data)",
            )
            return CFOReport()

        # Calculate costs
        total_input_tokens = sum(r.get("total_input_tokens") or 0 for r in recent_runs)
        total_output_tokens = sum(r.get("total_output_tokens") or 0 for r in recent_runs)
        total_cache_creation = sum(r.get("total_cache_creation_tokens") or 0 for r in recent_runs)
        total_cache_read = sum(r.get("total_cache_read_tokens") or 0 for r in recent_runs)
        total_posts = sum(r.get("posts_published") or 0 for r in recent_runs)

        # Sonnet pricing: $3/M input, $3.75/M cache write, $0.30/M cache read, $15/M output
        uncached_input = max(0, total_input_tokens - total_cache_creation - total_cache_read)
        input_cost = uncached_input / 1_000_000 * 3
        cache_write_cost = total_cache_creation / 1_000_000 * 3.75
        cache_read_cost = total_cache_read / 1_000_000 * 0.30
        output_cost = total_output_tokens / 1_000_000 * 15
        total_cost = input_cost + cache_write_cost + cache_read_cost + output_cost

        cost_per_post = total_cost / total_posts if total_posts else 0

        # Get engagement data
        posts_with_metrics = self.db.get_recent_posts_with_metrics(limit=50)
        total_engagements = sum(
            (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0)
            for p in posts_with_metrics
        )
        cost_per_engagement = total_cost / total_engagements if total_engagements else 0
        roi = total_engagements / total_cost if total_cost > 0 else 0

        # Build context for LLM
        cache_hit_rate = total_cache_read / total_input_tokens * 100 if total_input_tokens else 0

        user_message = f"""## Financial Metrics
Total API cost: ${total_cost:.4f}
  - Uncached input tokens: {uncached_input:,} (${input_cost:.4f})
  - Cache write tokens: {total_cache_creation:,} (${cache_write_cost:.4f})
  - Cache read tokens: {total_cache_read:,} (${cache_read_cost:.4f})
  - Output tokens: {total_output_tokens:,} (${output_cost:.4f})
  - Cache hit rate: {cache_hit_rate:.0f}%

Total posts published: {total_posts}
Cost per post: ${cost_per_post:.4f}

Total engagements: {total_engagements}
Cost per engagement: ${cost_per_engagement:.4f}
ROI (engagements per dollar): {roi:.1f}

Pipeline runs: {len(recent_runs)}
"""

        response = self.call_llm(user_message)

        report = CFOReport(
            total_spend_24h=total_cost,
            cost_per_post=cost_per_post,
            cost_per_engagement=cost_per_engagement,
            total_engagements_24h=total_engagements,
            roi_estimate=roi,
        )

        try:
            analysis = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
            report.budget_status = analysis.get("budget_status", "within_budget")
            report.recommendations = analysis.get("recommendations", [])
            report.summary = analysis.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            report.summary = response.text[:500]

        # Per-client budget enforcement
        all_clients = self.db.fetchall("SELECT * FROM clients WHERE active=1")
        paused_clients = []
        for c in all_clients:
            budget = c.get("monthly_budget") or 0
            if budget > 0:
                spend = self.db.get_current_month_spend(c["id"])
                if spend >= budget:
                    self.db.pause_client(c["id"])
                    paused_clients.append(c["name"])
                    logger.warning(f"Client {c['name']} paused: spend ${spend:.2f} >= budget ${budget:.2f}")

        if paused_clients:
            report.recommendations = report.recommendations or []
            report.recommendations.append(f"Paused over-budget clients: {', '.join(paused_clients)}")

        self.log_decision(
            run_id=run_id,
            input_summary=f"${total_cost:.4f} spent, {total_posts} posts, {total_engagements} engagements",
            output_summary=f"Cost/post: ${cost_per_post:.4f}, ROI: {roi:.1f} eng/$, paused: {len(paused_clients)}",
            reasoning=f"Budget: {report.budget_status}",
            llm_response=response,
        )
        return report
