"""Analytics queries — engagement metrics, performance summaries, trend data."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ortobahn.models import AnalyticsReport, PostPerformance


class AnalyticsMixin:
    """Mixed into Database to provide analytics methods."""

    def build_analytics_report(self, client_id: str | None = None) -> AnalyticsReport:
        posts = self.get_recent_published_posts(days=7, client_id=client_id)
        if not posts:
            return AnalyticsReport()

        total_likes = 0
        total_reposts = 0
        total_replies = 0
        best = None
        worst = None

        for p in posts:
            row = self.fetchone(
                """SELECT COALESCE(like_count,0) as likes,
                          COALESCE(repost_count,0) as reposts,
                          COALESCE(reply_count,0) as replies
                   FROM metrics WHERE post_id=?
                   ORDER BY measured_at DESC LIMIT 1""",
                (p["id"],),
            )
            likes = row["likes"] if row else 0
            reposts = row["reposts"] if row else 0
            replies = row["replies"] if row else 0
            engagement = likes + reposts + replies
            total_likes += likes
            total_reposts += reposts
            total_replies += replies

            perf = PostPerformance(
                text=p["text"],
                uri=p.get("bluesky_uri") or "",
                like_count=likes,
                repost_count=reposts,
                reply_count=replies,
                total_engagement=engagement,
            )
            if best is None or engagement > best.total_engagement:
                best = perf
            if worst is None or engagement < worst.total_engagement:
                worst = perf

        total = len(posts)
        total_eng = total_likes + total_reposts + total_replies
        return AnalyticsReport(
            period="last 7 days",
            total_posts=total,
            total_likes=total_likes,
            total_reposts=total_reposts,
            total_replies=total_replies,
            avg_engagement_per_post=round(total_eng / total, 2) if total else 0.0,
            best_post=best,
            worst_post=worst,
        )

    def get_current_month_spend(self, client_id: str) -> float:
        """Calculate total API cost for a client in the current calendar month."""
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = self.fetchone(
            """SELECT COALESCE(SUM(total_input_tokens), 0) as input_tok,
                      COALESCE(SUM(total_output_tokens), 0) as output_tok,
                      COALESCE(SUM(total_cache_creation_tokens), 0) as cache_create,
                      COALESCE(SUM(total_cache_read_tokens), 0) as cache_read
               FROM pipeline_runs
               WHERE client_id=? AND started_at >= ?""",
            (client_id, month_start),
        )
        if not row:
            return 0.0
        # Sonnet pricing: $3/M input, $3.75/M cache write, $0.30/M cache read, $15/M output
        uncached = max(0, row["input_tok"] - row["cache_create"] - row["cache_read"])
        input_cost = uncached / 1_000_000 * 3
        cache_write_cost = row["cache_create"] / 1_000_000 * 3.75
        cache_read_cost = row["cache_read"] / 1_000_000 * 0.30
        output_cost = row["output_tok"] / 1_000_000 * 15
        return input_cost + cache_write_cost + cache_read_cost + output_cost

    def get_public_stats(self) -> dict:
        clients = self.fetchone("SELECT COUNT(*) as c FROM clients WHERE active=1")
        posts = self.fetchone("SELECT COUNT(*) as c FROM posts WHERE status='published'")
        platforms = self.fetchone("SELECT COUNT(DISTINCT platform) as c FROM posts WHERE status='published'")
        return {
            "total_clients": clients["c"] if clients else 0,
            "total_posts_published": posts["c"] if posts else 0,
            "platforms_supported": platforms["c"] if platforms else 0,
        }

    # --- Strategies (with caching) ---

    def save_strategy(
        self, strategy_data: dict, run_id: str, raw_response: str = "", client_id: str = "default"
    ) -> str:
        sid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO strategies (id, themes, tone, goals, content_guidelines,
               posting_frequency, valid_until, run_id, raw_llm_response, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                json.dumps(strategy_data["themes"]),
                strategy_data["tone"],
                json.dumps(strategy_data["goals"]),
                strategy_data["content_guidelines"],
                strategy_data["posting_frequency"],
                strategy_data["valid_until"],
                run_id,
                raw_response,
                client_id,
            ),
            commit=True,
        )
        # Invalidate strategy cache for this client
        self._cache_invalidate(f"strategy:{client_id}")
        return sid

    def get_active_strategy(self, client_id: str = "default") -> dict | None:
        cache_key = f"strategy:{client_id}"
        cached = self._cache_get(cache_key, 600.0)  # cache for 10 minutes (pipeline cycle duration)
        if cached is not None:
            return cached

        row = self.fetchone(
            "SELECT * FROM strategies WHERE valid_until > ? AND client_id = ? ORDER BY created_at DESC LIMIT 1",
            (datetime.now(timezone.utc).isoformat(), client_id),
        )
        if not row:
            return None
        result = {
            "id": row["id"],
            "themes": json.loads(row["themes"]),
            "tone": row["tone"],
            "goals": json.loads(row["goals"]),
            "content_guidelines": row["content_guidelines"],
            "posting_frequency": row["posting_frequency"],
            "valid_until": row["valid_until"],
            "client_id": row["client_id"],
        }
        self._cache_set(cache_key, result)
        return result
