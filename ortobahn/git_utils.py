"""Shared git operations used by CTO, CI-fix, and other code-aware agents."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

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


def create_pr(branch_name: str, title: str, body: str, base: str = "main") -> str:
    """Create a GitHub pull request using the gh CLI. Returns the PR URL."""
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch_name, "--base", base],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        pr_url = result.stdout.strip()
        logger.info("Created PR: %s", pr_url)
        return pr_url
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning("Failed to create PR: %s", e)
        return ""


def enable_auto_merge(pr_url: str) -> bool:
    """Enable auto-merge (squash) on a pull request. Returns True on success."""
    try:
        subprocess.run(
            ["gh", "pr", "merge", pr_url, "--auto", "--squash"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        logger.info("Enabled auto-merge on %s", pr_url)
        return True
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning("Failed to enable auto-merge: %s", e)
        return False


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


# ---------------------------------------------------------------------------
# Git blame and recent changes
# ---------------------------------------------------------------------------


def git_blame_file(file_path: str, line_start: int = 1, line_end: int | None = None) -> list[dict[str, Any]]:
    """Get blame info for a file (or line range).

    Returns list of dicts with: commit, author, date, line, content.
    """
    args = ["blame", "--porcelain"]
    if line_end is not None:
        args.extend(["-L", f"{line_start},{line_end}"])
    elif line_start > 1:
        args.extend(["-L", f"{line_start},"])
    args.append(file_path)

    try:
        result = git_cmd(*args, check=False)
        if result.returncode != 0:
            logger.warning("git blame failed for %s: %s", file_path, result.stderr.strip())
            return []
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("git blame error for %s: %s", file_path, e)
        return []

    return _parse_blame_porcelain(result.stdout)


def _parse_blame_porcelain(output: str) -> list[dict[str, Any]]:
    """Parse git blame --porcelain output into structured dicts."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for raw_line in output.splitlines():
        # Header line: <sha> <orig_line> <final_line> [<num_lines>]
        header_match = re.match(r"^([0-9a-f]{40})\s+(\d+)\s+(\d+)", raw_line)
        if header_match:
            if current.get("commit"):
                entries.append(current)
            current = {
                "commit": header_match.group(1),
                "line": int(header_match.group(3)),
                "author": "",
                "date": "",
                "content": "",
            }
            continue

        if raw_line.startswith("author "):
            current["author"] = raw_line[7:]
        elif raw_line.startswith("author-time "):
            # Convert unix timestamp to ISO date
            try:
                from datetime import datetime, timezone

                ts = int(raw_line.split()[1])
                current["date"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                pass
        elif raw_line.startswith("\t"):
            current["content"] = raw_line[1:]

    if current.get("commit"):
        entries.append(current)

    return entries


def get_recent_changes(file_path: str, days: int = 7, limit: int = 10) -> list[dict[str, str]]:
    """Get recent commits that modified a file.

    Returns list of dicts with: sha, author, date, message.
    """
    try:
        result = git_cmd(
            "log",
            f"--since={days} days ago",
            f"--max-count={limit}",
            "--format=%H|%an|%ai|%s",
            "--",
            file_path,
            check=False,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("git log error for %s: %s", file_path, e)
        return []

    commits: list[dict[str, str]] = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append(
            {
                "sha": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            }
        )
    return commits


def get_changed_files_in_commit(sha: str) -> list[str]:
    """Get list of files changed in a specific commit."""
    try:
        result = git_cmd(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            sha,
            check=False,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("git diff-tree error for %s: %s", sha, e)
        return []

    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def correlate_failures_with_changes(errors: list[Any], days: int = 7) -> dict[str, list[dict[str, str]]]:
    """Given a list of CIError-like objects, find recent commits that changed the failing files.

    Returns dict mapping file_path -> list of recent commits that touched it.
    """
    result: dict[str, list[dict[str, str]]] = {}
    seen_files: set[str] = set()

    for err in errors:
        file_path = getattr(err, "file_path", "") if not isinstance(err, dict) else err.get("file_path", "")
        if not file_path or file_path in seen_files:
            continue
        seen_files.add(file_path)

        try:
            changes = get_recent_changes(file_path, days=days)
            if changes:
                result[file_path] = changes
        except Exception as e:
            logger.warning("Failed to get recent changes for %s: %s", file_path, e)

    return result
