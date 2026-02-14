"""Content Creator Agent - writes platform-specific content."""

from __future__ import annotations

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import (
    PLATFORM_CONSTRAINTS,
    Client,
    ContentPlan,
    ContentType,
    DraftPosts,
    Platform,
    Strategy,
)


class CreatorAgent(BaseAgent):
    name = "creator"
    prompt_file = "creator.txt"
    thinking_budget = 6_000

    def _build_platform_constraints_block(self, platforms: list[Platform]) -> str:
        lines = []
        for p in platforms:
            c = PLATFORM_CONSTRAINTS.get(p, PLATFORM_CONSTRAINTS[Platform.GENERIC])
            lines.append(
                f"- {p.value}: max {c['max_chars']} chars, hashtags={'yes' if c['hashtags'] else 'no'}, tone={c['tone']}"
            )
            if p == Platform.GOOGLE_ADS:
                lines.append("  - Ad headlines: max 30 chars")
                lines.append("  - Ad descriptions: max 90 chars")
        return "\n".join(lines)

    def _get_max_chars(self, platform: Platform, content_type: ContentType) -> int:
        if content_type == ContentType.AD_HEADLINE:
            return 30
        if content_type == ContentType.AD_DESCRIPTION:
            return 90
        c = PLATFORM_CONSTRAINTS.get(platform, PLATFORM_CONSTRAINTS[Platform.GENERIC])
        return c["max_chars"]

    def run(
        self,
        run_id: str,
        content_plan: ContentPlan,
        strategy: Strategy,
        client: Client | None = None,
        target_platforms: list[Platform] | None = None,
    ) -> DraftPosts:
        platforms = target_platforms or [Platform.GENERIC]

        # Format prompt with client context and platform constraints
        if client:
            system_prompt = self.format_prompt(
                client_name=client.name,
                client_brand_voice=client.brand_voice,
                client_target_audience=client.target_audience,
                client_products=client.products or "Not specified",
                platform_constraints=self._build_platform_constraints_block(platforms),
            )
        else:
            system_prompt = self.format_prompt(
                client_name="Ortobahn",
                client_brand_voice="authoritative but approachable",
                client_target_audience="tech-savvy professionals",
                platform_constraints=self._build_platform_constraints_block(platforms),
            )

        parts = [f"## Tone & Guidelines\nTone: {strategy.tone}"]
        parts.append(f"Guidelines: {strategy.content_guidelines}")
        parts.append(f"Target platforms: {', '.join(p.value for p in platforms)}")
        parts.append("\n## Post Ideas to Write")

        for i, idea in enumerate(content_plan.posts, 1):
            parts.append(f"\n### Idea {i}")
            parts.append(f"Topic: {idea.topic}")
            parts.append(f"Angle: {idea.angle}")
            parts.append(f"Hook: {idea.hook}")
            parts.append(f"Type: {idea.content_type.value}")
            if idea.target_platforms:
                parts.append(
                    f"Target platforms: {', '.join(p.value if hasattr(p, 'value') else str(p) for p in idea.target_platforms)}"
                )

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)
        drafts = parse_json_response(response.text, DraftPosts)

        # Enforce per-platform character limits
        for draft in drafts.posts:
            max_chars = self._get_max_chars(draft.platform, draft.content_type)
            if len(draft.text) > max_chars:
                draft.text = draft.text[: max_chars - 3] + "..."
                draft.confidence = min(draft.confidence, 0.5)

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(content_plan.posts)} ideas, platforms: {[p.value for p in platforms]}",
            output_summary=f"Created {len(drafts.posts)} drafts, avg confidence {sum(d.confidence for d in drafts.posts) / len(drafts.posts):.2f}"
            if drafts.posts
            else "No drafts created",
            reasoning=f"Posts: {[f'{d.platform.value}:{d.text[:30]}' for d in drafts.posts]}",
            llm_response=response,
        )
        return drafts
