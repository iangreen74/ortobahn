"""Content Creator Agent - writes platform-specific content."""

from __future__ import annotations

import logging

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import (
    PLATFORM_CONSTRAINTS,
    Client,
    ContentPlan,
    ContentType,
    DraftPost,
    DraftPosts,
    Platform,
    Strategy,
)

logger = logging.getLogger("ortobahn.agents")


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
        enable_self_critique: bool = True,
        critique_threshold: float = 0.8,
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

        # Inject memory context
        client_id = client.id if client else "default"
        memory_context = self.get_memory_context(client_id)
        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)
        drafts = parse_json_response(response.text, DraftPosts)

        # Self-critique high-confidence drafts
        if enable_self_critique:
            high_confidence = [d for d in drafts.posts if d.confidence >= critique_threshold]
            if high_confidence:
                improved = self._self_critique(high_confidence, strategy, memory_context)
                if improved:
                    # Replace originals with improved versions
                    improved_topics = {d.source_idea for d in improved}
                    drafts.posts = [d for d in drafts.posts if d.source_idea not in improved_topics] + improved

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

    def _self_critique(
        self,
        drafts: list[DraftPost],
        strategy: Strategy,
        memory_context: str,
    ) -> list[DraftPost] | None:
        """Critique and improve high-confidence drafts using a second LLM pass."""
        parts = [
            "## Self-Critique Pass",
            "Critique these drafts honestly. What's weak? Rewrite them better.",
            "Adjust confidence based on past accuracy.",
            f"\nTone: {strategy.tone}",
            f"Guidelines: {strategy.content_guidelines}",
        ]

        parts.append("\n## Drafts to Critique")
        for i, draft in enumerate(drafts, 1):
            parts.append(f"\n### Draft {i}")
            parts.append(f"Platform: {draft.platform.value}")
            parts.append(f"Source idea: {draft.source_idea}")
            parts.append(f"Text: {draft.text}")
            parts.append(f"Original confidence: {draft.confidence}")
            parts.append(f"Reasoning: {draft.reasoning}")

        if memory_context:
            parts.append(f"\n## Calibration Data\n{memory_context}")

        parts.append(
            "\nReturn improved versions as JSON with the same DraftPosts schema. Be ruthlessly honest in your critique."
        )

        try:
            response = self.call_llm("\n".join(parts))
            improved = parse_json_response(response.text, DraftPosts)
            logger.info(f"[creator] Self-critique improved {len(improved.posts)} drafts")
            return improved.posts
        except Exception:
            logger.warning("[creator] Self-critique failed, using original drafts")
            return None
