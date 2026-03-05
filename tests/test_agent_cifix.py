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
        assert version == 46

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
        assert v1 == v2 == 46


# ---------------------------------------------------------------------------
# TestCICategorizationAdvanced — more log patterns
# ---------------------------------------------------------------------------


class TestCICategorizationAdvanced:
    """Test categorization with realistic multi-line logs and edge cases."""

    def test_categorize_install_failure(self, cifix_agent):
        logs = (
            "Step 3/8 : RUN pip install -e .[dev]\n"
            "ERROR: Could not find a version that satisfies the requirement bogus>=2.0\n"
            "ERROR: No matching distribution found for bogus>=2.0"
        )
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.INSTALL

    def test_categorize_multiple_lint_errors(self, cifix_agent):
        """Multiple ruff errors should still categorize as LINT."""
        logs = (
            "ortobahn/foo.py:10:5: E501 Line too long (120 > 119)\n"
            "ortobahn/foo.py:12:1: F401 'os' imported but unused\n"
            "ortobahn/bar.py:99:1: W291 trailing whitespace\n"
            "Found 3 errors."
        )
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.LINT

    def test_categorize_multiple_pytest_failures(self, cifix_agent):
        logs = (
            "FAILED tests/test_foo.py::test_alpha - AssertionError\n"
            "FAILED tests/test_bar.py::TestGroup::test_beta - KeyError\n"
            "===== 2 failed, 10 passed ====="
        )
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.TEST

    def test_typecheck_takes_priority_over_lint(self, cifix_agent):
        """When logs contain both mypy AND lint patterns, typecheck should win
        because the regex is checked first and mypy errors match the .py:line:col pattern."""
        logs = (
            "ortobahn/db.py:50: error: Incompatible return type [return-value]\n"
            "ortobahn/foo.py:10:5: E501 Line too long"
        )
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.TYPECHECK

    def test_categorize_empty_logs(self, cifix_agent):
        result = cifix_agent._categorize_failure("")
        assert result == CIFailureCategory.UNKNOWN

    def test_categorize_format_with_reformatted(self, cifix_agent):
        logs = "reformatted ortobahn/agents/base.py\n2 files reformatted, 5 files left unchanged."
        result = cifix_agent._categorize_failure(logs)
        assert result == CIFailureCategory.FORMAT


# ---------------------------------------------------------------------------
# TestCIErrorExtractionAdvanced
# ---------------------------------------------------------------------------


class TestCIErrorExtractionAdvanced:
    """Test error extraction with edge cases and empty inputs."""

    def test_extract_no_lint_errors_from_clean_logs(self, cifix_agent):
        logs = "All checks passed!\n0 errors."
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.LINT)
        assert errors == []

    def test_extract_multiple_mypy_errors(self, cifix_agent):
        logs = (
            'ortobahn/db.py:50: error: Incompatible return value type (got "None") [return-value]\n'
            "ortobahn/models.py:120: error: Missing positional argument [call-arg]\n"
            'ortobahn/config.py:10: error: Need type annotation for "x" [var-annotated]'
        )
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.TYPECHECK)
        assert len(errors) == 3
        assert errors[0].file_path == "ortobahn/db.py"
        assert errors[0].line == 50
        assert errors[0].code == "return-value"
        assert errors[1].file_path == "ortobahn/models.py"
        assert errors[2].code == "var-annotated"

    def test_extract_multiple_pytest_failures(self, cifix_agent):
        logs = (
            "FAILED tests/test_db.py::test_create - AssertionError: 1 != 2\n"
            "FAILED tests/test_config.py::TestEnv::test_load - KeyError: 'missing'"
        )
        errors = cifix_agent._extract_error_details(logs, CIFailureCategory.TEST)
        assert len(errors) == 2
        assert errors[0].file_path == "tests/test_db.py"
        assert "test_create" in errors[0].message
        assert errors[1].file_path == "tests/test_config.py"

    def test_extract_unknown_returns_empty(self, cifix_agent):
        """Unknown category does not extract structured errors."""
        errors = cifix_agent._extract_error_details("random output", CIFailureCategory.UNKNOWN)
        assert errors == []

    def test_extract_install_returns_empty(self, cifix_agent):
        """Install category is not structured; extraction returns empty."""
        errors = cifix_agent._extract_error_details("pip install failed", CIFailureCategory.INSTALL)
        assert errors == []


