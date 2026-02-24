"""Tests for git blame and recent changes functions in git_utils."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ortobahn.git_utils import (
    correlate_failures_with_changes,
    get_changed_files_in_commit,
    get_recent_changes,
    git_blame_file,
)

# ---------------------------------------------------------------------------
# git_blame_file
# ---------------------------------------------------------------------------


class TestGitBlameFile:
    def test_git_blame_file(self, monkeypatch):
        """Parse porcelain blame output."""
        sha1 = "a" * 40
        sha2 = "b" * 40
        blame_output = (
            f"{sha1} 1 1 1\n"
            "author Alice\n"
            "author-time 1700000000\n"
            "\tdef hello():\n"
            f"{sha2} 2 2 1\n"
            "author Bob\n"
            "author-time 1700100000\n"
            "\t    return 'world'\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = blame_output
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            entries = git_blame_file("ortobahn/foo.py")

        assert len(entries) == 2
        assert entries[0]["author"] == "Alice"
        assert entries[0]["content"] == "def hello():"
        assert entries[0]["line"] == 1
        assert entries[1]["author"] == "Bob"
        assert entries[1]["content"] == "    return 'world'"

    def test_git_blame_line_range(self, monkeypatch):
        """Blame with line range passes -L flag."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result) as mock_cmd:
            git_blame_file("ortobahn/foo.py", line_start=10, line_end=20)

        # Verify -L flag was passed
        call_args = mock_cmd.call_args
        args = call_args[0] if call_args[0] else ()
        assert "-L" in args
        assert "10,20" in args

    def test_blame_file_not_found(self):
        """Handle missing file gracefully."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        mock_result.stderr = "fatal: no such path 'nonexistent.py'"

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            entries = git_blame_file("nonexistent.py")

        assert entries == []

    def test_blame_timeout_handling(self):
        """Subprocess timeout returns empty list."""
        with patch("ortobahn.git_utils.git_cmd", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            entries = git_blame_file("ortobahn/foo.py")

        assert entries == []


# ---------------------------------------------------------------------------
# get_recent_changes
# ---------------------------------------------------------------------------


class TestGetRecentChanges:
    def test_get_recent_changes(self):
        """Parse git log output."""
        log_output = (
            "abc1234|Alice|2024-01-15 10:00:00 +0000|Fix bug in config\n"
            "def5678|Bob|2024-01-14 09:00:00 +0000|Add new feature\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = log_output
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            changes = get_recent_changes("ortobahn/config.py")

        assert len(changes) == 2
        assert changes[0]["sha"] == "abc1234"
        assert changes[0]["author"] == "Alice"
        assert changes[0]["message"] == "Fix bug in config"
        assert changes[1]["sha"] == "def5678"

    def test_recent_changes_empty(self):
        """No recent changes returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            changes = get_recent_changes("ortobahn/foo.py")

        assert changes == []

    def test_recent_changes_timeout(self):
        """Timeout returns empty list."""
        with patch("ortobahn.git_utils.git_cmd", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            changes = get_recent_changes("ortobahn/foo.py")

        assert changes == []

    def test_recent_changes_error(self):
        """Non-zero exit returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            changes = get_recent_changes("ortobahn/foo.py")

        assert changes == []


# ---------------------------------------------------------------------------
# get_changed_files_in_commit
# ---------------------------------------------------------------------------


class TestGetChangedFilesInCommit:
    def test_get_changed_files_in_commit(self):
        """Parse diff-tree output."""
        diff_output = "ortobahn/config.py\nortobahn/models.py\ntests/test_config.py\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = diff_output
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            files = get_changed_files_in_commit("abc1234")

        assert len(files) == 3
        assert "ortobahn/config.py" in files
        assert "tests/test_config.py" in files

    def test_changed_files_error(self):
        """Non-zero exit returns empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            files = get_changed_files_in_commit("abc1234")

        assert files == []

    def test_changed_files_timeout(self):
        """Timeout returns empty list."""
        with patch("ortobahn.git_utils.git_cmd", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            files = get_changed_files_in_commit("abc1234")

        assert files == []


# ---------------------------------------------------------------------------
# correlate_failures_with_changes
# ---------------------------------------------------------------------------


class TestCorrelateFailures:
    def test_correlate_failures(self):
        """Correlate error objects with recent changes."""

        class MockError:
            def __init__(self, fp):
                self.file_path = fp

        errors = [MockError("ortobahn/config.py"), MockError("ortobahn/models.py")]

        log_output_config = "sha1|Alice|2024-01-15|Fix config\n"
        log_output_models = "sha2|Bob|2024-01-14|Update models\n"

        def mock_git_cmd(*args, check=True):
            result = MagicMock()
            result.returncode = 0
            # Determine which file by looking at args
            if "ortobahn/config.py" in args:
                result.stdout = log_output_config
            elif "ortobahn/models.py" in args:
                result.stdout = log_output_models
            else:
                result.stdout = ""
            result.stderr = ""
            return result

        with patch("ortobahn.git_utils.git_cmd", side_effect=mock_git_cmd):
            correlation = correlate_failures_with_changes(errors)

        assert "ortobahn/config.py" in correlation
        assert "ortobahn/models.py" in correlation
        assert correlation["ortobahn/config.py"][0]["sha"] == "sha1"
        assert correlation["ortobahn/models.py"][0]["sha"] == "sha2"

    def test_correlate_empty_errors(self):
        """Empty error list returns empty dict."""
        result = correlate_failures_with_changes([])
        assert result == {}

    def test_correlate_no_changes(self):
        """Files with no recent changes are not in result."""

        class MockError:
            def __init__(self, fp):
                self.file_path = fp

        errors = [MockError("ortobahn/foo.py")]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            correlation = correlate_failures_with_changes(errors)

        assert correlation == {}

    def test_correlate_with_dict_errors(self):
        """Also accepts dict-style errors."""
        errors = [{"file_path": "ortobahn/config.py"}]

        log_output = "sha1|Alice|2024-01-15|Fix config\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = log_output
        mock_result.stderr = ""

        with patch("ortobahn.git_utils.git_cmd", return_value=mock_result):
            correlation = correlate_failures_with_changes(errors)

        assert "ortobahn/config.py" in correlation
