"""CEO Agent - sets marketing strategy and direction."""

from __future__ import annotations

from datetime import datetime, timedelta

from ortobahn.agents.base import BaseAgent
from ortobahn.db import to_datetime
from ortobahn.llm import parse_json_response
from ortobahn.models import AnalyticsReport, Client, Platform, ReflectionReport, Strategy, TrendingTopic


class CEOAgent(BaseAgent):
    name = "ceo"
    prompt_file = "ceo.txt"
    thinking_budget = 10_000

    def run(
        self,
        run_id: str,
        analytics_report: AnalyticsReport | None = None,
        trending: list[TrendingTopic] | None = None,
        client: Client | None = None,
        performance_insights: str = "",
        reflection_report: ReflectionReport | None = None,
        **kwargs,
    ) -> Strategy:
        client_id = client.id if client else "default"

        # Check for existing valid strategy scoped to client
        existing = self.db.get_active_strategy(client_id=client_id)
        if existing:
            strategy = Strategy(
                themes=existing["themes"],
                tone=existing["tone"],
                goals=existing["goals"],
                content_guidelines=existing["content_guidelines"],
                posting_frequency=existing["posting_frequency"],
                valid_until=to_datetime(existing["valid_until"]),
                client_id=client_id,
            )
            self.log_decision(
                run_id=run_id,
                input_summary=f"Reusing active strategy for {client_id} (still valid)",
                output_summary=f"Themes: {', '.join(strategy.themes)}",
                reasoning="Existing strategy has not expired yet",
            )
            return strategy

        # Format prompt with client context
        if client:
            system_prompt = self.format_prompt(
                client_name=client.name,
                client_description=client.description,
                client_industry=client.industry,
                client_target_audience=client.target_audience,
                client_brand_voice=client.brand_voice,
                client_website=client.website,
                client_products=client.products or "Not specified",
                client_competitive_positioning=client.competitive_positioning or "Not specified",
                client_content_pillars=client.content_pillars or "Not specified",
                client_company_story=client.company_story or "Not specified",
                available_platforms=", ".join(p.value for p in Platform if p != Platform.BLUESKY),
            )
        else:
            system_prompt = None

        # Build the user message for the LLM
        parts = [f"Current date: {datetime.utcnow().isoformat()}"]
        parts.append(f"Strategy should be valid until: {(datetime.utcnow() + timedelta(days=7)).isoformat()}")

        if analytics_report and analytics_report.total_posts > 0:
            parts.append(f"\n## Recent Performance\n{analytics_report.model_dump_json(indent=2)}")
        else:
            parts.append("\nThis is the FIRST RUN. No previous analytics. Bootstrap a strong initial strategy.")

        if trending:
            parts.append("\n## Current Trending Topics")
            for t in trending[:10]:
                parts.append(f"- [{t.source}] {t.title}: {t.description or ''}")

        if performance_insights:
            parts.append(f"\n{performance_insights}")

        # Inject reflection report insights
        if reflection_report:
            parts.append("\n## Reflection Insights")
            parts.append(f"Confidence accuracy: {reflection_report.confidence_accuracy:.2f}")
            parts.append(f"Confidence bias: {reflection_report.confidence_bias}")
            if reflection_report.content_patterns:
                cp = reflection_report.content_patterns
                if cp.winning_attributes:
                    parts.append(f"Winning attributes: {', '.join(cp.winning_attributes)}")
                if cp.losing_attributes:
                    parts.append(f"Losing attributes: {', '.join(cp.losing_attributes)}")
            if reflection_report.recommendations:
                parts.append("Recommendations:")
                for rec in reflection_report.recommendations:
                    parts.append(f"- {rec}")

        # Inject memory context
        memory_context = self.get_memory_context(client_id)
        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)
        strategy = parse_json_response(response.text, Strategy)
        strategy.client_id = client_id

        # Save to DB
        strategy_data = strategy.model_dump()
        strategy_data["valid_until"] = strategy.valid_until.isoformat()
        self.db.save_strategy(strategy_data, run_id, raw_response=response.text, client_id=client_id)

        self.log_decision(
            run_id=run_id,
            input_summary=f"Analytics: {analytics_report.total_posts if analytics_report else 0} posts, Trends: {len(trending or [])}, Client: {client_id}",
            output_summary=f"Strategy set: themes={strategy.themes}",
            reasoning=f"Tone: {strategy.tone}",
            llm_response=response,
        )
        return strategy
