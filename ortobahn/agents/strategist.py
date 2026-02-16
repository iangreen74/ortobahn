"""Content Strategist Agent - plans specific content from strategy + trends."""

from __future__ import annotations

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import Client, ContentPlan, Strategy, TrendingTopic


class StrategistAgent(BaseAgent):
    name = "strategist"
    prompt_file = "strategist.txt"
    thinking_budget = 8_000

    def run(
        self,
        run_id: str,
        strategy: Strategy,
        trending: list[TrendingTopic] | None = None,
        client: Client | None = None,
    ) -> ContentPlan:
        # Format prompt with client context
        if client:
            system_prompt = self.format_prompt(
                client_name=client.name,
                client_description=client.description,
                client_target_audience=client.target_audience,
                client_brand_voice=client.brand_voice,
                client_products=client.products or "Not specified",
                client_content_pillars=client.content_pillars or "Not specified",
            )
        else:
            system_prompt = None

        parts = ["## Current Strategy"]
        parts.append(f"Themes: {', '.join(strategy.themes)}")
        parts.append(f"Tone: {strategy.tone}")
        parts.append(f"Goals: {', '.join(strategy.goals)}")
        parts.append(f"Guidelines: {strategy.content_guidelines}")
        if strategy.target_platforms:
            parts.append(
                f"Target platforms: {', '.join(p.value if hasattr(p, 'value') else str(p) for p in strategy.target_platforms)}"
            )

        if trending:
            parts.append("\n## Trending Topics")
            for t in trending:
                parts.append(f"- [{t.source}] {t.title}: {t.description or ''}")
        else:
            parts.append("\nNo trending topics available. Generate ideas from the strategy themes alone.")

        # Inject memory context
        client_id = client.id if client else "default"
        memory_context = self.get_memory_context(client_id)
        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)
        plan = parse_json_response(response.text, ContentPlan)

        # Sort by priority
        plan.posts.sort(key=lambda p: p.priority)

        self.log_decision(
            run_id=run_id,
            input_summary=f"Strategy themes: {strategy.themes}, Trends: {len(trending or [])}",
            output_summary=f"Planned {len(plan.posts)} posts: {[p.topic for p in plan.posts]}",
            reasoning=f"Content types: {[p.content_type.value for p in plan.posts]}",
            llm_response=response,
        )
        return plan
