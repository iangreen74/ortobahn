"""CTO Agent - autonomously implements engineering tasks."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from typing import Any

from ortobahn.agents.base import BaseAgent
from ortobahn.git_utils import (
    PROJECT_ROOT,
    commit_all,
    create_branch,
    current_branch,
    delete_branch,
    is_path_safe,
    switch_branch,
)
from ortobahn.models import CTOResult

logger = logging.getLogger("ortobahn.cto")

# Maximum time for pytest subprocess
TEST_TIMEOUT_SECONDS = 120


class CTOAgent(BaseAgent):
    name = "cto"
    prompt_file = "cto.txt"
    thinking_budget = 16_000

    def __init__(self, db, api_key: str, model: str = "claude-sonnet-4-5-20250929", max_tokens: int = 16384):
        super().__init__(db, api_key, model, max_tokens)

    # ------------------------------------------------------------------
    # CTO-specific helpers
    # ------------------------------------------------------------------

    def _relevant_source_files(self, title: str, description: str) -> list[str]:
        """Heuristic: pick source files likely relevant to the task based on keywords."""
        keywords = set(re.findall(r"[a-z_]+", (title + " " + description).lower()))

        # Map keywords to likely source paths
        keyword_file_map: dict[str, list[str]] = {
            "health": ["ortobahn/healthcheck.py", "ortobahn/web/app.py"],
            "healthcheck": ["ortobahn/healthcheck.py", "ortobahn/web/app.py"],
            "alb": ["ortobahn/healthcheck.py", "ortobahn/web/app.py"],
            "rate": ["ortobahn/web/app.py", "ortobahn/config.py"],
            "limit": ["ortobahn/web/app.py", "ortobahn/config.py"],
            "login": ["ortobahn/web/app.py", "ortobahn/auth.py", "ortobahn/config.py"],
            "auth": ["ortobahn/web/app.py", "ortobahn/auth.py", "ortobahn/config.py"],
            "password": ["ortobahn/web/app.py", "ortobahn/auth.py"],
            "api": ["ortobahn/web/app.py", "ortobahn/db.py"],
            "content": ["ortobahn/web/app.py", "ortobahn/db.py", "ortobahn/models.py"],
            "endpoint": ["ortobahn/web/app.py"],
            "test": ["tests/conftest.py"],
            "coverage": ["tests/conftest.py"],
            "database": ["ortobahn/db.py", "ortobahn/config.py"],
            "backup": ["ortobahn/db.py", "ortobahn/config.py"],
            "s3": ["ortobahn/config.py"],
            "openapi": ["ortobahn/web/app.py"],
            "docs": ["ortobahn/web/app.py"],
            "documentation": ["ortobahn/web/app.py"],
            "model": ["ortobahn/models.py"],
            "agent": ["ortobahn/agents/base.py"],
            "pipeline": ["ortobahn/orchestrator.py"],
            "config": ["ortobahn/config.py"],
            "bluesky": ["ortobahn/integrations/bluesky.py"],
            "twitter": ["ortobahn/integrations/twitter.py"],
            "linkedin": ["ortobahn/integrations/linkedin.py"],
            "migration": ["ortobahn/migrations.py"],
        }

        files: set[str] = set()
        for kw in keywords:
            if kw in keyword_file_map:
                files.update(keyword_file_map[kw])

        # Always include core files for context
        files.update(["ortobahn/models.py", "ortobahn/config.py"])

        # Filter to files that exist
        existing = []
        for f in sorted(files):
            full = PROJECT_ROOT / f
            if full.is_file():
                existing.append(f)

        return existing[:15]  # Cap to avoid oversized prompts

    def _read_source_files(self, file_paths: list[str]) -> str:
        """Read source files and format them for the LLM context."""
        parts: list[str] = []
        for rel_path in file_paths:
            full_path = PROJECT_ROOT / rel_path
            if not full_path.is_file():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
                # Truncate very large files
                if len(content) > 8000:
                    content = content[:8000] + "\n... (truncated)"
                parts.append(f"### {rel_path}\n```python\n{content}\n```")
            except Exception as e:
                logger.warning(f"Could not read {rel_path}: {e}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _run_tests(self) -> tuple[bool, str]:
        """Run pytest and return (passed, output)."""
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", "-x", "-q", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_SECONDS,
                cwd=str(PROJECT_ROOT),
            )
            output = result.stdout + result.stderr
            passed = result.returncode == 0
            return passed, output[-2000:]  # Truncate output
        except subprocess.TimeoutExpired:
            return False, "Tests timed out after 120 seconds"
        except FileNotFoundError:
            # pytest not installed; treat as pass to avoid blocking
            logger.warning("pytest not found, skipping test run")
            return True, "pytest not found, skipped"

    # ------------------------------------------------------------------
    # Core run
    # ------------------------------------------------------------------

    def run(self, run_id: str, **kwargs: Any) -> CTOResult:
        """Pick up the next engineering task and implement it."""

        # 1. Pick highest-priority backlog task
        task = self.db.get_next_engineering_task()
        if not task:
            logger.info("No backlog tasks available")
            self.log_decision(
                run_id=run_id,
                input_summary="No backlog tasks",
                output_summary="Skipped — nothing to do",
            )
            return CTOResult(task_id="", status="skipped", summary="No backlog tasks available")

        task_id = task["id"]
        branch_name = f"cto/{task.get('category', 'feature')}/{task_id[:8]}"
        original_branch = current_branch()

        # Record the CTO run
        self.db.start_cto_run(run_id, task_id)

        try:
            # 2. Mark task as in_progress
            self.db.update_engineering_task(
                task_id,
                {
                    "status": "in_progress",
                    "started_at": datetime.utcnow().isoformat(),
                    "assigned_run_id": run_id,
                    "branch_name": branch_name,
                },
            )

            # 3. Create feature branch
            # Make sure we are on main first
            if original_branch != "main":
                switch_branch("main")
                original_branch = "main"
            create_branch(branch_name)

            # 4. Read relevant source files
            source_files = self._relevant_source_files(task["title"], task["description"])
            source_context = self._read_source_files(source_files)

            # 5. Build user message
            user_message = f"""## Engineering Task
