"""Tests for git utility functions."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ortobahn.git_utils import (
    BLOCKED_PATTERNS,
    git_cmd,
    is_path_safe,
    read_source_file,
)


class TestIsPathSafe:
    def test_safe_path(self):
        """Regular source files should be considered safe."""
        assert is_path_safe("ortobahn/config.py") is True
        assert is_path_safe("tests/test_backup.py") is True
        assert is_path_safe("pyproject.toml") is True

    def test_dotenv_blocked(self):
        """Paths containing .env should be blocked."""
        assert is_path_safe(".env") is False
        assert is_path_safe(".env.local") is False
        assert is_path_safe("config/.env.production") is False

    def test_git_directory_blocked(self):
        """Paths inside .git/ should be blocked."""
        assert is_path_safe(".git/config") is False
        assert is_path_safe(".git/HEAD") is False

    def test_secret_pattern_blocked(self):
        """Paths containing 'secret' should be blocked."""
        assert is_path_safe("secrets.json") is False
        assert is_path_safe("my_secret_config.yaml") is False

    def test_credential_pattern_blocked(self):
        """Paths containing 'credential' or 'credentials' should be blocked."""
        assert is_path_safe("credentials.json") is False
        assert is_path_safe("credential_store.db") is False

    def test_path_traversal_blocked(self):
        """Paths that escape the project root via .. should be blocked."""
        assert is_path_safe("../../etc/passwd") is False
        assert is_path_safe("../other_project/main.py") is False

    def test_case_insensitive_blocking(self):
        """Blocked pattern matching should be case-insensitive."""
        assert is_path_safe(".ENV") is False
        assert is_path_safe("SECRET_KEY.txt") is False
        assert is_path_safe("CREDENTIALS.json") is False

    def test_blocked_patterns_exist(self):
        """BLOCKED_PATTERNS should contain the expected patterns."""
        assert ".env" in BLOCKED_PATTERNS
        assert ".git/" in BLOCKED_PATTERNS
        assert "secret" in BLOCKED_PATTERNS
        assert "credential" in BLOCKED_PATTERNS


class TestReadSourceFile:
    def test_read_existing_file(self, tmp_path):
        """Should return the file contents for an existing file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        with patch("ortobahn.git_utils.PROJECT_ROOT", tmp_path):
            result = read_source_file("test.py")

        assert result == "print('hello')"

    def test_read_missing_file(self, tmp_path):
        """Should return None for a file that does not exist."""
        with patch("ortobahn.git_utils.PROJECT_ROOT", tmp_path):
            result = read_source_file("nonexistent.py")

        assert result is None

    def test_read_truncates_long_file(self, tmp_path):
        """Should truncate files longer than max_chars."""
        test_file = tmp_path / "big.py"
        content = "x" * 10000
        test_file.write_text(content)

        with patch("ortobahn.git_utils.PROJECT_ROOT", tmp_path):
            result = read_source_file("big.py", max_chars=100)

        assert result is not None
        assert len(result) < 10000
        assert result.endswith("... (truncated)")
        # The first 100 chars should be preserved
        assert result.startswith("x" * 100)

    def test_read_does_not_truncate_short_file(self, tmp_path):
        """Files under max_chars should be returned in full."""
        test_file = tmp_path / "short.py"
        content = "short content"
        test_file.write_text(content)

        with patch("ortobahn.git_utils.PROJECT_ROOT", tmp_path):
            result = read_source_file("short.py", max_chars=8000)

        assert result == content
        assert "truncated" not in result

    def test_read_directory_returns_none(self, tmp_path):
        """Passing a directory path should return None (not a file)."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with patch("ortobahn.git_utils.PROJECT_ROOT", tmp_path):
            result = read_source_file("subdir")

        assert result is None


class TestGitCmd:
    @patch("ortobahn.git_utils.subprocess.run")
    def test_git_cmd_calls_subprocess(self, mock_run):
        """git_cmd should call subprocess.run with the correct arguments."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="clean\n",
            stderr="",
        )

        result = git_cmd("status")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert "status" in args
        assert result.stdout == "clean\n"

    @patch("ortobahn.git_utils.subprocess.run")
    def test_git_cmd_passes_check_flag(self, mock_run):
        """git_cmd should pass check=True by default and respect overrides."""
        mock_run.return_value = MagicMock(returncode=0)

        git_cmd("status", check=False)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["check"] is False

    @patch("ortobahn.git_utils.subprocess.run")
    def test_git_cmd_raises_on_failure(self, mock_run):
        """git_cmd with check=True should raise CalledProcessError on failure."""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "status"],
            stderr="fatal: not a git repo",
        )

        try:
            git_cmd("status")
            raise AssertionError("Expected CalledProcessError")
        except subprocess.CalledProcessError as e:
            assert e.returncode == 1
