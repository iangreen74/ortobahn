"""Database setup and operations — supports PostgreSQL and SQLite backends."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ortobahn.models import AnalyticsReport, PostPerformance

logger = logging.getLogger("ortobahn.db")


class Database:
    def __init__(self, db_path: Path | None = None, database_url: str = ""):
        if database_url:
            import psycopg2
            import psycopg2.pool

            self.backend = "postgresql"
            self._pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=10, dsn=database_url)
            self._sqlite_conn = None
        else:
            self.backend = "sqlite"
            self._pool = None
            db_path = db_path or Path("data/ortobahn.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._sqlite_conn.row_factory = sqlite3.Row

        self._create_tables()
        self._run_migrations()

    # ------------------------------------------------------------------
    # Low-level DB helpers (backend-agnostic)
    # ------------------------------------------------------------------

    def _convert_query(self, query: str) -> str:
        """Convert ? placeholders to %s for PostgreSQL."""
        if self.backend == "postgresql":
            return query.replace("?", "%s")
        return query

    def execute(self, query: str, params: tuple | list = (), *, commit: bool = False) -> Any:
        """Execute a query. Returns the cursor (sqlite) or None (pg)."""
        query = self._convert_query(query)
        if self.backend == "postgresql":
            conn = self._pool.getconn()  # type: ignore[union-attr]
            try:
                with conn.cursor() as cur:
                    cur.execute(query, tuple(params))
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)  # type: ignore[union-attr]
            return None
        else:
            result = self._sqlite_conn.execute(query, params)  # type: ignore[union-attr]
            if commit:
                self._sqlite_conn.commit()  # type: ignore[union-attr]
            return result

    def fetchone(self, query: str, params: tuple | list = ()) -> dict | None:
        """Execute and return one row as dict, or None."""
        query = self._convert_query(query)
        if self.backend == "postgresql":
            import psycopg2.extras

            conn = self._pool.getconn()  # type: ignore[union-attr]
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, tuple(params))
                    row = cur.fetchone()
                    return dict(row) if row else None
            finally:
                self._pool.putconn(conn)  # type: ignore[union-attr]
        else:
            row = self._sqlite_conn.execute(query, params).fetchone()  # type: ignore[union-attr]
            return dict(row) if row else None

    def fetchall(self, query: str, params: tuple | list = ()) -> list[dict]:
        """Execute and return all rows as list of dicts."""
        query = self._convert_query(query)
        if self.backend == "postgresql":
            import psycopg2.extras

            conn = self._pool.getconn()  # type: ignore[union-attr]
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, tuple(params))
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
            finally:
                self._pool.putconn(conn)  # type: ignore[union-attr]
        else:
            rows = self._sqlite_conn.execute(query, params).fetchall()  # type: ignore[union-attr]
            return [dict(r) for r in rows]

    def commit(self) -> None:
        """Explicit commit (mainly for SQLite; PG commits per-execute when commit=True)."""
        if self.backend == "sqlite" and self._sqlite_conn:
            self._sqlite_conn.commit()

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _create_tables(self):
        self.execute(
            """CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                themes TEXT NOT NULL,
                tone TEXT NOT NULL,
                goals TEXT NOT NULL,
                content_guidelines TEXT NOT NULL,
                posting_frequency TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                valid_until TIMESTAMP NOT NULL,
                run_id TEXT NOT NULL,
                raw_llm_response TEXT
            )"""
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                source_idea TEXT,
                reasoning TEXT,
                confidence REAL,
                status TEXT NOT NULL,
                bluesky_uri TEXT,
                bluesky_cid TEXT,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id TEXT NOT NULL,
                strategy_id TEXT REFERENCES strategies(id)
            )"""
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id),
                like_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS agent_logs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                reasoning TEXT,
                llm_model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                duration_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                raw_llm_response TEXT
            )"""
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT NOT NULL,
                posts_published INTEGER DEFAULT 0,
                errors TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0
            )"""
        )
        self.commit()

    def _run_migrations(self):
        from ortobahn.migrations import run_migrations

        run_migrations(self)

    # --- Clients ---

    def create_client(self, client_data: dict) -> str:
        cid = client_data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO clients (id, name, description, industry, target_audience, brand_voice,
               website, email, status, products, competitive_positioning, key_messages,
               content_pillars, company_story)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                client_data["name"],
                client_data.get("description", ""),
                client_data.get("industry", ""),
                client_data.get("target_audience", ""),
                client_data.get("brand_voice", ""),
                client_data.get("website", ""),
                client_data.get("email", ""),
                client_data.get("status", "active"),
                client_data.get("products", ""),
                client_data.get("competitive_positioning", ""),
                client_data.get("key_messages", ""),
                client_data.get("content_pillars", ""),
                client_data.get("company_story", ""),
            ),
            commit=True,
        )
        return cid

    def get_client(self, client_id: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE id=?", (client_id,))

    def get_client_by_email(self, email: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE email=?", (email,))

    def get_client_by_cognito_sub(self, cognito_sub: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE cognito_sub=?", (cognito_sub,))

    def get_all_clients(self) -> list[dict]:
        return self.fetchall("SELECT * FROM clients WHERE active=1 ORDER BY name")

    def update_client(self, client_id: str, data: dict) -> None:
        allowed = {
            "name",
            "description",
            "industry",
            "target_audience",
            "brand_voice",
            "website",
            "active",
            "status",
            "products",
            "competitive_positioning",
            "key_messages",
            "content_pillars",
            "company_story",
            "monthly_budget",
            "internal",
            "subscription_status",
            "subscription_plan",
            "cognito_sub",
            "news_category",
            "news_keywords",
            "rss_feeds",
            "posting_interval_hours",
            "timezone",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [client_id]
        self.execute(f"UPDATE clients SET {set_clause} WHERE id=?", values, commit=True)

    # --- Strategies ---

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
        return sid

    def get_active_strategy(self, client_id: str = "default") -> dict | None:
        row = self.fetchone(
            "SELECT * FROM strategies WHERE valid_until > ? AND client_id = ? ORDER BY created_at DESC LIMIT 1",
            (datetime.now(timezone.utc).isoformat(), client_id),
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "themes": json.loads(row["themes"]),
            "tone": row["tone"],
            "goals": json.loads(row["goals"]),
            "content_guidelines": row["content_guidelines"],
            "posting_frequency": row["posting_frequency"],
            "valid_until": row["valid_until"],
            "client_id": row["client_id"],
        }

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
    ) -> str:
        pid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO posts (id, text, source_idea, reasoning, confidence, status,
               run_id, strategy_id, client_id, platform, content_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        self.execute("UPDATE posts SET status='failed' WHERE id=?", (post_id,), commit=True)

    def get_recent_published_posts(self, days: int = 7, client_id: str | None = None) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = "SELECT * FROM posts WHERE status='published' AND published_at > ?"
        params: list = [cutoff]
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY published_at DESC"
        return self.fetchall(query, params)

    def get_recent_posts_with_metrics(self, limit: int = 20, client_id: str | None = None) -> list[dict]:
        query = """SELECT p.*, m.like_count, m.repost_count, m.reply_count, m.quote_count
               FROM posts p LEFT JOIN metrics m ON p.id = m.post_id
               WHERE p.status = 'published'"""
        params: list = []
        if client_id:
            query += " AND p.client_id=?"
            params.append(client_id)
        query += " ORDER BY p.published_at DESC LIMIT ?"
        params.append(limit)
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
        self, client_id: str | None = None, status: str | None = None, platform: str | None = None, limit: int = 50
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
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.fetchall(query, params)

    # --- Metrics ---

    def save_metrics(
        self, post_id: str, like_count: int = 0, repost_count: int = 0, reply_count: int = 0, quote_count: int = 0
    ) -> str:
        mid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, post_id, like_count, repost_count, reply_count, quote_count),
            commit=True,
        )
        return mid

    # --- Agent Logs ---

    def log_agent(
        self,
        run_id: str,
        agent_name: str,
        input_summary: str = "",
        output_summary: str = "",
        reasoning: str = "",
        llm_model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_seconds: float = 0.0,
        raw_response: str = "",
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> str:
        lid = str(uuid.uuid4())
        self.execute(
            """INSERT INTO agent_logs (id, run_id, agent_name, input_summary, output_summary,
               reasoning, llm_model, input_tokens, output_tokens, duration_seconds, raw_llm_response,
               cache_creation_input_tokens, cache_read_input_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lid,
                run_id,
                agent_name,
                input_summary,
                output_summary,
                reasoning,
                llm_model,
                input_tokens,
                output_tokens,
                duration_seconds,
                raw_response,
                cache_creation_input_tokens,
                cache_read_input_tokens,
            ),
            commit=True,
        )
        return lid

    def get_recent_agent_logs(self, limit: int = 20) -> list[dict]:
        return self.fetchall("SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT ?", (limit,))

    # --- Pipeline Runs ---

    def start_pipeline_run(self, run_id: str, mode: str = "single", client_id: str = "default"):
        self.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', ?)",
            (run_id, mode, datetime.now(timezone.utc).isoformat(), client_id),
            commit=True,
        )

    def complete_pipeline_run(
        self,
        run_id: str,
        posts_published: int = 0,
        errors: list[str] | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_cache_creation_tokens: int = 0,
        total_cache_read_tokens: int = 0,
    ):
        self.execute(
            """UPDATE pipeline_runs SET completed_at=?, status='completed',
               posts_published=?, errors=?, total_input_tokens=?, total_output_tokens=?,
               total_cache_creation_tokens=?, total_cache_read_tokens=?
               WHERE id=?""",
            (
                datetime.now(timezone.utc).isoformat(),
                posts_published,
                json.dumps(errors or []),
                total_input_tokens,
                total_output_tokens,
                total_cache_creation_tokens,
                total_cache_read_tokens,
                run_id,
            ),
            commit=True,
        )

    def fail_pipeline_run(self, run_id: str, errors: list[str]):
        self.execute(
            "UPDATE pipeline_runs SET completed_at=?, status='failed', errors=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), json.dumps(errors), run_id),
            commit=True,
        )

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        return self.fetchall("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,))

    def get_last_run_time(self, client_id: str) -> str | None:
        """Get the started_at timestamp of the most recent pipeline run for a client."""
        row = self.fetchone(
            "SELECT started_at FROM pipeline_runs WHERE client_id=? ORDER BY started_at DESC LIMIT 1",
            (client_id,),
        )
        return row["started_at"] if row else None

    # --- Analytics helpers ---

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
                """SELECT COALESCE(SUM(like_count),0) as likes,
                          COALESCE(SUM(repost_count),0) as reposts,
                          COALESCE(SUM(reply_count),0) as replies
                   FROM metrics WHERE post_id=?""",
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

    def pause_client(self, client_id: str) -> None:
        """Set client status to paused (budget exceeded)."""
        self.execute("UPDATE clients SET status='paused' WHERE id=?", (client_id,), commit=True)

    def get_public_stats(self) -> dict:
        clients = self.fetchone("SELECT COUNT(*) as c FROM clients WHERE active=1")
        posts = self.fetchone("SELECT COUNT(*) as c FROM posts WHERE status='published'")
        platforms = self.fetchone(
            "SELECT COUNT(DISTINCT platform) as c FROM posts WHERE status='published'"
        )
        return {
            "total_clients": clients["c"] if clients else 0,
            "total_posts_published": posts["c"] if posts else 0,
            "platforms_supported": platforms["c"] if platforms else 0,
        }

    # --- API Keys ---

    def create_api_key(self, client_id: str, key_hash: str, key_prefix: str, name: str = "default") -> str:
        kid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO api_keys (id, client_id, key_hash, key_prefix, name) VALUES (?, ?, ?, ?, ?)",
            (kid, client_id, key_hash, key_prefix, name),
            commit=True,
        )
        return kid

    def get_api_keys_for_client(self, client_id: str) -> list[dict]:
        return self.fetchall(
            "SELECT id, key_prefix, name, created_at, last_used_at, active FROM api_keys WHERE client_id=?",
            (client_id,),
        )

    def revoke_api_key(self, key_id: str) -> None:
        self.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,), commit=True)

    # --- Subscriptions ---

    def update_subscription(
        self,
        client_id: str,
        stripe_customer_id: str = "",
        stripe_subscription_id: str = "",
        subscription_status: str = "none",
        subscription_plan: str = "",
    ) -> None:
        self.execute(
            """UPDATE clients SET stripe_customer_id=?, stripe_subscription_id=?,
               subscription_status=?, subscription_plan=? WHERE id=?""",
            (stripe_customer_id, stripe_subscription_id, subscription_status, subscription_plan, client_id),
            commit=True,
        )

    def get_client_by_stripe_customer(self, stripe_customer_id: str) -> dict | None:
        return self.fetchone("SELECT * FROM clients WHERE stripe_customer_id=?", (stripe_customer_id,))

    def record_stripe_event(self, event_id: str, event_type: str) -> bool:
        """Record a Stripe event. Returns False if already processed."""
        existing = self.fetchone("SELECT id FROM stripe_events WHERE id=?", (event_id,))
        if existing:
            return False
        self.execute(
            "INSERT INTO stripe_events (id, event_type) VALUES (?, ?)",
            (event_id, event_type),
            commit=True,
        )
        return True

    def check_and_expire_trial(self, client_id: str) -> str:
        """If client is trialing and trial has ended, flip to 'expired'. Returns current status."""
        row = self.fetchone(
            "SELECT subscription_status, trial_ends_at FROM clients WHERE id=?",
            (client_id,),
        )
        if not row:
            return "none"
        status = row["subscription_status"]
        if status == "trialing" and row["trial_ends_at"]:
            try:
                trial_end = datetime.fromisoformat(row["trial_ends_at"])
                if trial_end.tzinfo is None:
                    trial_end = trial_end.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return status
            if datetime.now(timezone.utc) > trial_end:
                self.execute(
                    "UPDATE clients SET subscription_status='expired' WHERE id=?",
                    (client_id,),
                    commit=True,
                )
                return "expired"
        return status

    # --- Engineering Tasks (CTO Agent) ---

    def create_engineering_task(self, data: dict) -> str:
        tid = data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO engineering_tasks (id, title, description, priority, status,
               category, estimated_complexity, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid,
                data["title"],
                data["description"],
                data.get("priority", 3),
                data.get("status", "backlog"),
                data.get("category", "feature"),
                data.get("estimated_complexity", "medium"),
                data.get("created_by", "human"),
            ),
            commit=True,
        )
        return tid

    def get_next_engineering_task(self) -> dict | None:
        return self.fetchone(
            "SELECT * FROM engineering_tasks WHERE status='backlog' ORDER BY priority ASC, created_at ASC LIMIT 1"
        )

    def get_engineering_tasks(self, status: str | None = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM engineering_tasks"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)
        return self.fetchall(query, params)

    def update_engineering_task(self, task_id: str, data: dict) -> None:
        allowed = {
            "title",
            "description",
            "priority",
            "status",
            "category",
            "started_at",
            "completed_at",
            "assigned_run_id",
            "branch_name",
            "files_changed",
            "error",
            "blocked_reason",
            "estimated_complexity",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [task_id]
        self.execute(f"UPDATE engineering_tasks SET {set_clause} WHERE id=?", values, commit=True)

    def log_code_change(
        self, task_id: str, run_id: str, file_path: str, change_type: str, diff_summary: str = ""
    ) -> str:
        cid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO code_changes (id, task_id, run_id, file_path, change_type, diff_summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, task_id, run_id, file_path, change_type, diff_summary),
            commit=True,
        )
        return cid

    def start_cto_run(self, run_id: str, task_id: str) -> None:
        self.execute(
            "INSERT INTO cto_runs (id, task_id, status) VALUES (?, ?, 'running')",
            (run_id, task_id),
            commit=True,
        )

    def complete_cto_run(self, run_id: str, status: str, **kwargs) -> None:
        fields = ["status=?", "completed_at=CURRENT_TIMESTAMP"]
        values: list = [status]
        for key in (
            "thinking_summary",
            "files_read",
            "files_written",
            "tests_passed",
            "tests_failed",
            "commit_sha",
            "error",
            "total_input_tokens",
            "total_output_tokens",
        ):
            if key in kwargs:
                fields.append(f"{key}=?")
                val = kwargs[key]
                values.append(json.dumps(val) if isinstance(val, (list, dict)) else val)
        values.append(run_id)
        self.execute(f"UPDATE cto_runs SET {', '.join(fields)} WHERE id=?", values, commit=True)

    # --- CI Fix Tracking ---

    def log_ci_fix_attempt(self, data: dict) -> str:
        fid = data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO ci_fix_attempts
            (id, run_id, gh_run_id, gh_run_url, job_name, failure_category,
             error_count, error_codes, fix_strategy, status, files_changed,
             branch_name, commit_sha, pr_url, llm_used, input_tokens,
             output_tokens, validation_passed, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                data["run_id"],
                data.get("gh_run_id"),
                data.get("gh_run_url"),
                data.get("job_name", ""),
                data.get("failure_category", "unknown"),
                data.get("error_count", 0),
                json.dumps(data.get("error_codes", [])),
                data.get("fix_strategy", ""),
                data.get("status", "pending"),
                json.dumps(data.get("files_changed", [])),
                data.get("branch_name"),
                data.get("commit_sha"),
                data.get("pr_url"),
                1 if data.get("llm_used") else 0,
                data.get("input_tokens", 0),
                data.get("output_tokens", 0),
                1 if data.get("validation_passed") else 0,
                data.get("error_message"),
            ),
            commit=True,
        )
        return fid

    def get_ci_fix_history(self, category: str | None = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM ci_fix_attempts"
        params: list = []
        if category:
            query += " WHERE failure_category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.fetchall(query, params)

    def get_ci_fix_success_rate(self, category: str | None = None) -> float:
        query = "SELECT COUNT(*) as total, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes FROM ci_fix_attempts"
        params: list = []
        if category:
            query += " WHERE failure_category = ?"
            params.append(category)
        row = self.fetchone(query, params)
        if not row:
            return 0.0
        total = row["total"]
        return (row["successes"] or 0) / total if total > 0 else 0.0

    def close(self):
        if self.backend == "postgresql" and self._pool:
            self._pool.closeall()
        elif self._sqlite_conn:
            self._sqlite_conn.close()


def create_database(settings) -> Database:
    """Create a Database instance from settings."""
    return Database(
        db_path=settings.db_path if not settings.database_url else None,
        database_url=settings.database_url,
    )
