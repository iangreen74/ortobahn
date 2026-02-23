"""Pipeline run tracking — start, complete, fail, query recent runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

# TTL for cached recent pipeline runs (seconds).
_RECENT_RUNS_CACHE_TTL: float = 30.0


class PipelineMixin:
    """Mixed into Database to provide pipeline-run methods."""

    # --- Pipeline Runs ---

    def start_pipeline_run(self, run_id: str, mode: str = "single", client_id: str = "default"):
        self.execute(
            "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, ?, ?, 'running', ?)",
            (run_id, mode, datetime.now(timezone.utc).isoformat(), client_id),
            commit=True,
        )
        self._cache_invalidate_prefix("recent_runs")

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
        self._cache_invalidate_prefix("recent_runs")

    def fail_pipeline_run(self, run_id: str, errors: list[str]):
        self.execute(
            "UPDATE pipeline_runs SET completed_at=?, status='failed', errors=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), json.dumps(errors), run_id),
            commit=True,
        )
        self._cache_invalidate_prefix("recent_runs")

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        cache_key = f"recent_runs:{limit}"
        cached = self._cache_get(cache_key, _RECENT_RUNS_CACHE_TTL)
        if cached is not None:
            return cached
        result = self.fetchall("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        self._cache_set(cache_key, result)
        return result

    def get_last_run_time(self, client_id: str) -> str | None:
        """Get the started_at timestamp of the most recent pipeline run for a client."""
        row = self.fetchone(
            "SELECT started_at FROM pipeline_runs WHERE client_id=? ORDER BY started_at DESC LIMIT 1",
            (client_id,),
        )
        return row["started_at"] if row else None

    # --- Watchdog helpers ---

    def get_stale_runs(self, timeout_minutes: int = 60) -> list[dict]:
        """Get pipeline runs stuck in 'running' longer than timeout_minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        return self.fetchall(
            "SELECT * FROM pipeline_runs WHERE status='running' AND started_at < ?",
            (cutoff,),
        )

    def save_health_check(
        self, probe: str, status: str, detail: str | None = None, client_id: str | None = None
    ) -> str:
        """Record a watchdog health check result."""
        hid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO health_checks (id, probe, status, detail, client_id) VALUES (?, ?, ?, ?, ?)",
            (hid, probe, status, detail, client_id),
            commit=True,
        )
        return hid

    def save_remediation(
        self,
        finding_type: str,
        action: str,
        success: bool,
        client_id: str | None = None,
        verified: bool | None = None,
    ) -> str:
        """Record a watchdog remediation action."""
        rid = str(uuid.uuid4())
        self.execute(
            "INSERT INTO watchdog_remediations (id, finding_type, client_id, action, success, verified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, finding_type, client_id, action, int(success), int(verified) if verified is not None else None),
            commit=True,
        )
        return rid

    # --- Deployment tracking ---

    def record_deploy(
        self,
        sha: str,
        environment: str = "production",
        previous_sha: str | None = None,
    ) -> str:
        """Record a new deployment. Returns the deploy ID."""
        deploy_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.execute(
            "INSERT INTO deployments (id, sha, environment, status, previous_sha, deployed_at) "
            "VALUES (?, ?, ?, 'deployed', ?, ?)",
            (deploy_id, sha, environment, previous_sha, now),
            commit=True,
        )
        return deploy_id

    def get_current_deploy(self, environment: str = "production") -> dict | None:
        """Get the most recent active deployment for an environment."""
        return self.fetchone(
            "SELECT * FROM deployments WHERE environment=? AND status='deployed' ORDER BY deployed_at DESC LIMIT 1",
            (environment,),
        )

    def get_recent_deploys(self, environment: str = "production", limit: int = 5) -> list[dict]:
        """Get recent deployments for an environment."""
        return self.fetchall(
            "SELECT * FROM deployments WHERE environment=? ORDER BY deployed_at DESC LIMIT ?",
            (environment, limit),
        )

    def mark_deploy_validated(self, deploy_id: str) -> None:
        """Mark a deployment as validated (smoke tests passed)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.execute(
            "UPDATE deployments SET status='validated', validated_at=? WHERE id=?",
            (now, deploy_id),
            commit=True,
        )

    def mark_deploy_rolled_back(self, deploy_id: str) -> None:
        """Mark a deployment as rolled back."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.execute(
            "UPDATE deployments SET status='rolled_back', rolled_back_at=? WHERE id=?",
            (now, deploy_id),
            commit=True,
        )

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