# ---------------------------------------------------------------------------
# TestFetchFailedRuns — mock subprocess for _fetch_failed_runs
# ---------------------------------------------------------------------------


class TestFetchFailedRuns:
    """Test _fetch_failed_runs with various subprocess outcomes."""

    def test_successful_fetch(self, cifix_agent):
        import json as _json

        mock_result = MagicMock()
        mock_result.stdout = _json.dumps(
            [
                {
                    "databaseId": 123,
                    "conclusion": "failure",
                    "headBranch": "main",
                    "url": "https://gh/run/123",
                    "name": "CI",
                },
                {
                    "databaseId": 124,
                    "conclusion": "failure",
                    "headBranch": "main",
                    "url": "https://gh/run/124",
                    "name": "CI",
                },
            ]
        )
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            runs = cifix_agent._fetch_failed_runs()

        assert runs is not None
        assert len(runs) == 2
        assert runs[0]["databaseId"] == 123

    def test_empty_stdout_returns_empty_list(self, cifix_agent):
        mock_result = MagicMock()
        mock_result.stdout = "   "
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            runs = cifix_agent._fetch_failed_runs()

        assert runs == []

    def test_gh_not_installed_returns_none(self, cifix_agent):
        with patch("subprocess.run", side_effect=OSError("No such file or directory: 'gh'")):
            runs = cifix_agent._fetch_failed_runs()

        assert runs is None

    def test_subprocess_error_returns_empty(self, cifix_agent):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")):
            runs = cifix_agent._fetch_failed_runs()

        assert runs == []

    def test_invalid_json_returns_empty(self, cifix_agent):
        mock_result = MagicMock()
        mock_result.stdout = "not-json-at-all"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            runs = cifix_agent._fetch_failed_runs()

        assert runs == []

    def test_timeout_returns_empty(self, cifix_agent):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            runs = cifix_agent._fetch_failed_runs()

        assert runs == []


# ---------------------------------------------------------------------------
# TestApplyLLMChanges — parsing LLM JSON response
# ---------------------------------------------------------------------------


class TestApplyLLMChanges:
    """Test _apply_llm_changes parses and writes files correctly."""

    def test_valid_json_with_changes(self, cifix_agent, tmp_path):
        import json as _json

        from ortobahn.agents import cifix as cifix_mod

        llm_text = _json.dumps(
            {
                "changes": [
                    {"file_path": "ortobahn/dummy_fix.py", "content": "# fixed\nprint('hello')"},
                ]
            }
        )

        with patch.object(cifix_mod, "PROJECT_ROOT", tmp_path):
            with patch.object(cifix_mod, "is_path_safe", return_value=True):
                changed = cifix_agent._apply_llm_changes(llm_text)

        assert len(changed) == 1
        assert "ortobahn/dummy_fix.py" in changed
        assert (tmp_path / "ortobahn" / "dummy_fix.py").read_text() == "# fixed\nprint('hello')"

    def test_json_wrapped_in_markdown_fences(self, cifix_agent, tmp_path):
        import json as _json

        from ortobahn.agents import cifix as cifix_mod

        inner = _json.dumps({"changes": [{"file_path": "ortobahn/x.py", "content": "pass"}]})
        llm_text = f"Here is the fix:\n```json\n{inner}\n```\nDone."

        with patch.object(cifix_mod, "PROJECT_ROOT", tmp_path):
            with patch.object(cifix_mod, "is_path_safe", return_value=True):
                changed = cifix_agent._apply_llm_changes(llm_text)

        assert len(changed) == 1

    def test_invalid_json_returns_empty(self, cifix_agent):
        changed = cifix_agent._apply_llm_changes("This is not JSON at all.")
        assert changed == []

    def test_unsafe_path_skipped(self, cifix_agent, tmp_path):
        import json as _json

        from ortobahn.agents import cifix as cifix_mod

        llm_text = _json.dumps({"changes": [{"file_path": ".env", "content": "SECRET=bad"}]})

        with patch.object(cifix_mod, "PROJECT_ROOT", tmp_path):
            # is_path_safe with real implementation should block .env
            changed = cifix_agent._apply_llm_changes(llm_text)

        assert changed == []

    def test_empty_changes_array(self, cifix_agent):
        import json as _json

        llm_text = _json.dumps({"changes": []})
        changed = cifix_agent._apply_llm_changes(llm_text)
        assert changed == []

    def test_missing_content_key_skipped(self, cifix_agent, tmp_path):
        import json as _json

        from ortobahn.agents import cifix as cifix_mod

        llm_text = _json.dumps({"changes": [{"file_path": "ortobahn/x.py"}]})

        with patch.object(cifix_mod, "PROJECT_ROOT", tmp_path):
            with patch.object(cifix_mod, "is_path_safe", return_value=True):
                changed = cifix_agent._apply_llm_changes(llm_text)

        assert changed == []


