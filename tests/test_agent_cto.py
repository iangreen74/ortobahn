"""Tests for the CTO agent."""

from __future__ import annotations

import pytest

from ortobahn.agents.cto import CTOAgent
from ortobahn.db import Database
from ortobahn.git_utils import BLOCKED_PATTERNS, is_path_safe
from ortobahn.models import CTOResult


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "cto_test.db")


class TestCTOPathSafety:
    """Test the file path safety mechanisms (now in git_utils)."""

    def test_safe_paths(self):
        assert is_path_safe("ortobahn/agents/new.py") is True
        assert is_path_safe("tests/test_new.py") is True

    def test_blocked_env(self):
        assert is_path_safe(".env") is False
        assert is_path_safe("config/.env.local") is False

    def test_blocked_git(self):
        assert is_path_safe(".git/config") is False
        assert is_path_safe(".git/hooks/pre-commit") is False

    def test_blocked_secrets(self):
        assert is_path_safe("secrets/key.pem") is False
        assert is_path_safe("credential_store.json") is False

    def test_path_traversal_blocked(self):
        assert is_path_safe("../../etc/passwd") is False
        assert is_path_safe("../outside/file.py") is False


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
        db.create_engineering_task(
            {
                "title": "Low priority",
                "description": "P4 task",
                "priority": 4,
                "category": "docs",
            }
        )
        db.create_engineering_task(
            {
                "title": "High priority",
                "description": "P1 task",
                "priority": 1,
                "category": "infra",
            }
        )
        db.create_engineering_task(
            {
                "title": "Medium priority",
                "description": "P3 task",
                "priority": 3,
                "category": "feature",
            }
        )

        task = db.get_next_engineering_task()
        assert task is not None
        assert task["title"] == "High priority"
        assert task["priority"] == 1


class TestCTOBranchNaming:
    """Test branch name generation."""

    def test_branch_format(self, db):
        db.create_engineering_task(
            {
                "title": "Test feature",
                "description": "A test",
                "priority": 3,
                "category": "feature",
            }
        )
        task = db.get_next_engineering_task()
        branch_name = f"cto/{task.get('category', 'feature')}/{task['id'][:8]}"
        assert branch_name.startswith("cto/feature/")
        assert len(branch_name) > len("cto/feature/")


class TestCTOAuditLogging:
    """Test that CTO runs create audit records."""

    def test_start_and_complete_run(self, db):
        tid = db.create_engineering_task(
            {
                "title": "Audit test",
                "description": "Testing audit",
                "priority": 3,
                "category": "test",
            }
        )

        db.start_cto_run("run-001", tid)
        db.complete_cto_run("run-001", status="success", commit_sha="abc123")

        row = db.fetchone("SELECT * FROM cto_runs WHERE id='run-001'")
        assert row is not None
        assert row["status"] == "success"
        assert row["commit_sha"] == "abc123"

    def test_code_change_logging(self, db):
        tid = db.create_engineering_task(
            {
                "title": "Change log test",
                "description": "Testing code change log",
                "priority": 3,
                "category": "test",
            }
        )

        change_id = db.log_code_change(
            task_id=tid,
            run_id="run-002",
            file_path="ortobahn/new_file.py",
            change_type="create",
            diff_summary="Added new file",
        )
        assert change_id is not None

        row = db.fetchone("SELECT * FROM code_changes WHERE id=?", (change_id,))
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


# ---------------------------------------------------------------------------
# TestCTORelevantFilesAdvanced
# ---------------------------------------------------------------------------


