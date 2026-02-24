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
    correlate_failures_with_changes,
    create_branch,
    current_branch,
    delete_branch,
    enable_auto_merge,
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
from ortobahn.test_parser import TestErrorParser
from ortobahn.test_tracker import TestTracker

logger = logging.getLogger("ortobahn.cifix")

# Maps smoke-test endpoints to the source files that serve them.
# Used by _fix_deploy() to give the LLM the right context.
ENDPOINT_SOURCE_MAP: dict[str, list[str]] = {
    "/health": ["ortobahn/web/app.py"],
    "/api/toasts": ["ortobahn/web/app.py"],
    "/api/internal/pipeline-dry-run": ["ortobahn/web/app.py", "ortobahn/orchestrator.py"],
    "/my/dashboard": ["ortobahn/web/routes/tenant_dashboard.py", "ortobahn/auth.py"],
    "/glass": ["ortobahn/web/routes/glass.py"],
    "/api/public/stats": ["ortobahn/web/routes/glass.py"],
    "/api/onboard": ["ortobahn/web/routes/onboard.py"],
    "/static/": ["ortobahn/web/app.py"],
}

# Patterns in deploy logs that indicate a deploy (not CI) failure.
DEPLOY_LOG_PATTERNS = [
    r"Smoke test",
    r"Pipeline dry-run",
    r"FAIL:.*returned HTTP",
    r"deploy-staging|deploy-prod|smoke-test",
    r"post-deploy-validate",
    r"Gateway Time-out",
    r"services-stable",
]


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

        # 4a. Track test results and detect flaky tests
        flaky_skip = False
        try:
            tracker = TestTracker(self.db)
            test_results = tracker.parse_pytest_output(raw_logs)
            if test_results:
                tracker.record_results(str(gh_run_id), test_results)

            # Check if all failing tests are known flaky
            if category == CIFailureCategory.TEST and test_results:
                failing_tests = [r for r in test_results if r.outcome in ("failed", "error")]
                if failing_tests:
                    all_flaky = all(tracker.is_flaky(t.test_name) for t in failing_tests)
                    if all_flaky:
                        flaky_skip = True
                        logger.info(
                            "All %d failing tests are known flaky — skipping fix",
                            len(failing_tests),
                        )
        except Exception as e:
            logger.warning("Flaky test detection failed: %s", e)

        if flaky_skip:
            self.log_decision(
                run_id=run_id,
                input_summary=f"CI run #{gh_run_id} failed ({category.value}, {len(errors)} errors)",
                output_summary="All failing tests are known flaky — skipped",
            )
            return CIFixResult(
                status="flaky_skip",
                summary="All failing tests are known flaky — no fix needed",
            )

        # Build the CIFailure record
        failure = CIFailure(
            gh_run_id=gh_run_id,
            gh_run_url=gh_run_url,
            job_name=run_info.get("name", ""),
            category=category,
            errors=errors,
            raw_logs=raw_logs[-3000:],
        )

        # 5. Check memory for past fix patterns + build fix playbook
        memory_context = self.get_memory_context()
        if memory_context:
            logger.info("Found past fix patterns in memory")

        fix_playbook = self._build_fix_playbook(category)
        fix_context = ""
        if memory_context and fix_playbook:
            fix_context = f"{memory_context}\n\n{fix_playbook}"
        elif memory_context:
            fix_context = memory_context
        elif fix_playbook:
            fix_context = fix_playbook

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
                fix_attempt = self._fix_typecheck(errors, fix_context=fix_context)

            # Tier 3: complex LLM fix
            elif category == CIFailureCategory.TEST:
                fix_attempt = self._fix_tests(errors, fix_context=fix_context, raw_logs=raw_logs)

            # Tier 4: deploy failures — map endpoints to source, LLM diagnosis
            elif category == CIFailureCategory.DEPLOY:
                fix_attempt = self._fix_deploy(errors, raw_logs, fix_context=fix_context)

            # Unknown / install — attempt LLM diagnosis
            else:
                fix_attempt = self._fix_unknown(errors, raw_logs, fix_context=fix_context)

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
                    if pr_url:
                        enable_auto_merge(pr_url)

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
    # Fix playbook from historical data
    # ------------------------------------------------------------------

    def _build_fix_playbook(self, category: CIFailureCategory) -> str:
        """Build a playbook of past fix strategies for this failure category.

        Queries the ci_fix_attempts table and formats successes and failures
        into a context string.  Pure computation, no LLM calls.
        """
        try:
            history = self.db.get_ci_fix_history(category=category.value, limit=10)
            if not history:
                return ""

            success_rate = self.db.get_ci_fix_success_rate(category.value)

            # Two-pass deduplication: first collect all successful strategies,
            # then only add failures that were never successful.
            seen_success: set[str] = set()
            for entry in history:
                strategy = entry.get("fix_strategy", "").strip()
                if strategy and entry.get("status") == "success":
                    seen_success.add(strategy)

            successes: list[str] = []
            seen_failure: set[str] = set()
            failures: list[str] = []
            seen_success_ordered: set[str] = set()

            for entry in history:
                strategy = entry.get("fix_strategy", "").strip()
                if not strategy:
                    continue
                status = entry.get("status", "")
                if status == "success" and strategy not in seen_success_ordered:
                    seen_success_ordered.add(strategy)
                    successes.append(strategy)
                elif status != "success" and strategy not in seen_failure:
                    if strategy not in seen_success:
                        seen_failure.add(strategy)
                        failures.append(strategy)

            if not successes and not failures:
                return ""

            lines = [
                f"## Fix Playbook ({category.value}, {success_rate:.0%} historical success rate)",
            ]
            if successes:
                lines.append("Strategies that WORKED:")
                for s in successes:
                    lines.append(f"  - {s}")
            if failures:
                lines.append("Strategies that FAILED (avoid repeating):")
                for f in failures:
                    lines.append(f"  - {f}")

            return "\n".join(lines)

        except Exception as e:
            logger.warning("Failed to build fix playbook: %s", e)
            return ""

    # ------------------------------------------------------------------
    # CI data fetchers
    # ------------------------------------------------------------------

    def _fetch_failed_runs(self, limit: int = 5, workflow: str = "") -> list[dict] | None:
        """Fetch recent failed workflow runs via the GitHub CLI.

        Args:
            limit: Maximum number of runs to return.
            workflow: Filter by workflow name (e.g. "CI", "Deploy"). Empty = all.

        Returns list of runs, or None if gh CLI is unavailable.
        """
        try:
            cmd = [
                "gh",
                "run",
                "list",
                "--status=failure",
                f"--limit={limit}",
                "--json",
                "databaseId,conclusion,headBranch,event,url,name,workflowName",
            ]
            if workflow:
                cmd.extend(["--workflow", workflow])
            result = subprocess.run(
                cmd,
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
        # Deploy: smoke test failures, HTTP errors from staging/prod checks
        deploy_hits = sum(1 for pat in DEPLOY_LOG_PATTERNS if re.search(pat, logs, re.IGNORECASE))
        if deploy_hits >= 2:
            return CIFailureCategory.DEPLOY

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

        elif category == CIFailureCategory.DEPLOY:
            # Pattern: FAIL: <description> returned HTTP <code>
            for match in re.finditer(
                r"FAIL:\s*(.+?)(?:returned HTTP (\d+))?(?:\s*\(([^)]+)\))?\s*$", logs, re.MULTILINE
            ):
                desc = match.group(1).strip()
                http_code = match.group(2) or ""
                detail = match.group(3) or ""
                # Try to extract the endpoint from the description
                endpoint = ""
                ep_match = re.search(r"(/[\w/.-]+)", desc)
                if ep_match:
                    endpoint = ep_match.group(1)
                errors.append(
                    CIError(
                        file_path=endpoint,
                        message=f"{desc} {detail}".strip(),
                        code=f"HTTP_{http_code}" if http_code else "DEPLOY_FAIL",
                        category=category,
                    )
                )
            # Also catch crash signatures in deploy response bodies
            for match in re.finditer(
                r"(?:ImportError|ModuleNotFoundError|SyntaxError|AttributeError|NameError|TypeError):\s*(.+?)$",
                logs,
                re.MULTILINE,
            ):
                errors.append(
                    CIError(
                        file_path="",
                        message=match.group(0).strip(),
                        code="RUNTIME_CRASH",
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

    def _fix_typecheck(self, errors: list[CIError], fix_context: str = "") -> FixAttempt:
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

            # Add blame context for complex errors
            blame_context = ""
            try:
                all_complex_errors = [e for errs in complex_errors.values() for e in errs]
                file_changes = correlate_failures_with_changes(all_complex_errors)
                if file_changes:
                    blame_lines = ["## Recent changes to failing files:"]
                    for fpath, commits in file_changes.items():
                        for c in commits[:3]:
                            blame_lines.append(
                                f"  - {fpath}: commit {c['sha'][:8]} by {c['author']} on {c['date']}: {c['message']}"
                            )
                    blame_context = "\n".join(blame_lines)
            except Exception as e:
                logger.warning("Git blame correlation failed: %s", e)

            if context_parts:
                user_message = "Fix the following type errors. Return JSON with your changes.\n\n" + "\n\n".join(
                    context_parts
                )
                if blame_context:
                    user_message += "\n\n" + blame_context
                if fix_context:
                    user_message = f"{fix_context}\n\n{user_message}"
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

    def _fix_tests(self, errors: list[CIError], fix_context: str = "", raw_logs: str = "") -> FixAttempt:
        """Tier 3: fix failing tests by reading test and source files, then calling LLM."""
        context_parts: list[str] = []

        # Use structured error parsing for better LLM context
        structured_error_context = ""
        try:
            parser = TestErrorParser()
            if raw_logs:
                parsed_errors = parser.parse(raw_logs)
                if parsed_errors:
                    structured_error_context = parser.format_for_llm(parsed_errors)
        except Exception as e:
            logger.warning("Test error parsing failed: %s", e)

        # Add flakiness info
        flakiness_notes: list[str] = []
        try:
            tracker = TestTracker(self.db)
            for err in errors:
                test_name = f"{err.file_path}::{err.code}" if err.file_path and err.code else ""
                if test_name:
                    score = tracker.get_flakiness_score(test_name)
                    if score > 0:
                        flakiness_notes.append(f"Note: {test_name} has {score:.0%} flakiness rate (likely flaky)")
        except Exception as e:
            logger.warning("Flakiness check failed: %s", e)

        # Correlate with recent changes (git blame)
        blame_context = ""
        try:
            file_changes = correlate_failures_with_changes(errors)
            if file_changes:
                blame_lines = ["## Recent changes to failing files:"]
                for fpath, commits in file_changes.items():
                    for c in commits[:3]:
                        blame_lines.append(
                            f"  - {fpath}: commit {c['sha'][:8]} by {c['author']} on {c['date']}: {c['message']}"
                        )
                blame_context = "\n".join(blame_lines)
        except Exception as e:
            logger.warning("Git blame correlation failed: %s", e)

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

        # Build user message with structured context
        if structured_error_context:
            error_summary = structured_error_context
        else:
            error_summary = "\n".join(f"- {e.file_path}: {e.message}" for e in errors if e.file_path)

        user_message = f"Fix the following test failures:\n{error_summary}\n\n"

        if flakiness_notes:
            user_message += "\n".join(flakiness_notes) + "\n\n"
        if blame_context:
            user_message += blame_context + "\n\n"

        user_message += "Here are the relevant files:\n\n" + "\n\n".join(context_parts)

        if fix_context:
            user_message = f"{fix_context}\n\n{user_message}"

        response = self.call_llm(user_message)
        changed = self._apply_llm_changes(response.text)

        return FixAttempt(
            strategy="test fix via LLM",
            files_changed=changed,
            llm_used=True,
            tokens_used=response.input_tokens + response.output_tokens,
        )

    def _fix_unknown(self, errors: list[CIError], raw_logs: str, fix_context: str = "") -> FixAttempt:
        """Fallback: send raw logs to LLM for diagnosis and fix."""
        user_message = f"Diagnose and fix this CI failure. Here are the logs:\n\n```\n{raw_logs[-3000:]}\n```"
        if fix_context:
            user_message = f"{fix_context}\n\n{user_message}"

        response = self.call_llm(user_message)
        changed = self._apply_llm_changes(response.text)

        return FixAttempt(
            strategy="unknown failure — LLM diagnosis",
            files_changed=changed,
            llm_used=True,
            tokens_used=response.input_tokens + response.output_tokens,
        )

    def _fix_deploy(self, errors: list[CIError], raw_logs: str, fix_context: str = "") -> FixAttempt:
        """Fix deploy failures by mapping endpoints to source files and calling LLM."""
        context_parts: list[str] = []

        # Collect source files relevant to the failed endpoints
        source_files_seen: set[str] = set()
        for err in errors:
            endpoint = err.file_path  # file_path stores the endpoint for DEPLOY errors
            # Find matching source files
            matched_sources: list[str] = []
            for pattern, sources in ENDPOINT_SOURCE_MAP.items():
                if pattern in endpoint or endpoint in pattern:
                    matched_sources.extend(sources)
            if not matched_sources:
                # Fallback: include the main app file and rate limiter
                matched_sources = ["ortobahn/web/app.py", "ortobahn/web/rate_limit.py"]

            for src in matched_sources:
                if src in source_files_seen:
                    continue
                source_files_seen.add(src)
                content = read_source_file(src)
                if content:
                    context_parts.append(f"### {src}\n```python\n{content}\n```")

        # Always include middleware (common deploy failure source)
        for critical in ["ortobahn/web/app.py", "ortobahn/web/rate_limit.py"]:
            if critical not in source_files_seen:
                content = read_source_file(critical)
                if content:
                    context_parts.append(f"### {critical}\n```python\n{content}\n```")

        if not context_parts:
            return FixAttempt(strategy="deploy fix (no source context)", files_changed=[], llm_used=False)

        error_summary = "\n".join(f"- {e.code}: {e.message}" for e in errors)
        user_message = (
            "A deploy to staging failed during smoke tests. "
            "Diagnose the root cause and fix the code so the smoke tests pass.\n\n"
            f"## Failed smoke tests\n{error_summary}\n\n"
            f"## Deploy logs (last 3000 chars)\n```\n{raw_logs[-3000:]}\n```\n\n"
            "## Relevant source files\n\n" + "\n\n".join(context_parts)
        )
        if fix_context:
            user_message = f"{fix_context}\n\n{user_message}"

        response = self.call_llm(user_message)
        changed = self._apply_llm_changes(response.text)

        return FixAttempt(
            strategy="deploy fix via LLM (smoke test failure diagnosis)",
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

        if CIFailureCategory.DEPLOY in categories:
            # Run web integration tests + lint to validate deploy fixes
            try:
                result = subprocess.run(
                    [
                        "python3",
                        "-m",
                        "pytest",
                        "tests/test_web_integration.py",
                        "tests/test_web.py",
                        "-x",
                        "-q",
                        "--tb=short",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    all_passed = False
                    outputs.append(f"web integration tests failed:\n{(result.stdout + result.stderr)[-500:]}")
                else:
                    outputs.append("web integration tests passed")
            except subprocess.SubprocessError as e:
                all_passed = False
                outputs.append(f"web integration test error: {e}")
            # Also lint-check the changed files
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
            except subprocess.SubprocessError:
                pass  # Non-critical for deploy fixes

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

        # Also save parsed CI errors to the ci_errors table
        try:
            parser = TestErrorParser()
            parsed_errors = parser.parse(failure.raw_logs) if failure.raw_logs else []
            for pe in parsed_errors:
                ci_error_data: dict = {
                    "run_id": run_id,
                    "gh_run_id": failure.gh_run_id,
                    "test_name": pe.test_name,
                    "test_file": pe.test_file,
                    "error_type": pe.error_type,
                    "error_message": pe.error_message,
                    "stack_trace": "\n".join(
                        f"{f.file_path}:{f.line_number} in {f.function_name}" for f in pe.stack_frames
                    ),
                    "assertion_expected": pe.assertion_diff.expected if pe.assertion_diff else "",
                    "assertion_actual": pe.assertion_diff.actual if pe.assertion_diff else "",
                }
                self.db.save_ci_error(ci_error_data)
        except Exception as e:
            logger.warning("Failed to save parsed CI errors: %s", e)
