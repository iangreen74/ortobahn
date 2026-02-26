"""Content Repurposer — turn posts into articles and articles into social series.

Not a full BaseAgent subclass. Orchestrates existing ArticleWriterAgent
and CreatorAgent to repurpose content. Tracks provenance via source_post_id
and source_article_id columns.
"""

from __future__ import annotations

import logging
import uuid

from ortobahn.db import Database

logger = logging.getLogger(__name__)


class Repurposer:
    """Repurpose content between formats (posts <-> articles)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def post_to_article(
        self,
        post_id: str,
        client_id: str,
        run_id: str = "",
    ) -> str | None:
        """Expand a social post into a long-form article.

        Returns the article_id if successful, None otherwise.
        Uses the post text as the seed topic for article generation.
        """
        post = self.db.fetchone(
            "SELECT id, text, platform, confidence FROM posts WHERE id=? AND client_id=?",
            (post_id, client_id),
        )
        if not post:
            logger.warning("Repurposer: post %s not found for client %s", post_id, client_id)
            return None

        post_text = post.get("text") or ""
        if not post_text:
            return None

        # Create an article seeded from this post
        article_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO articles
                (id, client_id, run_id, title, subtitle, body_markdown, tags, status,
                 word_count, confidence, source_post_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 0, ?, ?, CURRENT_TIMESTAMP)""",
            (
                article_id,
                client_id,
                run_id or f"repurpose-{post_id[:8]}",
                f"Expanded: {post_text[:80]}...",
                "Generated from high-performing social post",
                f"<!-- Seeded from post: {post_id} -->\n\n{post_text}\n\n"
                "<!-- This article needs LLM expansion. Run article_writer agent to complete. -->",
                "[]",
                post.get("confidence", 0.7),
                post_id,
            ),
            commit=True,
        )

        logger.info("Repurposer: created article %s from post %s", article_id, post_id)
        return article_id

    def article_to_series(
        self,
        article_id: str,
        client_id: str,
        platform: str = "bluesky",
        num_posts: int = 3,
        run_id: str = "",
    ) -> list[str]:
        """Break an article into a social post series.

        Returns a list of post IDs (drafts).
        """
        article = self.db.fetchone(
            "SELECT id, title, body_markdown, tags FROM articles WHERE id=? AND client_id=?",
            (article_id, client_id),
        )
        if not article:
            logger.warning("Repurposer: article %s not found for client %s", article_id, client_id)
            return []

        body = article.get("body_markdown") or ""
        title = article.get("title") or ""
        if not body:
            return []

        # Create a series from the article
        series_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO content_series (id, client_id, series_title, status, max_parts, created_at)"
            " VALUES (?, ?, ?, 'active', ?, CURRENT_TIMESTAMP)",
            (series_id, client_id, f"Series: {title[:80]}", num_posts),
            commit=True,
        )

        # Split article into chunks for posts
        post_ids = []
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("<!--")]
        chunk_size = max(1, len(paragraphs) // num_posts)

        for i in range(num_posts):
            start = i * chunk_size
            end = start + chunk_size if i < num_posts - 1 else len(paragraphs)
            chunk = "\n\n".join(paragraphs[start:end])

            if not chunk:
                chunk = f"Part {i + 1} of series: {title}"

            # Truncate to platform limits
            if platform == "bluesky":
                chunk = chunk[:280]
            elif platform == "twitter":
                chunk = chunk[:250]
            else:
                chunk = chunk[:500]

            pid = self.db.save_post(
                text=chunk,
                run_id=run_id or f"repurpose-{article_id[:8]}",
                status="draft",
                confidence=0.7,
                client_id=client_id,
                platform=platform,
                series_id=series_id,
            )

            # Set provenance
            self.db.execute(
                "UPDATE posts SET source_article_id=?, repurpose_type='article_to_series' WHERE id=?",
                (article_id, pid),
                commit=True,
            )
            post_ids.append(pid)

        logger.info(
            "Repurposer: created %d posts from article %s (series %s)",
            len(post_ids),
            article_id,
            series_id,
        )
        return post_ids

    def get_repurpose_candidates(self, client_id: str, min_engagement: float = 2.0, limit: int = 5) -> list[dict]:
        """Find high-performing posts that haven't been repurposed yet."""
        avg_row = self.db.fetchone(
            "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p"
            " LEFT JOIN metrics m ON p.id = m.post_id"
            " AND m.id = (SELECT m2.id FROM metrics m2"
            " WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL",
            (client_id,),
        )
        avg = float(avg_row["avg_eng"] or 0) if avg_row else 0
        if avg <= 0:
            return []

        threshold = avg * min_engagement

        rows = self.db.fetchall(
            "SELECT p.id, p.text, p.platform,"
            " COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0) as total_eng"
            " FROM posts p"
            " LEFT JOIN metrics m ON p.id = m.post_id"
            " AND m.id = (SELECT m2.id FROM metrics m2"
            " WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL"
            " AND p.source_post_id IS NULL"
            " AND p.id NOT IN (SELECT COALESCE(source_post_id,'') FROM articles WHERE client_id=? AND source_post_id IS NOT NULL)"
            " AND (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) >= ?"
            " ORDER BY total_eng DESC LIMIT ?",
            (client_id, client_id, threshold, limit),
        )
        return [dict(r) for r in rows]
