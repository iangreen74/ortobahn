"""Content Serialization — multi-part narrative arcs across posts.

Manages "series" of related posts that form narrative arcs.
Examples: "Day N of running an autonomous company", "Building in public: Week 3".

Zero LLM calls — pure data management. LLM integration happens in
Strategist (proposes new series) and Creator (writes installments).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.serialization")


class SeriesManager:
    """Manage content series (multi-part narrative arcs)."""

    def __init__(self, db: Database):
        self.db = db

    def create_series(
        self,
        client_id: str,
        title: str,
        description: str = "",
        max_parts: int = 0,
    ) -> str:
        """Create a new content series. Returns series ID.

        max_parts=0 means open-ended (no predetermined end).
        """
        series_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        self.db.execute(
            """INSERT INTO content_series
               (id, client_id, series_title, series_description,
                current_part, max_parts, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, 'active', ?, ?)""",
            (series_id, client_id, title, description, max_parts, now, now),
            commit=True,
        )
        logger.info(f"Created series '{title}' (id={series_id}) for client {client_id}")
        return series_id

    def get_active_series(self, client_id: str) -> list[dict]:
        """Get all active series for a client."""
        rows = self.db.fetchall(
            """SELECT id, series_title, series_description, current_part,
                      max_parts, status, created_at
               FROM content_series
               WHERE client_id = ? AND status = 'active'
               ORDER BY updated_at DESC""",
            (client_id,),
        )
        return [dict(r) for r in rows]

    def get_series(self, series_id: str) -> dict | None:
        """Get a single series by ID."""
        row = self.db.fetchone(
            "SELECT * FROM content_series WHERE id = ?",
            (series_id,),
        )
        return dict(row) if row else None

    def get_series_posts(self, series_id: str, limit: int = 20) -> list[dict]:
        """Get posts in a series, ordered by part number."""
        rows = self.db.fetchall(
            """SELECT id, text, series_part, confidence, status, published_at
               FROM posts
               WHERE series_id = ?
               ORDER BY series_part ASC
               LIMIT ?""",
            (series_id, limit),
        )
        return [dict(r) for r in rows]

    def get_series_context(self, client_id: str, max_series: int = 3) -> str:
        """Build context string for LLM about active series.

        Returns formatted text describing active series and their recent installments
        for injection into Strategist/Creator prompts.
        """
        active = self.get_active_series(client_id)
        if not active:
            return ""

        lines = ["## Active Content Series"]
        for series in active[:max_series]:
            lines.append(f"\n### Series: {series['series_title']}")
            if series["series_description"]:
                lines.append(f"Description: {series['series_description']}")
            lines.append(f"Current part: {series['current_part']}")
            if series["max_parts"]:
                lines.append(f"Max parts: {series['max_parts']}")

            # Get last 3 installments for continuity
            recent = self.get_series_posts(series["id"], limit=3)
            if recent:
                lines.append("Recent installments:")
                for post in recent:
                    preview = (post["text"] or "")[:120]
                    lines.append(f"  Part {post['series_part']}: {preview}...")

        return "\n".join(lines)

    def advance_series(self, series_id: str, post_id: str) -> int:
        """Record a new installment in a series. Returns the new part number."""
        series = self.get_series(series_id)
        if not series:
            raise ValueError(f"Series {series_id} not found")

        if series["status"] != "active":
            raise ValueError(f"Series {series_id} is {series['status']}, not active")

        new_part = (series["current_part"] or 0) + 1
        now = datetime.now(timezone.utc).isoformat()

        # Update the post with series info
        self.db.execute(
            "UPDATE posts SET series_id = ?, series_part = ? WHERE id = ?",
            (series_id, new_part, post_id),
            commit=True,
        )

        # Update the series
        self.db.execute(
            "UPDATE content_series SET current_part = ?, updated_at = ? WHERE id = ?",
            (new_part, now, series_id),
            commit=True,
        )

        # Check if series is complete
        if series["max_parts"] and new_part >= series["max_parts"]:
            self.db.execute(
                "UPDATE content_series SET status = 'completed', updated_at = ? WHERE id = ?",
                (now, series_id),
                commit=True,
            )
            logger.info(f"Series {series_id} completed at part {new_part}")

        logger.info(f"Advanced series {series_id} to part {new_part}")
        return new_part

    def pause_series(self, series_id: str) -> None:
        """Pause an active series."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE content_series SET status = 'paused', updated_at = ? WHERE id = ?",
            (now, series_id),
            commit=True,
        )

    def resume_series(self, series_id: str) -> None:
        """Resume a paused series."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE content_series SET status = 'active', updated_at = ? WHERE id = ? AND status = 'paused'",
            (now, series_id),
            commit=True,
        )

    def suggest_new_series(self, client_id: str) -> bool:
        """Check if we should suggest a new series.

        Suggests if: no active series OR all active series are > 10 parts in.
        Returns True if a new series should be proposed.
        """
        active = self.get_active_series(client_id)
        if not active:
            return True
        # All series are well-established, room for a new one
        return all(s["current_part"] >= 10 for s in active)
