"""Tests for the intelligent test selector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ortobahn.test_selector import format_pytest_args, get_changed_files, select_tests


class TestSelectTests:
    def test_no_changes_returns_empty(self):
        """No changed files means run all tests."""
        result = select_tests([])
        assert result == []

    def test_infrastructure_change_returns_empty(self):
        """pyproject.toml change means run all tests."""
        result = select_tests(["pyproject.toml"])
        assert result == []

    def test_github_dir_change_returns_empty(self):
        """Changes under .github/ mean run all tests."""
        result = select_tests([".github/workflows/ci.yml"])
        assert result == []

    def test_dockerfile_change_returns_empty(self):
        """Dockerfile change means run all tests."""
        result = select_tests(["Dockerfile"])
        assert result == []

    def test_makefile_change_returns_empty(self):
        """Makefile change means run all tests."""
        result = select_tests(["Makefile"])
        assert result == []

    def test_agent_change_selects_agent_test(self):
        """Changing ceo.py selects test_agent_ceo.py."""
        result = select_tests(["ortobahn/agents/ceo.py"])
        assert "tests/test_agent_ceo.py" in result

    def test_always_includes_critical_tests(self):
        """Config and DB tests always run."""
        result = select_tests(["ortobahn/agents/ceo.py"])
        assert "tests/test_config.py" in result
        assert "tests/test_db.py" in result

    def test_test_file_change_includes_itself(self):
        """Changing a test file includes that test file."""
        result = select_tests(["tests/test_memory.py"])
        assert "tests/test_memory.py" in result

    def test_directory_prefix_matching(self):
        """Changes in ortobahn/web/ select web tests."""
        result = select_tests(["ortobahn/web/routes/glass.py"])
        assert "tests/test_web.py" in result

    def test_directory_prefix_matching_onboard(self):
        """Changes in ortobahn/web/ also select onboard API tests."""
        result = select_tests(["ortobahn/web/routes/onboard.py"])
        assert "tests/test_onboard_api.py" in result

    def test_multiple_changes_union(self):
        """Multiple file changes produce union of test files."""
        result = select_tests(["ortobahn/agents/ceo.py", "ortobahn/llm.py"])
        assert "tests/test_agent_ceo.py" in result
        assert "tests/test_llm.py" in result
        assert "tests/test_config.py" in result  # always-run

    def test_orchestrator_selects_pipeline(self):
        """Orchestrator change selects both orchestrator and pipeline tests."""
        result = select_tests(["ortobahn/orchestrator.py"])
        assert "tests/test_orchestrator.py" in result
        assert "tests/test_pipeline.py" in result

    def test_migrations_selects_both(self):
        """Migration change selects both migrations and db tests."""
        result = select_tests(["ortobahn/migrations.py"])
        assert "tests/test_migrations.py" in result
        assert "tests/test_db.py" in result

    def test_conventional_fallback(self, tmp_path, monkeypatch):
        """Unknown module uses convention test_<module>.py if it exists."""
        # Create a fake test file so Path.exists() returns True
        fake_tests = tmp_path / "tests"
        fake_tests.mkdir()
        (fake_tests / "test_healthcheck.py").touch()
        # Also create critical test files so they pass the exists() filter
        (fake_tests / "test_config.py").touch()
        (fake_tests / "test_db.py").touch()
        # Also create enough total test files so we don't hit the 60% threshold
        for i in range(10):
            (fake_tests / f"test_extra_{i}.py").touch()

        monkeypatch.chdir(tmp_path)
        result = select_tests(["ortobahn/healthcheck.py"])
        assert "tests/test_healthcheck.py" in result

    def test_many_changes_runs_all(self, tmp_path, monkeypatch):
        """If >60% of tests selected, run all instead."""
        # Create a small set of total tests so we can easily exceed 60%
        fake_tests = tmp_path / "tests"
        fake_tests.mkdir()
        (fake_tests / "test_config.py").touch()
        (fake_tests / "test_db.py").touch()
        (fake_tests / "test_a.py").touch()
        (fake_tests / "test_b.py").touch()
        (fake_tests / "test_c.py").touch()
        # 5 total tests, ALWAYS_RUN gives us 2, adding test_a, test_b, test_c = 5 = 100% > 60%

        monkeypatch.chdir(tmp_path)
        result = select_tests(["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"])
        assert result == []  # Too many, run all

    def test_only_existing_files_returned(self, tmp_path, monkeypatch):
        """Non-existent test files are filtered out."""
        fake_tests = tmp_path / "tests"
        fake_tests.mkdir()
        (fake_tests / "test_config.py").touch()
        (fake_tests / "test_db.py").touch()
        # Don't create test_agent_ceo.py — it should be filtered out
        for i in range(10):
            (fake_tests / f"test_extra_{i}.py").touch()

        monkeypatch.chdir(tmp_path)
        result = select_tests(["ortobahn/agents/ceo.py"])
        assert "tests/test_agent_ceo.py" not in result

    def test_none_changed_files_calls_get_changed(self, monkeypatch):
        """Passing None for changed_files invokes get_changed_files."""
        monkeypatch.setattr(
            "ortobahn.test_selector.get_changed_files",
            lambda base_ref: [],
        )
        result = select_tests(None)
        assert result == []

    def test_slack_integration_mapping(self):
        """Slack integration change selects slack test."""
        result = select_tests(["ortobahn/integrations/slack.py"])
        assert "tests/test_slack.py" in result


class TestGetChangedFiles:
    def test_returns_file_list(self, monkeypatch):
        """Mocked git diff returns file list."""
        mock_result = MagicMock()
        mock_result.stdout = "ortobahn/agents/ceo.py\n" + "ortobahn/llm.py\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = get_changed_files("HEAD~1")
            assert result == ["ortobahn/agents/ceo.py", "ortobahn/llm.py"]
            mock_run.assert_called_once_with(
                ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

    def test_handles_git_failure(self, monkeypatch):
        """Git failure returns empty list."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = get_changed_files("HEAD~1")
            assert result == []

    def test_handles_timeout(self, monkeypatch):
        """Git timeout returns empty list."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = get_changed_files("HEAD~1")
            assert result == []

    def test_handles_file_not_found(self, monkeypatch):
        """Missing git binary returns empty list."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = get_changed_files("HEAD~1")
            assert result == []

    def test_empty_output(self, monkeypatch):
        """Empty git output returns empty list."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = get_changed_files("HEAD~1")
            assert result == []

    def test_custom_base_ref(self, monkeypatch):
        """Custom base ref is passed to git."""
        mock_result = MagicMock()
        mock_result.stdout = "foo.py\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            get_changed_files("origin/main")
            mock_run.assert_called_once_with(
                ["git", "diff", "--name-only", "origin/main", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )


class TestFormatPytestArgs:
    def test_empty_returns_empty(self):
        assert format_pytest_args([]) == ""

    def test_formats_paths(self):
        result = format_pytest_args(["tests/test_a.py", "tests/test_b.py"])
        assert "tests/test_a.py" in result
        assert "tests/test_b.py" in result

    def test_single_path(self):
        result = format_pytest_args(["tests/test_config.py"])
        assert result == "tests/test_config.py"

    def test_space_separated(self):
        result = format_pytest_args(["tests/test_a.py", "tests/test_b.py"])
        assert result == "tests/test_a.py tests/test_b.py"
