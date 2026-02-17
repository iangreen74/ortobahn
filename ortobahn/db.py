"""SQLite database setup and operations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from ortobahn.models import AnalyticsReport, PostPerformance


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._run_migrations()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
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
            );

            CREATE TABLE IF NOT EXISTS posts (
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
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id),
                like_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_logs (
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
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT NOT NULL,
                posts_published INTEGER DEFAULT 0,
                errors TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0
            );
        """)
        self.conn.commit()

    def _run_migrations(self):
        from ortobahn.migrations import run_migrations

        run_migrations(self.conn)

    # --- Clients ---

    def create_client(self, client_data: dict) -> str:
        cid = client_data.get("id") or str(uuid.uuid4())
        self.conn.execute(
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
        )
        self.conn.commit()
        return cid

    def get_client(self, client_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        return dict(row) if row else None

    def get_client_by_email(self, email: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM clients WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def get_client_by_cognito_sub(self, cognito_sub: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM clients WHERE cognito_sub=?", (cognito_sub,)).fetchone()
        return dict(row) if row else None

    def get_all_clients(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM clients WHERE active=1 ORDER BY name").fetchall()
        return [dict(r) for r in rows]

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
        self.conn.execute(f"UPDATE clients SET {set_clause} WHERE id=?", values)
        self.conn.commit()

    # --- Strategies ---

    def save_strategy(
        self, strategy_data: dict, run_id: str, raw_response: str = "", client_id: str = "default"
    ) -> str:
        sid = str(uuid.uuid4())
        self.conn.execute(
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
        )
        self.conn.commit()
        return sid

    def get_active_strategy(self, client_id: str = "default") -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM strategies WHERE valid_until > ? AND client_id = ? ORDER BY created_at DESC LIMIT 1",
            (datetime.utcnow().isoformat(), client_id),
        ).fetchone()
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
        self.conn.execute(
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
        )
        self.conn.commit()
        return pid

    def update_post_published(self, post_id: str, uri: str, cid: str):
        self.conn.execute(
            """UPDATE posts SET status='published', platform_uri=?, platform_id=?,
               bluesky_uri=?, bluesky_cid=?, published_at=? WHERE id=?""",
            (uri, cid, uri, cid, datetime.utcnow().isoformat(), post_id),
        )
        self.conn.commit()

    def update_post_failed(self, post_id: str, error: str):
        self.conn.execute("UPDATE posts SET status='failed' WHERE id=?", (post_id,))
        self.conn.commit()

    def get_recent_published_posts(self, days: int = 7, client_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM posts WHERE status='published' AND published_at > datetime('now', ?)"
        params: list = [f"-{days} days"]
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY published_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

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
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

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
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_post(self, post_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return dict(row) if row else None

    def approve_post(self, post_id: str) -> None:
        self.conn.execute("UPDATE posts SET status='approved' WHERE id=?", (post_id,))
        self.conn.commit()

    def reject_post(self, post_id: str) -> None:
        self.conn.execute("UPDATE posts SET status='rejected' WHERE id=?", (post_id,))
        self.conn.commit()

    def update_post_text(self, post_id: str, new_text: str) -> None:
        self.conn.execute(
            "UPDATE posts SET text=? WHERE id=? AND status IN ('draft', 'rejected')",
            (new_text, post_id),
        )
        self.conn.commit()

    def get_approved_posts(self, client_id: str | None = None) -> list[dict]:
        """Get posts in 'approved' status ready for publishing."""
        query = "SELECT * FROM posts WHERE status='approved'"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY created_at ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

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
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # --- Metrics ---

    def save_metrics(
        self, post_id: str, like_count: int = 0, repost_count: int = 0, reply_count: int = 0, quote_count: int = 0
    ) -> str:
        mid = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, post_id, like_count, repost_count, reply_count, quote_count),
        )
        self.conn.commit()
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
    ) -> str:
        lid = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO agent_logs (id, run_id, agent_name, input_summary, output_summary,
               reasoning, llm_model, input_tokens, output_tokens, duration_seconds, raw_llm_response)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        self.conn.commit()
        return lid

    def get_recent_agent_logs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- Pipeline Runs ---

    def start_pipeline_run(self, run_id: str, mode: str = "single", client_id: str = "default"):
        self.conn.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', ?)",
            (run_id, mode, datetime.utcnow().isoformat(), client_id),
        )
        self.conn.commit()

    def complete_pipeline_run(
        self,
        run_id: str,
        posts_published: int = 0,
        errors: list[str] | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ):
        self.conn.execute(
            """UPDATE pipeline_runs SET completed_at=?, status='completed',
               posts_published=?, errors=?, total_input_tokens=?, total_output_tokens=?
               WHERE id=?""",
            (
                datetime.utcnow().isoformat(),
                posts_published,
                json.dumps(errors or []),
                total_input_tokens,
                total_output_tokens,
                run_id,
            ),
        )
        self.conn.commit()

    def fail_pipeline_run(self, run_id: str, errors: list[str]):
        self.conn.execute(
            "UPDATE pipeline_runs SET completed_at=?, status='failed', errors=? WHERE id=?",
            (datetime.utcnow().isoformat(), json.dumps(errors), run_id),
        )
        self.conn.commit()

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_last_run_time(self, client_id: str) -> str | None:
        """Get the started_at timestamp of the most recent pipeline run for a client."""
        row = self.conn.execute(
            "SELECT started_at FROM pipeline_runs WHERE client_id=? ORDER BY started_at DESC LIMIT 1",
            (client_id,),
        ).fetchone()
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
            row = self.conn.execute(
                """SELECT COALESCE(SUM(like_count),0) as likes,
                          COALESCE(SUM(repost_count),0) as reposts,
                          COALESCE(SUM(reply_count),0) as replies
                   FROM metrics WHERE post_id=?""",
                (p["id"],),
            ).fetchone()
            likes = row["likes"]
            reposts = row["reposts"]
            replies = row["replies"]
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
        row = self.conn.execute(
            """SELECT COALESCE(SUM(total_input_tokens), 0) as input_tok,
                      COALESCE(SUM(total_output_tokens), 0) as output_tok
               FROM pipeline_runs
               WHERE client_id=? AND started_at >= date('now', 'start of month')""",
            (client_id,),
        ).fetchone()
        # Sonnet pricing: $3/M input, $15/M output
        input_cost = row["input_tok"] / 1_000_000 * 3
        output_cost = row["output_tok"] / 1_000_000 * 15
        return input_cost + output_cost

    def pause_client(self, client_id: str) -> None:
        """Set client status to paused (budget exceeded)."""
        self.conn.execute("UPDATE clients SET status='paused' WHERE id=?", (client_id,))
        self.conn.commit()

    def get_public_stats(self) -> dict:
        clients = self.conn.execute("SELECT COUNT(*) as c FROM clients WHERE active=1").fetchone()
        posts = self.conn.execute("SELECT COUNT(*) as c FROM posts WHERE status='published'").fetchone()
        platforms = self.conn.execute(
            "SELECT COUNT(DISTINCT platform) as c FROM posts WHERE status='published'"
        ).fetchone()
        return {
            "total_clients": clients["c"],
            "total_posts_published": posts["c"],
            "platforms_supported": platforms["c"],
        }

    # --- API Keys ---

    def create_api_key(self, client_id: str, key_hash: str, key_prefix: str, name: str = "default") -> str:
        kid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO api_keys (id, client_id, key_hash, key_prefix, name) VALUES (?, ?, ?, ?, ?)",
            (kid, client_id, key_hash, key_prefix, name),
        )
        self.conn.commit()
        return kid

    def get_api_keys_for_client(self, client_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, key_prefix, name, created_at, last_used_at, active FROM api_keys WHERE client_id=?",
            (client_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str) -> None:
        self.conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
        self.conn.commit()

    # --- Subscriptions ---

    def update_subscription(
        self,
        client_id: str,
        stripe_customer_id: str = "",
        stripe_subscription_id: str = "",
        subscription_status: str = "none",
        subscription_plan: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE clients SET stripe_customer_id=?, stripe_subscription_id=?,
               subscription_status=?, subscription_plan=? WHERE id=?""",
            (stripe_customer_id, stripe_subscription_id, subscription_status, subscription_plan, client_id),
        )
        self.conn.commit()

    def get_client_by_stripe_customer(self, stripe_customer_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM clients WHERE stripe_customer_id=?", (stripe_customer_id,)).fetchone()
        return dict(row) if row else None

    def record_stripe_event(self, event_id: str, event_type: str) -> bool:
        """Record a Stripe event. Returns False if already processed."""
        existing = self.conn.execute("SELECT id FROM stripe_events WHERE id=?", (event_id,)).fetchone()
        if existing:
            return False
        self.conn.execute(
            "INSERT INTO stripe_events (id, event_type) VALUES (?, ?)",
            (event_id, event_type),
        )
        self.conn.commit()
        return True

    def check_and_expire_trial(self, client_id: str) -> str:
        """If client is trialing and trial has ended, flip to 'expired'. Returns current status."""
        row = self.conn.execute(
            "SELECT subscription_status, trial_ends_at FROM clients WHERE id=?",
            (client_id,),
        ).fetchone()
        if not row:
            return "none"
        status = row["subscription_status"]
        if status == "trialing" and row["trial_ends_at"]:
            from datetime import datetime, timezone

            try:
                trial_end = datetime.fromisoformat(row["trial_ends_at"])
                if trial_end.tzinfo is None:
                    trial_end = trial_end.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return status
            if datetime.now(timezone.utc) > trial_end:
                self.conn.execute(
                    "UPDATE clients SET subscription_status='expired' WHERE id=?",
                    (client_id,),
                )
                self.conn.commit()
                return "expired"
        return status

    # --- Engineering Tasks (CTO Agent) ---

    def create_engineering_task(self, data: dict) -> str:
        tid = data.get("id") or str(uuid.uuid4())
        self.conn.execute(
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
        )
        self.conn.commit()
        return tid

    def get_next_engineering_task(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM engineering_tasks WHERE status='backlog' ORDER BY priority ASC, created_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_engineering_tasks(self, status: str | None = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM engineering_tasks"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

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
        self.conn.execute(f"UPDATE engineering_tasks SET {set_clause} WHERE id=?", values)
        self.conn.commit()

    def log_code_change(
        self, task_id: str, run_id: str, file_path: str, change_type: str, diff_summary: str = ""
    ) -> str:
        cid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO code_changes (id, task_id, run_id, file_path, change_type, diff_summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, task_id, run_id, file_path, change_type, diff_summary),
        )
        self.conn.commit()
        return cid

    def start_cto_run(self, run_id: str, task_id: str) -> None:
        self.conn.execute(
            "INSERT INTO cto_runs (id, task_id, status) VALUES (?, ?, 'running')",
            (run_id, task_id),
        )
        self.conn.commit()

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
        self.conn.execute(f"UPDATE cto_runs SET {', '.join(fields)} WHERE id=?", values)
        self.conn.commit()

    # --- CI Fix Tracking ---

    def log_ci_fix_attempt(self, data: dict) -> str:
        fid = data.get("id") or str(uuid.uuid4())
        self.conn.execute(
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
        )
        self.conn.commit()
        return fid

    def get_ci_fix_history(self, category: str | None = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM ci_fix_attempts"
        params: list = []
        if category:
            query += " WHERE failure_category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_ci_fix_success_rate(self, category: str | None = None) -> float:
        query = "SELECT COUNT(*) as total, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes FROM ci_fix_attempts"
        params: list = []
        if category:
            query += " WHERE failure_category = ?"
            params.append(category)
        row = self.conn.execute(query, params).fetchone()
        total = row["total"]
        return row["successes"] / total if total > 0 else 0.0

    def close(self):
        self.conn.close()