# ---------------------------------------------------------------------------
# TestValidationLocally — advanced scenarios
# ---------------------------------------------------------------------------


class TestValidationLocallyAdvanced:
    """Test _validate_locally with failures, timeouts, and multiple categories."""

    def test_lint_validation_failure(self, cifix_agent):
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stdout = "ortobahn/x.py:1:1: E501 too long"
        mock_fail.stderr = ""

        with patch("subprocess.run", return_value=mock_fail):
            passed, output = cifix_agent._validate_locally([CIFailureCategory.LINT])

        assert passed is False
        assert "ruff" in output.lower()

    def test_test_validation_passes(self, cifix_agent):
        mock_pass = MagicMock()
        mock_pass.returncode = 0
        mock_pass.stdout = "10 passed"
        mock_pass.stderr = ""

        with patch("subprocess.run", return_value=mock_pass):
            passed, output = cifix_agent._validate_locally([CIFailureCategory.TEST])

        assert passed is True
        assert "pytest passed" in output

    def test_test_validation_failure(self, cifix_agent):
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stdout = "FAILED tests/test_x.py::test_y"
        mock_fail.stderr = ""

        with patch("subprocess.run", return_value=mock_fail):
            passed, output = cifix_agent._validate_locally([CIFailureCategory.TEST])

        assert passed is False
        assert "pytest failed" in output

    def test_subprocess_exception_marks_failure(self, cifix_agent):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=60)):
            passed, output = cifix_agent._validate_locally([CIFailureCategory.LINT])

        assert passed is False

    def test_no_categories_no_checks(self, cifix_agent):
        passed, output = cifix_agent._validate_locally([])
        assert passed is True
        assert output == "No checks run"

    def test_format_category_also_checks_lint(self, cifix_agent):
        """FORMAT triggers both ruff check and ruff format."""
        mock_pass = MagicMock()
        mock_pass.returncode = 0
        mock_pass.stdout = ""
        mock_pass.stderr = ""

        with patch("subprocess.run", return_value=mock_pass) as mock_run:
            passed, output = cifix_agent._validate_locally([CIFailureCategory.FORMAT])

        assert passed is True
        call_args_list = [" ".join(str(a) for a in call.args[0]) for call in mock_run.call_args_list]
        assert any("ruff" in args and "check" in args for args in call_args_list)
        assert any("ruff" in args and "format" in args for args in call_args_list)


# ---------------------------------------------------------------------------
# TestCreatePR — PR creation via subprocess
# ---------------------------------------------------------------------------


class TestCreatePR:
    """Test _create_pr subprocess interactions."""

    def test_successful_pr_creation(self, cifix_agent):
        from ortobahn.models import CIFailure, FixAttempt
        from ortobahn.models import CIFailureCategory as CFC

        failure = CIFailure(
            gh_run_id=999,
            gh_run_url="https://github.com/org/repo/actions/runs/999",
            category=CFC.LINT,
            errors=[],
        )
        fix = FixAttempt(
            strategy="ruff auto-fix",
            files_changed=["ortobahn/foo.py"],
            llm_used=False,
        )

        mock_result = MagicMock()
        mock_result.stdout = "https://github.com/org/repo/pull/42\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            pr_url = cifix_agent._create_pr("cifix/lint/999", failure, fix)

        assert pr_url == "https://github.com/org/repo/pull/42"

    def test_pr_creation_failure_returns_empty(self, cifix_agent):
        import subprocess

        from ortobahn.models import CIFailure, FixAttempt
        from ortobahn.models import CIFailureCategory as CFC

        failure = CIFailure(gh_run_id=999, category=CFC.LINT, errors=[])
        fix = FixAttempt(strategy="ruff auto-fix", files_changed=["x.py"])

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")):
            pr_url = cifix_agent._create_pr("cifix/lint/999", failure, fix)

        assert pr_url == ""


