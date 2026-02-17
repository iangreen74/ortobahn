"""Tests for CI Fix Agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.migrations import run_migrations
from ortobahn.models import CIFailureCategory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cifix_agent(test_db):
    """CIFixAgent instance backed by the test database."""
    from ortobahn.agents.cifix import CIFixAgent

    return CIFixAgent(db=test_db, api_key="sk-ant-test")


# ---------------------------------------------------------------------------
# TestCIFailureCategorization
# ---------------------------------------------------------------------------


class TestCIFailureCategorization:
    """Test the _categorize_failure method."""

    def test_categorize_ruff_lint_error(self, cifix_agent):
        logs = "ortobahn/foo.py:10:5: E501 Line too long (120 > 119)"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.LINT

    def test_categorize_ruff_format_error(self, cifix_agent):
        logs = "would reformat ortobahn/foo.py\n1 file would be reformatted"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.FORMAT

    def test_categorize_ruff_format_error_alt(self, cifix_agent):
        logs = "error: ruff format found differences"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.FORMAT

    def test_categorize_mypy_error(self, cifix_agent):
        logs = "ortobahn/agents/reflection.py:306: error: Need type annotation [var-annotated]"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.TYPECHECK

    def test_categorize_pytest_failure(self, cifix_agent):
        logs = "FAILED tests/test_foo.py::test_bar - AssertionError: assert 1 == 2"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.TEST

    def test_categorize_unknown(self, cifix_agent):
        logs = "something completely unrelated happened in CI"
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# TestCIErrorExtraction
# ---------------------------------------------------------------------------


class TestCIErrorExtraction:
    """Test the _extract_error_details method."""

    def test_extract_lint_errors(self, cifix_agent):
        logs = (
            "ortobahn/foo.py:10:5: E501 Line too long (120 > 119)\nortobahn/bar.py:3:1: F401 'os' imported but unused"
        )
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.LINT)
        assert len(errors) == 2

        assert errors[0].file_path == "ortobahn/foo.py"
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].code == "E501"

        assert errors[1].file_path == "ortobahn/bar.py"
        assert errors[1].line == 3
        assert errors[1].column == 1
        assert errors[1].code == "F401"

    def test_extract_mypy_errors(self, cifix_agent):
        logs = 'ortobahn/agents/reflection.py:306: error: Need type annotation for "progress" [var-annotated]'
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.TYPECHECK)
        assert len(errors) >= 1

        err = errors[0]
        assert err.file_path == "ortobahn/agents/reflection.py"
        assert err.line == 306
        assert err.code == "var-annotated"

    def test_extract_pytest_failures(self, cifix_agent):
        logs = "FAILED tests/test_foo.py::TestFoo::test_bar - AssertionError"
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.TEST)
        assert len(errors) >= 1

        err = errors[0]
        assert err.file_path == "tests/test_foo.py"


# ---------------------------------------------------------------------------
# TestCIFixResult
# ---------------------------------------------------------------------------


class TestCIFixResult:
    """Test run() return values for edge cases."""

    def test_no_failures_result(self, test_db):
        from ortobahn.agents.cifix import CIFixAgent

        agent = CIFixAgent(db=test_db, api_key="sk-ant-test")

        with patch.object(agent, "_fetch_failed_runs", return_value=[]):
            result = agent.run(run_id="run-nofail")

        assert result.status == "no_failures"

    def test_gh_cli_unavailable(self, test_db):
        from ortobahn.agents.cifix import CIFixAgent

        agent = CIFixAgent(db=test_db, api_key="sk-ant-test")

        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = agent.run(run_id="run-nogh")

        assert result.status == "skipped"


# ---------------------------------------------------------------------------
# TestCIFixDBIntegration
# ---------------------------------------------------------------------------


class TestCIFixDBIntegration:
    """Test Database methods for CI fix tracking."""

    def test_log_fix_attempt(self, test_db):
        fid = test_db.log_ci_fix_attempt(
            {
                "run_id": "run-1",
                "gh_run_id": 12345,
                "job_name": "typecheck",
                "failure_category": "typecheck",
                "fix_strategy": "mypy_annotation",
                "status": "success",
                "files_changed": ["ortobahn/agents/reflection.py"],
                "validation_passed": True,
            }
        )
        assert fid

        history = test_db.get_ci_fix_history()
        assert len(history) == 1
        assert history[0]["job_name"] == "typecheck"

    def test_success_rate_calculation(self, test_db):
        for i, status in enumerate(["success", "success", "failed"]):
            test_db.log_ci_fix_attempt(
                {
                    "run_id": f"run-{i}",
                    "job_name": "lint",
                    "failure_category": "lint",
                    "fix_strategy": "ruff_autofix",
                    "status": status,
                }
            )

        rate = test_db.get_ci_fix_success_rate()
        assert abs(rate - 2 / 3) < 0.01

    def test_filter_by_category(self, test_db):
        test_db.log_ci_fix_attempt(
            {
                "run_id": "r1",
                "job_name": "lint",
                "failure_category": "lint",
                "fix_strategy": "ruff",
                "status": "success",
            }
        )
        test_db.log_ci_fix_attempt(
            {
                "run_id": "r2",
                "job_name": "typecheck",
                "failure_category": "typecheck",
                "fix_strategy": "mypy",
                "status": "failed",
            }
        )

        lint_history = test_db.get_ci_fix_history(category="lint")
        assert len(lint_history) == 1


# ---------------------------------------------------------------------------
# TestCIFixValidation
# ---------------------------------------------------------------------------


class TestCIFixValidation:
    """Test local validation helpers."""

    def test_lint_validation_runs_ruff(self, cifix_agent):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            cifix_agent._validate_locally([CIFailureCategory.LINT])

        # Verify ruff check and ruff format --check were called
        call_args_list = [" ".join(str(a) for a in call.args[0]) for call in mock_run.call_args_list]
        assert any("ruff" in args and "check" in args for args in call_args_list)
        assert any("ruff" in args and "format" in args for args in call_args_list)

    def test_typecheck_validation_runs_mypy(self, cifix_agent):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            cifix_agent._validate_locally([CIFailureCategory.TYPECHECK])

        call_args_list = [" ".join(str(a) for a in call.args[0]) for call in mock_run.call_args_list]
        assert any("mypy" in args for args in call_args_list)


# ---------------------------------------------------------------------------
# TestMigration011
# ---------------------------------------------------------------------------


class TestMigration011:
    """Test migration 011 creates the ci_fix_attempts table correctly."""

    def test_migration_version(self, test_db):
        from ortobahn.migrations import _get_schema_version

        version = _get_schema_version(test_db)
        assert version == 16

        # Verify ci_fix_attempts table exists with expected columns
        test_db.fetchall(
            "SELECT id, run_id, gh_run_id, gh_run_url, job_name, failure_category, "
            "error_count, error_codes, fix_strategy, status, files_changed, "
            "branch_name, commit_sha, pr_url, llm_used, input_tokens, "
            "output_tokens, validation_passed, error_message, created_at "
            "FROM ci_fix_attempts LIMIT 1"
        )

    def test_migration_idempotent(self, test_db):
        v1 = run_migrations(test_db)
        v2 = run_migrations(test_db)
        assert v1 == v2 == 16
