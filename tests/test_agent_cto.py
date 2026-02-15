"""Tests for the CTO agent."""

from __future__ import annotations

import pytest

from ortobahn.agents.cto import CTOAgent, BLOCKED_PATTERNS
from ortobahn.db import Database
from ortobahn.models import CTOResult


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "cto_test.db")


class TestCTOPathSafety:
    """Test the file path safety mechanisms."""

    def test_safe_paths(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        assert agent._is_path_safe("ortobahn/agents/new.py") is True
        assert agent._is_path_safe("tests/test_new.py") is True

    def test_blocked_env(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        assert agent._is_path_safe(".env") is False
        assert agent._is_path_safe("config/.env.local") is False

    def test_blocked_git(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        assert agent._is_path_safe(".git/config") is False
        assert agent._is_path_safe(".git/hooks/pre-commit") is False

    def test_blocked_secrets(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        assert agent._is_path_safe("secrets/key.pem") is False
        assert agent._is_path_safe("credential_store.json") is False

    def test_path_traversal_blocked(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        assert agent._is_path_safe("../../etc/passwd") is False
        assert agent._is_path_safe("../outside/file.py") is False


class TestCTORelevantFiles:
    """Test keyword-based file discovery."""

    def test_health_keywords(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Add health check", "endpoint for ALB health checks")
        # Should include core files at minimum
        assert any("config.py" in f for f in files)

    def test_auth_keywords(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Add login", "password-based auth login")
        # Should pick up auth.py
        assert any("auth.py" in f for f in files)

    def test_always_includes_core_files(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Random task", "no matching keywords here xyz")
        assert any("models.py" in f for f in files)
        assert any("config.py" in f for f in files)

    def test_caps_at_15_files(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        # Even with many keyword matches, should cap
        files = agent._relevant_source_files(
            "health auth login api content test coverage database backup model agent pipeline config bluesky",
            "everything everywhere",
        )
        assert len(files) <= 15


class TestCTOSkipOnEmptyBacklog:
    """Test that the agent gracefully handles empty backlog."""

    def test_skip_on_empty(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        result = agent.run("test-run-001")
        assert isinstance(result, CTOResult)
        assert result.status == "skipped"
        assert result.task_id == ""


class TestCTOPriorityOrdering:
    """Test that tasks are picked up in priority order."""

    def test_highest_priority_first(self, db):
        # Create tasks with different priorities
        db.create_engineering_task({
            "title": "Low priority",
            "description": "P4 task",
            "priority": 4,
            "category": "docs",
        })
        db.create_engineering_task({
            "title": "High priority",
            "description": "P1 task",
            "priority": 1,
            "category": "infra",
        })
        db.create_engineering_task({
            "title": "Medium priority",
            "description": "P3 task",
            "priority": 3,
            "category": "feature",
        })

        task = db.get_next_engineering_task()
        assert task is not None
        assert task["title"] == "High priority"
        assert task["priority"] == 1


class TestCTOBranchNaming:
    """Test branch name generation."""

    def test_branch_format(self, db):
        tid = db.create_engineering_task({
            "title": "Test feature",
            "description": "A test",
            "priority": 3,
            "category": "feature",
        })
        task = db.get_next_engineering_task()
        branch_name = f"cto/{task.get('category', 'feature')}/{task['id'][:8]}"
        assert branch_name.startswith("cto/feature/")
        assert len(branch_name) > len("cto/feature/")


class TestCTOAuditLogging:
    """Test that CTO runs create audit records."""

    def test_start_and_complete_run(self, db):
        tid = db.create_engineering_task({
            "title": "Audit test",
            "description": "Testing audit",
            "priority": 3,
            "category": "test",
        })

        db.start_cto_run("run-001", tid)
        db.complete_cto_run("run-001", status="success", commit_sha="abc123")

        row = db.conn.execute("SELECT * FROM cto_runs WHERE id='run-001'").fetchone()
        assert row is not None
        assert row["status"] == "success"
        assert row["commit_sha"] == "abc123"

    def test_code_change_logging(self, db):
        tid = db.create_engineering_task({
            "title": "Change log test",
            "description": "Testing code change log",
            "priority": 3,
            "category": "test",
        })

        change_id = db.log_code_change(
            task_id=tid,
            run_id="run-002",
            file_path="ortobahn/new_file.py",
            change_type="create",
            diff_summary="Added new file",
        )
        assert change_id is not None

        row = db.conn.execute("SELECT * FROM code_changes WHERE id=?", (change_id,)).fetchone()
        assert row["file_path"] == "ortobahn/new_file.py"
        assert row["change_type"] == "create"


class TestBlockedPatterns:
    """Verify the blocked patterns set is comprehensive."""

    def test_env_blocked(self):
        assert ".env" in BLOCKED_PATTERNS

    def test_git_blocked(self):
        assert ".git/" in BLOCKED_PATTERNS
        assert ".git" in BLOCKED_PATTERNS

    def test_secrets_blocked(self):
        assert "secret" in BLOCKED_PATTERNS
        assert "credential" in BLOCKED_PATTERNS
