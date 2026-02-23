"""Article Writer Agent — generates long-form articles for cross-platform publishing."""

from __future__ import annotations

import logging

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.models import (
    ARTICLE_LENGTH_TARGETS,
    Client,
    DraftArticle,
    Strategy,
)

logger = logging.getLogger("ortobahn.agents")


class ArticleWriterAgent(BaseAgent):
    name = "article_writer"
    prompt_file = "article_writer.txt"
    thinking_budget = 16_000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_tokens = 8192

    def run(
        self,
        run_id: str,
        strategy: Strategy,
        client: Client | None = None,
        recent_articles: list[dict] | None = None,
        top_social_posts: list[dict] | None = None,
    ) -> DraftArticle:
        client_id = client.id if client else "default"

        # Determine article length target
        article_length = "medium"
        target_words = ARTICLE_LENGTH_TARGETS.get(article_length, 1500)

        # Build available topics
        article_topics_str = ""
        if client:
            # Get from client data (would come from DB)
            article_topics_str = getattr(client, "article_topics", "") or ""

        # Format system prompt with client context
        if client:
            system_prompt = self.format_prompt(
                client_name=client.name,
                article_voice=getattr(client, "article_voice", "") or client.brand_voice,
                client_target_audience=client.target_audience,
                client_products=client.products or "Not specified",
                article_length=f"{article_length} (~{target_words} words)",
                client_content_pillars=client.content_pillars or "Not specified",
                client_brand_voice=client.brand_voice,
            )
        else:
            system_prompt = self.format_prompt(
                client_name="Ortobahn",
                article_voice="authoritative but approachable",
                client_target_audience="tech-savvy professionals",
                client_products="AI marketing platform",
                article_length=f"{article_length} (~{target_words} words)",
                client_content_pillars="AI, marketing automation, content strategy",
                client_brand_voice="authoritative but approachable",
            )

        # Build user message
        parts = [f"## Strategy Context\nThemes: {', '.join(strategy.themes)}"]
        parts.append(f"Tone: {strategy.tone}")
        parts.append(f"Guidelines: {strategy.content_guidelines}")
        parts.append(f"\nTarget word count: ~{target_words} words")

        # Available topics
        if article_topics_str:
            parts.append(f"\n## Preferred Topics\n{article_topics_str}")

        # Recently covered topics (avoid duplication)
        if recent_articles:
            covered = [a.get("topic_used", "") for a in recent_articles if a.get("topic_used")]
            if covered:
                parts.append("\n## Recently Covered (avoid these)\n- " + "\n- ".join(covered[:10]))

        # Top-performing social posts (can expand into articles)
        if top_social_posts:
            parts.append("\n## Top Social Posts (consider expanding)")
            for i, sp in enumerate(top_social_posts[:5], 1):
                engagement = sp.get("like_count", 0) + sp.get("repost_count", 0) + sp.get("reply_count", 0)
                parts.append(f"{i}. [{engagement} engagements] {sp.get('text', '')[:200]}")

        # Inject memory context
        memory_context = self.get_memory_context(client_id)
        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)
        article = parse_json_response(response.text, DraftArticle)

        # Ensure word count is set
        if not article.word_count:
            article.word_count = len(article.body_markdown.split())

        self.log_decision(
            run_id=run_id,
            input_summary=f"Strategy themes: {strategy.themes}, target: {target_words}w",
            output_summary=f"Article: '{article.title}' ({article.word_count}w, confidence={article.confidence:.2f})",
            reasoning=f"Topic: {article.topic_used}",
            llm_response=response,
        )
        return article
