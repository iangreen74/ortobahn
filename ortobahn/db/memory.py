"""Memory and learning — engineering tasks, CI fix tracking, chat, legal, articles, etc."""

from __future__ import annotations

import json
import uuid


class MemoryMixin:
    """Mixed into Database for memory/learning and miscellaneous domain methods."""

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

    # --- Chat Messages ---

    def save_chat_message(self, client_id: str, role: str, content: str) -> str:
        """Save a chat message and return its ID."""
        mid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO chat_messages (id, client_id, role, content) VALUES (?, ?, ?, ?)",
            (mid, client_id, role, content),
            commit=True,
        )
        return mid

    def get_chat_history(self, client_id: str, limit: int = 20) -> list[dict]:
        """Get recent chat messages for a client, oldest first."""
        rows = self.fetchall(
            "SELECT role, content, created_at FROM chat_messages WHERE client_id=? ORDER BY created_at DESC LIMIT ?",
            (client_id, limit),
        )
        return rows[::-1]

    # --- Legal Documents ---

    def save_legal_document(self, data: dict) -> str:
        """Save or update a legal document. Returns the document ID."""
        doc_id = data.get("id") or str(uuid.uuid4())
        # Upsert: update if same client_id + document_type exists
        existing = self.fetchone(
            "SELECT id FROM legal_documents WHERE client_id=? AND document_type=?",
            (data.get("client_id", "default"), data["document_type"]),
        )
        if existing:
            doc_id = existing["id"]
            self.execute(
                "UPDATE legal_documents SET content=?, version=?, title=?, effective_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (data["content"], data.get("version", "1.0"), data["title"], data.get("effective_date", ""), doc_id),
                commit=True,
            )
        else:
            self.execute(
                "INSERT INTO legal_documents (id, client_id, document_type, title, content, version, effective_date, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    data.get("client_id", "default"),
                    data["document_type"],
                    data["title"],
                    data["content"],
                    data.get("version", "1.0"),
                    data.get("effective_date", ""),
                    data.get("created_by", "legal_agent"),
                ),
                commit=True,
            )
        return doc_id

    def get_legal_documents(self, client_id: str = "default") -> list[dict]:
        """Get all legal documents for a client."""
        return self.fetchall(
            "SELECT * FROM legal_documents WHERE client_id=? ORDER BY updated_at DESC",
            (client_id,),
        )

    def get_legal_document(self, document_type: str, client_id: str = "default") -> dict | None:
        """Get a specific legal document by type."""
        return self.fetchone(
            "SELECT * FROM legal_documents WHERE client_id=? AND document_type=? ORDER BY updated_at DESC LIMIT 1",
            (client_id, document_type),
        )

    # --- Access Logs ---

    def log_access(
        self, method: str, path: str, status_code: int, source_ip: str, user_agent: str, response_time_ms: float = 0
    ) -> None:
        """Log an HTTP access request for security monitoring."""
        self.execute(
            "INSERT INTO access_logs (id, method, path, status_code, source_ip, user_agent, response_time_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), method, path, status_code, source_ip, user_agent[:500], response_time_ms),
            commit=True,
        )

    def get_suspicious_access_logs(self, hours: int = 24) -> list[dict]:
        """Get suspicious access log entries from the last N hours."""
        return self.fetchall(
            "SELECT method, path, status_code, source_ip, user_agent, timestamp FROM access_logs "
            "WHERE (path LIKE '%%.env%%' OR path LIKE '%%/admin%%' OR path LIKE '%%/wp-%%' OR path LIKE '%%/phpmyadmin%%' OR status_code = 403) "
            "AND timestamp >= datetime('now', ? || ' hours') ORDER BY timestamp DESC LIMIT 100",
            (str(-hours),),
        )

    def cleanup_access_logs(self, days: int = 7) -> int:
        """Remove access logs older than N days. Returns count deleted."""
        result = self.execute(
            "DELETE FROM access_logs WHERE timestamp < datetime('now', ? || ' days')",
            (str(-days),),
            commit=True,
        )
        return result.rowcount if hasattr(result, "rowcount") else 0

    # --- Articles ---

    def save_article(self, data: dict) -> str:
        """Save a new article draft and return its ID."""
        aid = data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO articles
               (id, client_id, run_id, title, subtitle, body_markdown, tags,
                meta_description, topic_used, confidence, word_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                data.get("client_id", "default"),
                data.get("run_id", ""),
                data["title"],
                data.get("subtitle", ""),
                data["body_markdown"],
                json.dumps(data.get("tags", [])),
                data.get("meta_description", ""),
                data.get("topic_used", ""),
                data.get("confidence", 0.0),
                data.get("word_count", 0),
                data.get("status", "draft"),
            ),
            commit=True,
        )
        return aid

    def get_article(self, article_id: str) -> dict | None:
        row = self.fetchone("SELECT * FROM articles WHERE id=?", (article_id,))
        if row and row.get("tags"):
            row["tags"] = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
        return row

    def get_recent_articles(self, client_id: str, limit: int = 10, offset: int = 0) -> list[dict]:
        rows = self.fetchall(
            "SELECT * FROM articles WHERE client_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (client_id, limit, offset),
        )
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                r["tags"] = json.loads(r["tags"])
        return rows

    def count_articles(self, client_id: str) -> int:
        """Count articles for a client."""
        row = self.fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE client_id=?",
            (client_id,),
        )
        return row["cnt"] if row else 0

    def get_draft_articles(self, client_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM articles WHERE status='draft'"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY created_at DESC"
        rows = self.fetchall(query, params)
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                r["tags"] = json.loads(r["tags"])
        return rows

    def get_approved_articles(self, client_id: str | None = None) -> list[dict]:
        """Get articles in 'approved' status ready for publishing."""
        query = "SELECT * FROM articles WHERE status='approved'"
        params: list = []
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        query += " ORDER BY created_at ASC"
        rows = self.fetchall(query, params)
        for r in rows:
            if r.get("tags") and isinstance(r["tags"], str):
                r["tags"] = json.loads(r["tags"])
        return rows

    def approve_article(self, article_id: str) -> None:
        self.execute(
            "UPDATE articles SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (article_id,),
            commit=True,
        )

    def reject_article(self, article_id: str) -> None:
        self.execute(
            "UPDATE articles SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (article_id,),
            commit=True,
        )

    def update_article_body(self, article_id: str, title: str, subtitle: str, body_markdown: str) -> None:
        word_count = len(body_markdown.split())
        self.execute(
            "UPDATE articles SET title=?, subtitle=?, body_markdown=?, word_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (title, subtitle, body_markdown, word_count, article_id),
            commit=True,
        )

    def save_article_publication(self, article_id: str, platform: str, status: str = "pending", **kwargs) -> str:
        pub_id = str(uuid.uuid4())
        self.execute(
            """INSERT INTO article_publications
               (id, article_id, platform, status, published_url, platform_id, error, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pub_id,
                article_id,
                platform,
                status,
                kwargs.get("published_url"),
                kwargs.get("platform_id"),
                kwargs.get("error"),
                kwargs.get("published_at"),
            ),
            commit=True,
        )
        return pub_id

    def update_article_publication(self, pub_id: str, status: str, **kwargs) -> None:
        fields = ["status=?"]
        values: list = [status]
        for key in ("published_url", "platform_id", "error", "published_at"):
            if key in kwargs:
                fields.append(f"{key}=?")
                values.append(kwargs[key])
        values.append(pub_id)
        self.execute(f"UPDATE article_publications SET {', '.join(fields)} WHERE id=?", values, commit=True)

    def get_last_article_time(self, client_id: str) -> str | None:
        row = self.fetchone(
            "SELECT created_at FROM articles WHERE client_id=? ORDER BY created_at DESC LIMIT 1",
            (client_id,),
        )
        return row["created_at"] if row else None

    def get_article_publications(self, article_id: str) -> list[dict]:
        return self.fetchall(
            "SELECT * FROM article_publications WHERE article_id=? ORDER BY created_at DESC",
            (article_id,),
        )

    def update_article_publication_failed(
        self, pub_id: str, error: str, failure_category: str = "unknown", retry_count: int = 0
    ) -> None:
        """Mark an article publication as failed with error classification."""
        self.execute(
            "UPDATE article_publications SET status='failed', error=?, failure_category=?, retry_count=? WHERE id=?",
            (error, failure_category, retry_count, pub_id),
            commit=True,
        )

    def get_failed_article_publications(self, client_id: str | None = None) -> list[dict]:
        """Get article publications that failed and may be retryable."""
        query = (
            "SELECT ap.*, a.client_id, a.title, a.body_markdown, a.tags"
            " FROM article_publications ap"
            " JOIN articles a ON ap.article_id = a.id"
            " WHERE ap.status = 'failed'"
        )
        params: list = []
        if client_id:
            query += " AND a.client_id = ?"
            params.append(client_id)
        query += " ORDER BY ap.created_at DESC"
        return self.fetchall(query, params)

    # --- Test Results (Flaky Test Detection) ---

    def save_test_result(self, data: dict) -> str:
        """Save a single test result and return its ID."""
        rid = data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO test_results
               (id, run_id, test_file, test_name, outcome, duration_ms, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                data["run_id"],
                data["test_file"],
                data["test_name"],
                data["outcome"],
                data.get("duration_ms", 0.0),
                data.get("error_message", ""),
            ),
            commit=True,
        )
        return rid

    def save_test_results_batch(self, run_id: str, results: list[dict]) -> int:
        """Save a batch of test results for a run. Returns count saved."""
        count = 0
        for result in results:
            result["run_id"] = run_id
            try:
                self.save_test_result(result)
                count += 1
            except Exception:
                pass
        return count

    def get_test_history(self, test_name: str, limit: int = 20) -> list[dict]:
        """Get recent results for a specific test, newest first."""
        return self.fetchall(
            "SELECT * FROM test_results WHERE test_name = ? ORDER BY created_at DESC LIMIT ?",
            (test_name, limit),
        )

    def get_flaky_tests(self, window_days: int = 14, min_runs: int = 3) -> list[dict]:
        """Find tests with both pass and fail outcomes within the window."""
        return self.fetchall(
            """SELECT
                test_name,
                test_file,
                COUNT(*) as total_runs,
                SUM(CASE WHEN outcome IN ('failed', 'error') THEN 1 ELSE 0 END) as failures,
                SUM(CASE WHEN outcome = 'passed' THEN 1 ELSE 0 END) as passes
            FROM test_results
            WHERE created_at >= datetime('now', ? || ' days')
            GROUP BY test_name
            HAVING total_runs >= ?
                AND failures > 0
                AND passes > 0
            ORDER BY CAST(failures AS REAL) / total_runs DESC""",
            (str(-window_days), min_runs),
        )

    # --- CI Errors (Structured Error Tracking) ---

    def save_ci_error(self, data: dict) -> str:
        """Save a parsed CI error and return its ID."""
        eid = data.get("id") or str(uuid.uuid4())
        self.execute(
            """INSERT INTO ci_errors
               (id, run_id, gh_run_id, test_name, test_file, error_type,
                error_message, stack_trace, assertion_expected, assertion_actual,
                blame_author, blame_commit, related_commits)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid,
                data["run_id"],
                data.get("gh_run_id"),
                data.get("test_name", ""),
                data.get("test_file", ""),
                data.get("error_type", "unknown"),
                data.get("error_message", ""),
                data.get("stack_trace", ""),
                data.get("assertion_expected", ""),
                data.get("assertion_actual", ""),
                data.get("blame_author", ""),
                data.get("blame_commit", ""),
                json.dumps(data.get("related_commits", [])),
            ),
            commit=True,
        )
        return eid

    def get_ci_errors_for_run(self, run_id: str) -> list[dict]:
        """Get all parsed CI errors for a specific run."""
        return self.fetchall(
            "SELECT * FROM ci_errors WHERE run_id = ? ORDER BY created_at DESC",
            (run_id,),
        )

    # --- Executive Directives ---

    def save_directive(self, run_id: str, client_id: str, directive: dict) -> str:
        """Save a CEO executive directive for audit trail."""
        did = str(uuid.uuid4())
        self.execute(
            "INSERT INTO executive_directives (id, run_id, client_id, priority, category, directive, target_agent, reasoning) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did,
                run_id,
                client_id,
                directive.get("priority", "medium"),
                directive.get("category", ""),
                directive.get("directive", ""),
                directive.get("target_agent", ""),
                directive.get("reasoning", ""),
            ),
            commit=True,
        )
        return did