# ---------------------------------------------------------------------------
# TestRunOrchestration — full run() with mocked internals
# ---------------------------------------------------------------------------


class TestRunOrchestration:
    """Test the full run() method with various mocked scenarios."""

    def test_run_lint_fix_success(self, cifix_agent):
        """Full lint fix: fetch failure -> categorize -> fix -> validate -> commit -> PR."""
        from ortobahn.models import FixAttempt

        failed_runs = [
            {"databaseId": 100, "url": "https://gh/run/100", "name": "lint-job"},
        ]
        raw_logs = "ortobahn/foo.py:10:5: E501 Line too long (120 > 119)"

        lint_fix = FixAttempt(
            strategy="ruff auto-fix",
            files_changed=["ortobahn/foo.py"],
            llm_used=False,
            tokens_used=0,
        )

        with (
            patch.object(cifix_agent, "_fetch_failed_runs", return_value=failed_runs),
            patch.object(cifix_agent, "_fetch_run_logs", return_value=raw_logs),
            patch.object(cifix_agent, "_fix_lint", return_value=lint_fix),
            patch.object(cifix_agent, "_validate_locally", return_value=(True, "all passed")),
            patch("ortobahn.agents.cifix.current_branch", return_value="main"),
            patch("ortobahn.agents.cifix.create_branch"),
            patch("ortobahn.agents.cifix.switch_branch"),
            patch("ortobahn.agents.cifix.commit_all", return_value="abc123"),
            patch("ortobahn.agents.cifix.push_branch"),
            patch.object(cifix_agent, "_create_pr", return_value="https://github.com/pull/1"),
        ):
            result = cifix_agent.run(run_id="run-lint-ok")

        assert result.status == "fixed"
        assert result.validation_passed is True
        assert result.commit_sha == "abc123"
        assert result.pr_url == "https://github.com/pull/1"

    def test_run_validation_fails_marks_failed(self, cifix_agent):
        """When validation fails after fix, status should be 'failed'."""
        from ortobahn.models import FixAttempt

        failed_runs = [{"databaseId": 200, "url": "", "name": "test-job"}]
        raw_logs = "FAILED tests/test_x.py::test_y - AssertionError"

        mock_fix = FixAttempt(
            strategy="test fix via LLM",
            files_changed=["tests/test_x.py"],
            llm_used=True,
            tokens_used=500,
        )

        with (
            patch.object(cifix_agent, "_fetch_failed_runs", return_value=failed_runs),
            patch.object(cifix_agent, "_fetch_run_logs", return_value=raw_logs),
            patch.object(cifix_agent, "_fix_tests", return_value=mock_fix),
            patch.object(cifix_agent, "_validate_locally", return_value=(False, "pytest failed")),
            patch("ortobahn.agents.cifix.current_branch", return_value="main"),
            patch("ortobahn.agents.cifix.create_branch"),
            patch("ortobahn.agents.cifix.switch_branch"),
        ):
            result = cifix_agent.run(run_id="run-test-fail")

        assert result.status == "failed"
        assert result.validation_passed is False

    def test_run_no_logs_returns_failed(self, cifix_agent):
        """If log fetch fails, status should be 'failed' with log error."""
        failed_runs = [{"databaseId": 300, "url": "", "name": "job"}]

        with (
            patch.object(cifix_agent, "_fetch_failed_runs", return_value=failed_runs),
            patch.object(cifix_agent, "_fetch_run_logs", return_value=""),
        ):
            result = cifix_agent.run(run_id="run-nologs")

        assert result.status == "failed"
        assert "logs" in result.error.lower() or "logs" in result.summary.lower()