class TestCTORelevantFilesAdvanced:
    """Advanced tests for keyword-based file discovery."""

    def test_database_keywords(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Fix database backup", "improve s3 backup strategy for database")
        assert any("db.py" in f for f in files) or any("config.py" in f for f in files)

    def test_bluesky_keyword(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Bluesky integration", "update bluesky posting logic")
        assert any("bluesky.py" in f for f in files)

    def test_migration_keyword(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Add migration", "database migration for new table")
        assert any("migrations.py" in f for f in files)

    def test_pipeline_keyword(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Pipeline improvement", "orchestrator optimisation")
        assert any("orchestrator.py" in f for f in files)

    def test_no_matching_keywords_still_returns_core(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("Completely unrelated gibberish", "zzz yyy xxx")
        # Should always include models.py and config.py
        assert any("models.py" in f for f in files)
        assert any("config.py" in f for f in files)

    def test_only_existing_files_returned(self, db):
        """All returned files should exist on disk."""
        from ortobahn.git_utils import PROJECT_ROOT as _ROOT

        agent = CTOAgent(db, api_key="sk-ant-test")
        files = agent._relevant_source_files("health api endpoint docs", "full stack update")
        for f in files:
            assert (_ROOT / f).is_file(), f"{f} does not exist"


# ---------------------------------------------------------------------------
# TestCTOReadSourceFiles
# ---------------------------------------------------------------------------


class TestCTOReadSourceFiles:
    """Test _read_source_files method."""

    def test_reads_existing_files(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        context = agent._read_source_files(["ortobahn/models.py"])
        assert "models.py" in context
        assert "```python" in context

    def test_skips_missing_files(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        context = agent._read_source_files(["nonexistent/file.py"])
        assert context == ""

    def test_mixed_existing_and_missing(self, db):
        agent = CTOAgent(db, api_key="sk-ant-test")
        context = agent._read_source_files(["ortobahn/models.py", "nonexistent.py"])
        assert "models.py" in context
        assert "nonexistent.py" not in context


# ---------------------------------------------------------------------------
# TestCTORunTests
# ---------------------------------------------------------------------------


class TestCTORunTests:
    """Test the _run_tests subprocess wrapper."""

    def test_tests_pass(self, db):
        from unittest.mock import MagicMock, patch

        agent = CTOAgent(db, api_key="sk-ant-test")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "20 passed"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            passed, output = agent._run_tests()

        assert passed is True
        assert "20 passed" in output

    def test_tests_fail(self, db):
        from unittest.mock import MagicMock, patch

        agent = CTOAgent(db, api_key="sk-ant-test")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED tests/test_x.py::test_y"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            passed, output = agent._run_tests()

        assert passed is False
        assert "FAILED" in output

    def test_tests_timeout(self, db):
        import subprocess
        from unittest.mock import patch

        agent = CTOAgent(db, api_key="sk-ant-test")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=120)):
            passed, output = agent._run_tests()

        assert passed is False
        assert "timed out" in output.lower()

    def test_pytest_not_found(self, db):
        from unittest.mock import patch

        agent = CTOAgent(db, api_key="sk-ant-test")

        with patch("subprocess.run", side_effect=FileNotFoundError("pytest")):
            passed, output = agent._run_tests()

        assert passed is True
        assert "skipped" in output.lower()


# ---------------------------------------------------------------------------
# TestCTOPriorityOrdering — more scenarios
# ---------------------------------------------------------------------------


class TestCTOPriorityOrderingAdvanced:
    """Additional priority ordering edge cases."""

    def test_same_priority_picks_first_created(self, db):
        db.create_engineering_task(
            {"title": "First", "description": "Created first", "priority": 2, "category": "feature"}
        )
        db.create_engineering_task(
            {"title": "Second", "description": "Created second", "priority": 2, "category": "feature"}
        )

        task = db.get_next_engineering_task()
        assert task is not None
        assert task["title"] == "First"

    def test_completed_tasks_not_picked(self, db):
        tid = db.create_engineering_task(
            {"title": "Done task", "description": "Already done", "priority": 1, "category": "feature"}
        )
        db.update_engineering_task(tid, {"status": "completed"})

        task = db.get_next_engineering_task()
        assert task is None

    def test_in_progress_tasks_not_picked(self, db):
        tid = db.create_engineering_task(
            {"title": "Busy task", "description": "In progress", "priority": 1, "category": "feature"}
        )
        db.update_engineering_task(tid, {"status": "in_progress"})

        task = db.get_next_engineering_task()
        assert task is None

    def test_failed_tasks_not_picked(self, db):
        tid = db.create_engineering_task(
            {"title": "Failed task", "description": "Previously failed", "priority": 1, "category": "bugfix"}
        )
        db.update_engineering_task(tid, {"status": "failed"})

        task = db.get_next_engineering_task()
        assert task is None


# ---------------------------------------------------------------------------
# TestCTORunWithMockedLLM — full run() integration
# ---------------------------------------------------------------------------


class TestCTORunWithMockedLLM:
    """Test full run() by mocking git operations and LLM calls."""

    def test_run_success_with_valid_llm_response(self, db, tmp_path):
        import json
        from unittest.mock import patch

        from ortobahn.llm import LLMResponse

        db.create_engineering_task(
            {"title": "Add healthcheck", "description": "Add health endpoint", "priority": 2, "category": "feature"}
        )

        llm_response = LLMResponse(
            text=json.dumps(
                {
                    "plan": "Add /healthz endpoint",
                    "changes": [
                        {
                            "file_path": "ortobahn/healthcheck.py",
                            "content": "def health(): return 'ok'",
                            "change_type": "create",
                        }
                    ],
                    "test_files": [],
                }
            ),
            input_tokens=500,
            output_tokens=300,
            model="test",
            thinking="",
        )

        agent = CTOAgent(db, api_key="sk-ant-test")

        with (
            patch.object(agent, "call_llm", return_value=llm_response),
            patch("ortobahn.agents.cto.current_branch", return_value="main"),
            patch("ortobahn.agents.cto.create_branch"),
            patch("ortobahn.agents.cto.switch_branch"),
            patch("ortobahn.agents.cto.commit_all", return_value="def456"),
            patch("ortobahn.agents.cto.push_branch"),
            patch("ortobahn.agents.cto.create_pr", return_value="https://github.com/pull/99"),
            patch("ortobahn.agents.cto.enable_auto_merge", return_value=True),
            patch("ortobahn.agents.cto.is_path_safe", return_value=True),
            patch.object(agent, "_run_tests", return_value=(True, "10 passed")),
            patch("ortobahn.agents.cto.PROJECT_ROOT", tmp_path),
        ):
            result = agent.run("run-cto-001")

        assert result.status == "success"
        assert result.commit_sha == "def456"
        assert any("healthcheck.py" in f for f in result.files_changed)

    def test_run_fails_on_bad_json(self, db):
        from unittest.mock import patch

        from ortobahn.llm import LLMResponse

        db.create_engineering_task(
            {"title": "Bad JSON task", "description": "Will fail", "priority": 1, "category": "bugfix"}
        )

        llm_response = LLMResponse(
            text="This is not valid JSON at all.",
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        agent = CTOAgent(db, api_key="sk-ant-test")

        with (
            patch.object(agent, "call_llm", return_value=llm_response),
            patch("ortobahn.agents.cto.current_branch", return_value="main"),
            patch("ortobahn.agents.cto.create_branch"),
            patch("ortobahn.agents.cto.switch_branch"),
            patch("ortobahn.agents.cto.delete_branch"),
        ):
            result = agent.run("run-cto-bad-json")

        assert result.status == "failed"
        assert "JSON" in result.error

    def test_run_rollback_on_test_failure(self, db, tmp_path):
        import json
        from unittest.mock import patch

        from ortobahn.llm import LLMResponse

        db.create_engineering_task(
            {"title": "Test failure task", "description": "Tests will fail", "priority": 2, "category": "feature"}
        )

        llm_response = LLMResponse(
            text=json.dumps(
                {
                    "plan": "Add buggy code",
                    "changes": [{"file_path": "ortobahn/buggy.py", "content": "x = 1/0", "change_type": "create"}],
                    "test_files": [],
                }
            ),
            input_tokens=200,
            output_tokens=100,
            model="test",
        )

        agent = CTOAgent(db, api_key="sk-ant-test")

        with (
            patch.object(agent, "call_llm", return_value=llm_response),
            patch("ortobahn.agents.cto.current_branch", return_value="main"),
            patch("ortobahn.agents.cto.create_branch"),
            patch("ortobahn.agents.cto.switch_branch"),
            patch("ortobahn.agents.cto.delete_branch") as mock_delete,
            patch("ortobahn.agents.cto.is_path_safe", return_value=True),
            patch.object(agent, "_run_tests", return_value=(False, "FAILED test_x.py")),
            patch("ortobahn.agents.cto.PROJECT_ROOT", tmp_path),
        ):
            result = agent.run("run-cto-test-fail")

        assert result.status == "failed"
        assert "Tests failed" in result.error
        mock_delete.assert_called_once()
