"""Post CRUD operations — create, read, update, approval, filtering."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


class PostsMixin:
    """Mixed into Database to provide post-related methods."""

    # --- Posts ---

    def save_post(
        self,
        text: str,
        run_id: str,
        strategy_id: str | None = None,
        source_idea: str = "",
        reasoning: str = "",
        confidence: float = 0.0,
        status: str = "draft",
        client_id: str = "default",
        platform: str = "generic",
        content_type: str = "social_post",
        ab_group: str | None = None,
        series_id: str | None = None,
    ) -> str:
        pid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO posts (id, text, source_idea, reasoning, confidence, status,
               run_id, strategy_id, client_id, platform, content_type,
               ab_group, series_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                text,
                source_idea,
                reasoning,
                confidence,
                status,
                run_id,
                strategy_id,
                client_id,
                platform,
                content_type,
                ab_group,
                series_id,
            ),
            commit=True,
        )
        return pid

    def update_post_published(self, post_id: str, uri: str, cid: str):
        self.execute(
            """UPDATE posts SET status='published', platform_uri=?, platform_id=?,
               bluesky_uri=?, bluesky_cid=?, published_at=? WHERE id=?""",
            (uri, cid, uri, cid, datetime.now(timezone.utc).isoformat(), post_id),
            commit=True,
        )

    def update_post_failed(self, post_id: str, error: str):
        self.execute(
            "UPDATE posts SET status='failed', error_message=? WHERE id=?",
            (error, post_id),
            commit=True,
        )

    def update_post_failed_with_category(self, post_id: str, error: str, failure_category: str):
        self.execute(
            "UPDATE posts SET status='failed', error_message=?, failure_category=? WHERE id=?",
            (error, failure_category, post_id),
            commit=True,
        )

    def get_recent_published_posts(self, days: int = 7, client_id: str | None = None) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = "SELECT * FROM posts WHERE status='published' AND published_at > ?"
        params: list = [cutoff]
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY published_at DESC"
        return self.fetchall(query, params)

    def get_recent_posts_with_metrics(
        self, limit: int = 20, client_id: str | None = None, offset: int = 0
    ) -> list[dict]:
        query = """SELECT p.*,
                   COALESCE(latest_m.like_count, 0) AS like_count,
                   COALESCE(latest_m.repost_count, 0) AS repost_count,
                   COALESCE(latest_m.reply_count, 0) AS reply_count,
                   COALESCE(latest_m.quote_count, 0) AS quote_count
               FROM posts p
               LEFT JOIN metrics latest_m ON p.id = latest_m.post_id
                   AND latest_m.id = (
                       SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1
                   )
               WHERE p.status IN ('published', 'failed')"""
        params: list = []
        if client_id:
            query += " AND p.client_id=?"
            params.append(client_id)
        query += " ORDER BY COALESCE(p.published_at, p.created_at) DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.fetchall(query, params)

    # --- Content Approval ---

    def get_drafts_for_review(self, client_id: str | None = None, platform: str | None = None) -> list[dict]:
        query = "SELECT * FROM posts WHERE status='draft'"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        if platform:
            query += " AND platform=?"
            params.append(platform)
        query += " ORDER BY created_at DESC"
        return self.fetchall(query, params)

    def get_post(self, post_id: str) -> dict | None:
        return self.fetchone("SELECT * FROM posts WHERE id=?", (post_id,))

    def approve_post(self, post_id: str) -> None:
        self.execute("UPDATE posts SET status='approved' WHERE id=?", (post_id,), commit=True)

    def reject_post(self, post_id: str) -> None:
        self.execute("UPDATE posts SET status='rejected' WHERE id=?", (post_id,), commit=True)

    def update_post_text(self, post_id: str, new_text: str) -> None:
        self.execute(
            "UPDATE posts SET text=? WHERE id=? AND status IN ('draft', 'rejected')",
            (new_text, post_id),
            commit=True,
        )

    def get_approved_posts(self, client_id: str | None = None) -> list[dict]:
        """Get posts in 'approved' status ready for publishing."""
        query = "SELECT * FROM posts WHERE status='approved'"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY created_at ASC"
        return self.fetchall(query, params)

    def get_all_posts(
        self,
        client_id: str | None = None,
        status: str | None = None,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        query = "SELECT * FROM posts WHERE 1=1"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        if status:
            query += " AND status=?"
            params.append(status)
        if platform:
            query += " AND platform=?"
            params.append(platform)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.fetchall(query, params)

    def get_recent_posts_by_status(self, hours: int = 24, status: str = "published") -> list[dict]:
        """Get posts with a given status from the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return self.fetchall(
            "SELECT * FROM posts WHERE status=? AND created_at > ? ORDER BY created_at DESC",
            (status, cutoff),
        )

    def get_post_failure_rate(self, hours: int = 24, client_id: str | None = None) -> tuple[int, int]:
        """Return (failed_count, total_count) for posts in the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        base = "FROM posts WHERE created_at > ? AND status IN ('published', 'failed')"
        params: list = [cutoff]
        if client_id:
            base += " AND client_id=?"
            params.append(client_id)
        total_row = self.fetchone(f"SELECT COUNT(*) as cnt {base}", params)
        failed_row = self.fetchone(f"SELECT COUNT(*) as cnt {base} AND status='failed'", params)
        total = total_row["cnt"] if total_row else 0
        failed = failed_row["cnt"] if failed_row else 0
        return failed, total

    def count_posts(self, client_id: str | None = None, status: str | None = None, platform: str | None = None) -> int:
        """Count posts matching filters."""
        query = "SELECT COUNT(*) as cnt FROM posts WHERE 1=1"
        params: list = []
        if client_id:
            query += " AND client_id = ?"
            params.append(client_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        if platform:
            query += " AND platform = ?"
            params.append(platform)
        row = self.fetchone(query, params)
        return row["cnt"] if row else 0

    # --- Metrics ---

    def save_metrics(
        self, post_id: str, like_count: int = 0, repost_count: int = 0, reply_count: int = 0, quote_count: int = 0
    ) -> str:
        # Upsert: update existing metrics row or insert new one
        existing = self.fetchone("SELECT id FROM metrics WHERE post_id=?", (post_id,))
        if existing:
            self.execute(
                """UPDATE metrics SET like_count=?, repost_count=?, reply_count=?, quote_count=?,
                   measured_at=CURRENT_TIMESTAMP WHERE post_id=?""",
                (like_count, repost_count, reply_count, quote_count, post_id),
                commit=True,
            )
            return existing["id"]
        mid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, post_id, like_count, repost_count, reply_count, quote_count),
            commit=True,
        )
        return mid