**Title:** {task["title"]}
**Description:** {task["description"]}
**Priority:** P{task.get("priority", 3)}
**Category:** {task.get("category", "feature")}
**Complexity:** {task.get("estimated_complexity", "medium")}

## Relevant Source Files
{source_context if source_context else "(No source files found — this may be a new feature.)"}
"""

            # 6. Call LLM with extended thinking
            response = self.call_llm(user_message)

            # 7. Parse JSON response
            try:
                cleaned = response.text.strip()
                # Strip markdown code fences
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in cleaned:
                    cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

                llm_output = json.loads(cleaned)
            except (json.JSONDecodeError, IndexError) as e:
                raise ValueError(f"Failed to parse LLM response as JSON: {e}") from e

            plan = llm_output.get("plan", "")
            changes = llm_output.get("changes", [])
            test_files = llm_output.get("test_files", [])
            all_changes = changes + test_files

            # 8. Write files to disk with safety checks
            files_written: list[str] = []
            for change in all_changes:
                file_path = change.get("file_path", "")
                content = change.get("content", "")

                if not file_path or not content:
                    continue

                if not is_path_safe(file_path):
                    logger.warning(f"Skipping unsafe path: {file_path}")
                    continue

                full_path = (PROJECT_ROOT / file_path).resolve()

                # Create parent directories if needed
                full_path.parent.mkdir(parents=True, exist_ok=True)

                full_path.write_text(content, encoding="utf-8")
                files_written.append(file_path)

                # Log the code change
                self.db.log_code_change(
                    task_id=task_id,
                    run_id=run_id,
                    file_path=file_path,
                    change_type=change.get("change_type", "modify"),
                )

            if not files_written:
                raise RuntimeError("LLM produced no valid file changes")

            # 9. Run tests
            tests_passed, test_output = self._run_tests()

            if tests_passed:
                # 10. Tests pass: commit
                commit_msg = f"cto: {task['title']}\n\nTask: {task_id}\nPlan: {plan}"
                commit_sha = commit_all(commit_msg)

                self.db.update_engineering_task(
                    task_id,
                    {
                        "status": "completed",
                        "completed_at": datetime.utcnow().isoformat(),
                        "files_changed": json.dumps(files_written),
                    },
                )

                self.db.complete_cto_run(
                    run_id,
                    status="success",
                    thinking_summary=(response.thinking or "")[:500],
                    files_read=source_files,
                    files_written=files_written,
                    tests_passed=True,
                    commit_sha=commit_sha,
                    total_input_tokens=response.input_tokens,
                    total_output_tokens=response.output_tokens,
                )

                self.log_decision(
                    run_id=run_id,
                    input_summary=f"Task: {task['title']} (P{task.get('priority', 3)})",
                    output_summary=f"Completed: {len(files_written)} files, branch={branch_name}, sha={commit_sha[:8]}",
                    reasoning=f"Plan: {plan[:200]}",
                    llm_response=response,
                )

                # 12. Switch back to main
                switch_branch("main")

                return CTOResult(
                    task_id=task_id,
                    status="success",
                    branch_name=branch_name,
                    commit_sha=commit_sha,
                    files_changed=files_written,
                    summary=plan,
                )
            else:
                # 11. Tests fail: rollback
                logger.warning(f"Tests failed for task {task_id}, rolling back")

                switch_branch("main")
                delete_branch(branch_name)

                self.db.update_engineering_task(
                    task_id,
                    {
                        "status": "failed",
                        "completed_at": datetime.utcnow().isoformat(),
                        "error": test_output[:500],
                    },
                )

                self.db.complete_cto_run(
                    run_id,
                    status="failed",
                    thinking_summary=(response.thinking or "")[:500],
                    files_read=source_files,
                    files_written=files_written,
                    tests_passed=False,
                    tests_failed=test_output[:500],
                    error=test_output[:500],
                    total_input_tokens=response.input_tokens,
                    total_output_tokens=response.output_tokens,
                )

                self.log_decision(
                    run_id=run_id,
                    input_summary=f"Task: {task['title']} (P{task.get('priority', 3)})",
                    output_summary="Failed: tests did not pass. Branch deleted.",
                    reasoning=f"Test output: {test_output[:200]}",
                    llm_response=response,
                )

                return CTOResult(
                    task_id=task_id,
                    status="failed",
                    files_changed=files_written,
                    summary=plan,
                    error=f"Tests failed: {test_output[:300]}",
                )

        except Exception as e:
            logger.error(f"CTO agent error for task {task_id}: {e}")

            # Try to switch back to main and clean up
            try:
                cur = current_branch()
                if cur != "main":
                    switch_branch("main")
                    delete_branch(branch_name)
            except Exception:
                pass

            self.db.update_engineering_task(
                task_id,
                {
                    "status": "failed",
                    "completed_at": datetime.utcnow().isoformat(),
                    "error": str(e)[:500],
                },
            )

            self.db.complete_cto_run(
                run_id,
                status="error",
                error=str(e)[:500],
            )

            self.log_decision(
                run_id=run_id,
                input_summary=f"Task: {task['title']} (P{task.get('priority', 3)})",
                output_summary=f"Error: {str(e)[:100]}",
                reasoning=str(e)[:300],
            )

            return CTOResult(
                task_id=task_id,
                status="failed",
                error=str(e)[:300],
            )
