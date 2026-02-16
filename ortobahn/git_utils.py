"""Shared git operations used by CTO, CI-fix, and other code-aware agents."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("ortobahn.git_utils")

# Project root: one level up from this file (ortobahn/git_utils.py -> ortobahn/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files and directories that must never be written to
BLOCKED_PATTERNS = {".env", ".git/", ".git", "secret", "credential", "credentials"}


def git_cmd(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the project root."""
    cmd = ["git", "-C", str(PROJECT_ROOT)] + list(args)
    logger.debug("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=30)


def current_branch() -> str:
    """Return the name of the currently checked-out branch."""
    result = git_cmd("rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip()


def create_branch(name: str) -> None:
    """Create and switch to a new branch."""
    git_cmd("checkout", "-b", name)
    logger.info("Created branch %s", name)


def switch_branch(name: str) -> None:
    """Switch to an existing branch."""
    git_cmd("checkout", name)
    logger.debug("Switched to branch %s", name)


def delete_branch(name: str) -> None:
    """Delete a local branch (best-effort, does not raise on failure)."""
    git_cmd("branch", "-D", name, check=False)
    logger.info("Deleted branch %s", name)


def commit_all(message: str) -> str:
    """Stage all changes, commit, and return the new commit SHA."""
    git_cmd("add", "-A")
    git_cmd("commit", "-m", message)
    result = git_cmd("rev-parse", "HEAD")
    sha = result.stdout.strip()
    logger.info("Committed %s: %s", sha[:8], message.split("\n", 1)[0])
    return sha


def push_branch(branch: str) -> None:
    """Push a branch to the origin remote."""
    git_cmd("push", "origin", branch)
    logger.info("Pushed branch %s to origin", branch)


def is_path_safe(file_path: str) -> bool:
    """Check that a file path is within the project and not blocked."""
    resolved = (PROJECT_ROOT / file_path).resolve()

    # Must stay within the project root
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        logger.warning("Path escapes project root: %s", file_path)
        return False

    # Check against blocked patterns
    lower = file_path.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lower:
            logger.warning("Path matches blocked pattern '%s': %s", pattern, file_path)
            return False

    return True


def read_source_file(rel_path: str, max_chars: int = 8000) -> str | None:
    """Read a project file and return its contents, truncated if needed.

    Returns None if the file does not exist or cannot be read.
    """
    full_path = PROJECT_ROOT / rel_path
    if not full_path.is_file():
        return None
    try:
        content = full_path.read_text(encoding="utf-8")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"
        return content
    except Exception as e:
        logger.warning("Could not read %s: %s", rel_path, e)
        return None
