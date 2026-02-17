"""Marketing Agent - generates marketing content for Ortobahn itself."""

from __future__ import annotations

import json

from ortobahn.agents.base import BaseAgent
from ortobahn.models import MarketingIdea, MarketingReport


class MarketingAgent(BaseAgent):
    name = "marketing"
    prompt_file = "marketing.txt"
    thinking_budget = 8_000

    def run(self, run_id: str, **kwargs) -> MarketingReport:
        # Gather real platform metrics for marketing material
        posts = self.db.get_all_posts(limit=100)
        published = [p for p in posts if p.get("status") == "published"]
        recent_runs = self.db.get_recent_runs(limit=50)

        total_runs = len(recent_runs)
        failed_runs = sum(1 for r in recent_runs if r.get("status") == "failed")
        success_rate = (total_runs - failed_runs) / total_runs if total_runs else 0

        # Client count
        clients = self.db.fetchone("SELECT COUNT(*) as c FROM clients WHERE active=1")
        client_count = clients["c"] if clients else 0

        # Platform breakdown
        platform_counts: dict[str, int] = {}
        for p in published:
            plat = p.get("platform", "unknown")
            platform_counts[plat] = platform_counts.get(plat, 0) + 1

        # Sample recent posts for inspiration
        recent_published = published[:5]
        sample_posts = [p.get("text", "")[:150] for p in recent_published]

        user_message = f"""## Ortobahn Platform Metrics
Total posts published: {len(published)}
Active clients: {client_count}
Pipeline runs: {total_runs}
Pipeline success rate: {success_rate:.1%}
Platform breakdown: {json.dumps(platform_counts)}

## Sample Recent Posts (for reference, don't copy)
{chr(10).join(f"- {s}" for s in sample_posts) if sample_posts else "No posts yet."}

Generate marketing content for Ortobahn based on these real metrics. Focus on proof points and results.
"""

        response = self.call_llm(user_message)

        report = MarketingReport()
        try:
            analysis = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
            report.content_ideas = [MarketingIdea(**idea) for idea in analysis.get("content_ideas", [])]
            report.draft_posts = analysis.get("draft_posts", [])
            report.metrics_highlights = analysis.get("metrics_highlights", [])
            report.recommendations = analysis.get("recommendations", [])
            report.summary = analysis.get("summary", "")
        except (json.JSONDecodeError, KeyError, TypeError):
            report.summary = response.text[:500]

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(published)} posts, {client_count} clients, {success_rate:.1%} success",
            output_summary=f"Ideas: {len(report.content_ideas)}, Drafts: {len(report.draft_posts)}",
            reasoning=report.summary[:200],
            llm_response=response,
        )
        return report
