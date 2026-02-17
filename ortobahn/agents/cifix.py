"""CI Fix Agent — automatically diagnoses and fixes CI failures."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any

from ortobahn.agents.base import BaseAgent
from ortobahn.git_utils import (
    PROJECT_ROOT,
    commit_all,
    create_branch,
    current_branch,
    delete_branch,
    git_cmd,
    is_path_safe,
    push_branch,
    read_source_file,
    switch_branch,
)
from ortobahn.memory import MemoryStore
from ortobahn.models import (
    AgentMemory,
    CIError,
    CIFailure,
    CIFailureCategory,
    CIFixResult,
    FixAttempt,
    MemoryCategory,
    MemoryType,
)

logger = logging.getLogger("ortobahn.cifix")


class CIFixAgent(BaseAgent):
    name = "cifix"
    prompt_file = "cifix.txt"
    thinking_budget = 10_000

    def __init__(self, db, api_key: str, model: str = "claude-sonnet-4-5-20250929", max_tokens: int = 16384, **kwargs):
        super().__init__(db, api_key, model, max_tokens, **kwargs)

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------

    def run(self, run_id: str, auto_pr: bool = True, **kwargs: Any) -> CIFixResult:
        """Diagnose the latest CI failure and attempt an automated fix."""

        # 1. Fetch latest failed CI runs
        failed_runs = self._fetch_failed_runs()
        if failed_runs is None:
            logger.warning("gh CLI not available — skipping CI fix")
            self.log_decision(
                run_id=run_id,
                input_summary="Checked for CI failures",
                output_summary="gh CLI not available",
            )
            return CIFixResult(status="skipped", summary="gh CLI not available")
        if not failed_runs:
            logger.info("No failed CI runs found")
            self.log_decision(
                run_id=run_id,
                input_summary="Checked for CI failures",
                output_summary="No failures found",
            )
            return CIFixResult(status="no_failures", summary="No failed CI runs found")

        run_info = failed_runs[0]
        gh_run_id = run_info["databaseId"]
        gh_run_url = run_info.get("url", "")

        # 2. Fetch logs
        raw_logs = self._fetch_run_logs(gh_run_id)
        if not raw_logs:
            logger.warning("Could not fetch logs for run %s", gh_run_id)
            return CIFixResult(
                status="failed",
                error="Could not fetch CI logs",
                summary=f"Failed to retrieve logs for run {gh_run_id}",
            )

        # 3. Categorize the failure
        category = self._categorize_failure(raw_logs)

        # 4. Extract structured errors
        errors = self._extract_error_details(raw_logs, category)

        # Build the CIFailure record
        failure = CIFailure(
            gh_run_id=gh_run_id,
            gh_run_url=gh_run_url,
            job_name=run_info.get("name", ""),
            category=category,
            errors=errors,
            raw_logs=raw_logs[-3000:],
        )

        # 5. Check memory for past fix patterns
        memory_context = self.get_memory_context()
        if memory_context:
            logger.info("Found past fix patterns in memory")

        # 6. Apply fix based on category tier
        original_branch = current_branch()
        branch_name = f"cifix/{category.value}/{gh_run_id}"
        fix_attempt: FixAttempt | None = None

        try:
            # Switch to main before branching
            if original_branch != "main":
                switch_branch("main")
            create_branch(branch_name)

            # Tier 1: deterministic auto-fix (no LLM)
            if category in (CIFailureCategory.LINT, CIFailureCategory.FORMAT):
                fix_attempt = self._fix_lint()

            # Tier 2: targeted LLM fix
            elif category == CIFailureCategory.TYPECHECK:
                fix_attempt = self._fix_typecheck(errors)

            # Tier 3: complex LLM fix
            elif category == CIFailureCategory.TEST:
                fix_attempt = self._fix_tests(errors)

            # Unknown / install — attempt LLM diagnosis
            else:
                fix_attempt = self._fix_unknown(errors, raw_logs)

            if not fix_attempt or not fix_attempt.files_changed:
                raise RuntimeError("Fix produced no file changes")

            # 7. Validate locally
            categories_to_check = [category]
            if category in (CIFailureCategory.LINT, CIFailureCategory.FORMAT):
                categories_to_check = [CIFailureCategory.LINT, CIFailureCategory.FORMAT]

            validation_passed, validation_output = self._validate_locally(categories_to_check)

            # 8. If valid, commit and optionally create PR
            commit_sha = ""
            pr_url = ""
            if validation_passed:
                commit_msg = (
                    f"cifix: auto-fix {category.value} errors from run #{gh_run_id}\n\n"
                    f"Strategy: {fix_attempt.strategy}\n"
                    f"Files: {', '.join(fix_attempt.files_changed)}"
                )
                commit_sha = commit_all(commit_msg)

                if auto_pr:
                    push_branch(branch_name)
                    pr_url = self._create_pr(branch_name, failure, fix_attempt)

            # Switch back to main
            switch_branch("main")

            # 9. Store memory and record attempt
            status = "fixed" if validation_passed else "failed"
            self._store_memory(run_id, failure, fix_attempt, validation_passed)
            self._record_fix_attempt(
                run_id=run_id,
                failure=failure,
                fix_attempt=fix_attempt,
                success=validation_passed,
                branch_name=branch_name,
                commit_sha=commit_sha,
                pr_url=pr_url,
            )

            self.log_decision(
                run_id=run_id,
                input_summary=f"CI run #{gh_run_id} failed ({category.value}, {len(errors)} errors)",
                output_summary=f"{status}: {fix_attempt.strategy}, {len(fix_attempt.files_changed)} files changed",
            )

            return CIFixResult(
                failure=failure,
                status=status,
                fix_attempt=fix_attempt,
                branch_name=branch_name,
                commit_sha=commit_sha,
                pr_url=pr_url,
                validation_passed=validation_passed,
                summary=f"{'Fixed' if validation_passed else 'Attempted fix for'} "
                f"{category.value} errors: {fix_attempt.strategy}",
            )

        except Exception as e:
            logger.error("CI fix failed: %s", e)

            # Clean up: switch back to main, delete branch
            try:
                cur = current_branch()
                if cur != "main":
                    switch_branch("main")
                    delete_branch(branch_name)
            except Exception:
                pass

            if fix_attempt:
                self._store_memory(run_id, failure, fix_attempt, False)
                self._record_fix_attempt(
                    run_id=run_id,
                    failure=failure,
                    fix_attempt=fix_attempt,
                    success=False,
                    branch_name=branch_name,
                    error=str(e),
                )

            self.log_decision(
                run_id=run_id,
                input_summary=f"CI run #{gh_run_id} failed ({category.value})",
                output_summary=f"Error: {str(e)[:100]}",
                reasoning=str(e)[:300],
            )

            return CIFixResult(
                failure=failure,
                status="failed",
                fix_attempt=fix_attempt,
                error=str(e)[:300],
                summary=f"Failed to fix {category.value} errors: {e}",
            )

    # ------------------------------------------------------------------
    # CI data fetchers
    # ------------------------------------------------------------------

    def _fetch_failed_runs(self, limit: int = 5) -> list[dict] | None:
        """Fetch recent failed CI runs via the GitHub CLI.

        Returns list of runs, or None if gh CLI is unavailable.
        """
        try:
            result = subprocess.run(
                [
                    "gh",
                    "run",
                    "list",
                    "--status=failure",
                    f"--limit={limit}",
                    "--json",
                    "databaseId,conclusion,headBranch,event,url,name",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                cwd=str(PROJECT_ROOT),
            )
            return json.loads(result.stdout) if result.stdout.strip() else []
        except OSError as e:
            logger.warning("gh CLI not available: %s", e)
            return None
        except (subprocess.SubprocessError, json.JSONDecodeError) as e:
            logger.warning("Failed to fetch CI runs: %s", e)
            return []

    def _fetch_run_logs(self, gh_run_id: int) -> str:
        """Fetch the failed-job logs for a specific CI run."""
        try:
            result = subprocess.run(
                ["gh", "run", "view", str(gh_run_id), "--log-failed"],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                cwd=str(PROJECT_ROOT),
            )
            output = result.stdout + result.stderr
            # Truncate to last 5000 chars to keep context manageable
            return output[-5000:] if len(output) > 5000 else output
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Failed to fetch logs for run %s: %s", gh_run_id, e)
            return ""

    # ------------------------------------------------------------------
    # Failure categorisation (pure regex, no LLM)
    # ------------------------------------------------------------------

    def _categorize_failure(self, logs: str) -> CIFailureCategory:
        """Determine the failure category from raw CI logs."""
        # Typecheck: mypy errors (check before lint — mypy lines also match py:line:col)
        if re.search(r"error:.*\[", logs) and re.search(r"\.py:\d+:\s*error:", logs):
            return CIFailureCategory.TYPECHECK

        # Lint: ruff-style errors (file.py:line:col: CODE message)
        if re.search(r"\w+\.py:\d+:\d+:\s+[A-Z]\d+\s", logs):
            return CIFailureCategory.LINT

        # Format: ruff format errors
        if re.search(r"would reformat|reformatted|ruff\s+format", logs):
            return CIFailureCategory.FORMAT

        # Test: pytest failures
        if re.search(r"FAILED\s+\S+\.py::", logs):
            return CIFailureCategory.TEST

        # Install: pip errors
        if re.search(r"pip install", logs, re.IGNORECASE) and re.search(r"error|failed", logs, re.IGNORECASE):
            return CIFailureCategory.INSTALL

        return CIFailureCategory.UNKNOWN

    # ------------------------------------------------------------------
    # Error extraction
    # ------------------------------------------------------------------

    def _extract_error_details(self, logs: str, category: CIFailureCategory) -> list[CIError]:
        """Parse structured error information from CI logs."""
        errors: list[CIError] = []

        if category == CIFailureCategory.LINT:
            # Pattern: filepath:line:col: CODE message
            for match in re.finditer(r"^(\S+\.py):(\d+):(\d+):\s+([A-Z]\d+)\s+(.+)$", logs, re.MULTILINE):
                errors.append(
                    CIError(
                        file_path=match.group(1),
                        line=int(match.group(2)),
                        column=int(match.group(3)),
                        code=match.group(4),
                        message=match.group(5).strip(),
                        category=category,
                    )
                )

        elif category == CIFailureCategory.TYPECHECK:
            # Pattern: filepath:line: error: message [code]
            for match in re.finditer(r"^(\S+\.py):(\d+):\s+error:\s+(.+?)(?:\s+\[(\S+)\])?\s*$", logs, re.MULTILINE):
                errors.append(
                    CIError(
                        file_path=match.group(1),
                        line=int(match.group(2)),
                        message=match.group(3).strip(),
                        code=match.group(4) or "",
                        category=category,
                    )
                )

        elif category == CIFailureCategory.TEST:
            # Pattern: FAILED test_path::test_name (file path stops at first ::)
            for match in re.finditer(r"FAILED\s+([^\s:]+)::(\S+)", logs):
                errors.append(
                    CIError(
                        file_path=match.group(1),
                        message=f"Test failed: {match.group(2)}",
                        code=match.group(2),
                        category=category,
                    )
                )

        return errors

    # ------------------------------------------------------------------
    # Fix strategies
    # ------------------------------------------------------------------

    def _fix_lint(self) -> FixAttempt:
        """Tier 1: run ruff check --fix and ruff format (deterministic, no LLM)."""
        try:
            subprocess.run(
                ["python3", "-m", "ruff", "check", "--fix", "ortobahn/", "tests/"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.SubprocessError as e:
            logger.warning("ruff check --fix failed: %s", e)

        try:
            subprocess.run(
                ["python3", "-m", "ruff", "format", "ortobahn/", "tests/"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.SubprocessError as e:
            logger.warning("ruff format failed: %s", e)

        # Collect changed files
        diff_result = git_cmd("diff", "--name-only", check=False)
        changed = [f.strip() for f in diff_result.stdout.strip().splitlines() if f.strip()]

        return FixAttempt(
            strategy="ruff auto-fix (lint + format)",
            files_changed=changed,
            llm_used=False,
            tokens_used=0,
        )

    def _fix_typecheck(self, errors: list[CIError]) -> FixAttempt:
        """Tier 2: fix type errors — simple patterns inline, complex via LLM."""
        files_changed: list[str] = []
        total_tokens = 0
        llm_used = False

        # Group errors by file
        errors_by_file: dict[str, list[CIError]] = {}
        for err in errors:
            if err.file_path:
                errors_by_file.setdefault(err.file_path, []).append(err)

        simple_patterns = ["Need type annotation for", "has no attribute", "Missing return statement"]
        complex_errors: dict[str, list[CIError]] = {}

        for file_path, file_errors in errors_by_file.items():
            if not is_path_safe(file_path):
                continue

            content = read_source_file(file_path)
            if content is None:
                continue

            # Try simple inline fixes first
            has_simple = any(any(pat in err.message for pat in simple_patterns) for err in file_errors)

            if has_simple:
                lines = content.split("\n")
                modified = False
                for err in file_errors:
                    if err.line is None:
                        continue
                    idx = err.line - 1
                    if idx < 0 or idx >= len(lines):
                        continue
                    line = lines[idx]

                    # Add type: ignore for simple cases
                    if "Need type annotation for" in err.message:
                        if "# type: ignore" not in line:
                            lines[idx] = line + "  # type: ignore[annotation-unchecked]"
                            modified = True
                    elif "has no attribute" in err.message:
                        if "# type: ignore" not in line:
                            lines[idx] = line + "  # type: ignore[attr-defined]"
                            modified = True

                if modified:
                    full_path = PROJECT_ROOT / file_path
                    full_path.write_text("\n".join(lines), encoding="utf-8")
                    files_changed.append(file_path)
            else:
                complex_errors[file_path] = file_errors

        # Handle complex errors with LLM
        if complex_errors:
            llm_used = True
            context_parts: list[str] = []
            for file_path, file_errors in complex_errors.items():
                content = read_source_file(file_path)
                if content is None:
                    continue
                error_lines = "\n".join(f"  Line {e.line}: {e.message} [{e.code}]" for e in file_errors)
                context_parts.append(f"### {file_path}\nErrors:\n{error_lines}\n\n```python\n{content}\n```")

            if context_parts:
                user_message = "Fix the following type errors. Return JSON with your changes.\n\n" + "\n\n".join(
                    context_parts
                )
                response = self.call_llm(user_message)
                total_tokens = response.input_tokens + response.output_tokens

                changed = self._apply_llm_changes(response.text)
                files_changed.extend(changed)

        return FixAttempt(
            strategy="typecheck fix (inline annotations + LLM)" if llm_used else "typecheck fix (inline annotations)",
            files_changed=files_changed,
            llm_used=llm_used,
            tokens_used=total_tokens,
        )

    def _fix_tests(self, errors: list[CIError]) -> FixAttempt:
        """Tier 3: fix failing tests by reading test and source files, then calling LLM."""
        context_parts: list[str] = []

        for err in errors:
            if not err.file_path:
                continue
            # Read the failing test file
            test_content = read_source_file(err.file_path)
            if test_content:
                context_parts.append(f"### {err.file_path} (test file)\n```python\n{test_content}\n```")

            # Try to find the corresponding source file
            source_path = err.file_path.replace("tests/test_", "ortobahn/").replace("tests/", "ortobahn/")
            source_content = read_source_file(source_path)
            if source_content:
                context_parts.append(f"### {source_path} (source file)\n```python\n{source_content}\n```")

        if not context_parts:
            return FixAttempt(strategy="test fix (no context available)", files_changed=[], llm_used=False)

        error_summary = "\n".join(f"- {e.file_path}: {e.message}" for e in errors if e.file_path)
        user_message = (
            f"Fix the following test failures:\n{error_summary}\n\n"
            "Here are the relevant files:\n\n" + "\n\n".join(context_parts)
        )

        response = self.call_llm(user_message)
        changed = self._apply_llm_changes(response.text)

        return FixAttempt(
            strategy="test fix via LLM",
            files_changed=changed,
            llm_used=True,
            tokens_used=response.input_tokens + response.output_tokens,
        )

    def _fix_unknown(self, errors: list[CIError], raw_logs: str) -> FixAttempt:
        """Fallback: send raw logs to LLM for diagnosis and fix."""
        user_message = f"Diagnose and fix this CI failure. Here are the logs:\n\n```\n{raw_logs[-3000:]}\n```"

        response = self.call_llm(user_message)
        changed = self._apply_llm_changes(response.text)

        return FixAttempt(
            strategy="unknown failure — LLM diagnosis",
            files_changed=changed,
            llm_used=True,
            tokens_used=response.input_tokens + response.output_tokens,
        )

    # ------------------------------------------------------------------
    # LLM response handling
    # ------------------------------------------------------------------

    def _apply_llm_changes(self, llm_text: str) -> list[str]:
        """Parse LLM JSON response and write file changes. Returns list of changed file paths."""
        try:
            cleaned = llm_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

            data = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError) as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            return []

        changes = data.get("changes", [])
        files_written: list[str] = []

        for change in changes:
            file_path = change.get("file_path", "")
            content = change.get("content", "")

            if not file_path or not content:
                continue
            if not is_path_safe(file_path):
                logger.warning("Skipping unsafe path from LLM: %s", file_path)
                continue

            full_path = (PROJECT_ROOT / file_path).resolve()
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            files_written.append(file_path)

        return files_written

    # ------------------------------------------------------------------
    # Local validation
    # ------------------------------------------------------------------

    def _validate_locally(self, categories: list[CIFailureCategory]) -> tuple[bool, str]:
        """Run relevant checks locally and return (all_passed, output_summary)."""
        outputs: list[str] = []
        all_passed = True

        if CIFailureCategory.LINT in categories or CIFailureCategory.FORMAT in categories:
            # ruff check
            try:
                result = subprocess.run(
                    ["python3", "-m", "ruff", "check", "ortobahn/", "tests/"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    all_passed = False
                    outputs.append(f"ruff check failed:\n{(result.stdout + result.stderr)[-500:]}")
                else:
                    outputs.append("ruff check passed")
            except subprocess.SubprocessError as e:
                all_passed = False
                outputs.append(f"ruff check error: {e}")

            # ruff format --check
            try:
                result = subprocess.run(
                    ["python3", "-m", "ruff", "format", "--check", "ortobahn/", "tests/"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    all_passed = False
                    outputs.append(f"ruff format --check failed:\n{(result.stdout + result.stderr)[-500:]}")
                else:
                    outputs.append("ruff format passed")
            except subprocess.SubprocessError as e:
                all_passed = False
                outputs.append(f"ruff format error: {e}")

        if CIFailureCategory.TYPECHECK in categories:
            try:
                result = subprocess.run(
                    ["python3", "-m", "mypy", "ortobahn/"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    all_passed = False
                    outputs.append(f"mypy failed:\n{(result.stdout + result.stderr)[-500:]}")
                else:
                    outputs.append("mypy passed")
            except subprocess.SubprocessError as e:
                all_passed = False
                outputs.append(f"mypy error: {e}")

        if CIFailureCategory.TEST in categories:
            try:
                result = subprocess.run(
                    ["python3", "-m", "pytest", "-x", "-q", "--tb=short"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    all_passed = False
                    outputs.append(f"pytest failed:\n{(result.stdout + result.stderr)[-500:]}")
                else:
                    outputs.append("pytest passed")
            except subprocess.SubprocessError as e:
                all_passed = False
                outputs.append(f"pytest error: {e}")

        summary = " | ".join(outputs) if outputs else "No checks run"
        return all_passed, summary

    # ------------------------------------------------------------------
    # PR creation
    # ------------------------------------------------------------------

    def _create_pr(self, branch_name: str, failure: CIFailure, fix_attempt: FixAttempt) -> str:
        """Create a GitHub pull request for the fix and return the PR URL."""
        title = f"cifix: auto-fix {failure.category.value} errors (run #{failure.gh_run_id})"
        body = (
            f"## Automated CI Fix\n\n"
            f"**Failed run:** {failure.gh_run_url or f'#{failure.gh_run_id}'}\n"
            f"**Category:** {failure.category.value}\n"
            f"**Errors found:** {len(failure.errors)}\n"
            f"**Strategy:** {fix_attempt.strategy}\n"
            f"**Files changed:** {', '.join(fix_attempt.files_changed)}\n"
            f"**LLM used:** {'Yes' if fix_attempt.llm_used else 'No'}\n"
        )
        try:
            result = subprocess.run(
                ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch_name],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                cwd=str(PROJECT_ROOT),
            )
            pr_url = result.stdout.strip()
            logger.info("Created PR: %s", pr_url)
            return pr_url
        except subprocess.SubprocessError as e:
            logger.warning("Failed to create PR: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Memory and tracking
    # ------------------------------------------------------------------

    def _store_memory(
        self,
        run_id: str,
        failure: CIFailure,
        fix_attempt: FixAttempt,
        success: bool,
    ) -> None:
        """Store a memory about this fix attempt for future reference."""
        try:
            store = MemoryStore(self.db)
            error_pattern = failure.category.value
            if failure.errors:
                codes = list({e.code for e in failure.errors if e.code})
                if codes:
                    error_pattern += f" ({', '.join(codes[:5])})"

            if success:
                store.remember(
                    AgentMemory(
                        agent_name=self.name,
                        memory_type=MemoryType.LESSON,
                        category=MemoryCategory.CALIBRATION,
                        content={
                            "summary": f"Successfully fixed {error_pattern} with strategy: {fix_attempt.strategy}",
                            "error_pattern": error_pattern,
                            "fix_strategy": fix_attempt.strategy,
                            "files_changed": fix_attempt.files_changed,
                        },
                        confidence=0.7,
                        source_run_id=run_id,
                    )
                )
            else:
                store.remember(
                    AgentMemory(
                        agent_name=self.name,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CALIBRATION,
                        content={
                            "summary": f"Failed to fix {error_pattern} with strategy: {fix_attempt.strategy}",
                            "error_pattern": error_pattern,
                            "fix_strategy": fix_attempt.strategy,
                        },
                        confidence=0.4,
                        source_run_id=run_id,
                    )
                )
        except Exception as e:
            logger.warning("Failed to store memory: %s", e)

    def _record_fix_attempt(
        self,
        run_id: str,
        failure: CIFailure,
        fix_attempt: FixAttempt,
        success: bool,
        branch_name: str = "",
        commit_sha: str = "",
        pr_url: str = "",
        error: str = "",
    ) -> None:
        """Record the fix attempt in the database for tracking."""
        try:
            error_codes = list({e.code for e in failure.errors if e.code})
            self.db.log_ci_fix_attempt(
                {
                    "run_id": run_id,
                    "gh_run_id": failure.gh_run_id,
                    "gh_run_url": failure.gh_run_url,
                    "job_name": failure.job_name,
                    "failure_category": failure.category.value,
                    "error_count": len(failure.errors),
                    "error_codes": error_codes,
                    "fix_strategy": fix_attempt.strategy,
                    "status": "success" if success else "failed",
                    "files_changed": fix_attempt.files_changed,
                    "branch_name": branch_name,
                    "commit_sha": commit_sha,
                    "pr_url": pr_url,
                    "llm_used": fix_attempt.llm_used,
                    "input_tokens": fix_attempt.tokens_used // 2 if fix_attempt.llm_used else 0,
                    "output_tokens": fix_attempt.tokens_used // 2 if fix_attempt.llm_used else 0,
                    "validation_passed": success,
                    "error_message": error or None,
                }
            )
        except Exception as e:
            logger.warning("Failed to record fix attempt: %s", e)
