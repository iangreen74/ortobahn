"""Flaky test detection — track per-test results over time and identify intermittent failures."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.test_tracker")


@dataclass
class TestResult:
    """Single test result from a pytest run."""

    test_file: str
    test_name: str  # full test::name
    outcome: str  # "passed", "failed", "error", "skipped"
    duration_ms: float = 0.0
    error_message: str = ""


class TestTracker:
    """Parse pytest output, record per-test results, and detect flaky tests."""

    # Patterns for verbose pytest output lines
    _RESULT_RE = re.compile(
        r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(\S+\.py)::(\S+)"
        r"(?:\s+-\s+(.+))?$",
        re.MULTILINE,
    )

    # Short summary line: "= 150 passed, 2 failed in 10.5s ="
    _SUMMARY_RE = re.compile(
        r"=+\s*(.*?)\s+in\s+[\d.]+s\s*=+",
    )

    # Individual count inside summary, e.g. "150 passed"
    _COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|skipped|warnings?|deselected)")

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_pytest_output(self, output: str) -> list[TestResult]:
        """Parse verbose pytest output and return individual test results."""
        results: list[TestResult] = []
        seen: set[str] = set()

        for match in self._RESULT_RE.finditer(output):
            outcome = match.group(1).lower()
            test_file = match.group(2)
            test_name_part = match.group(3)
            error_msg = match.group(4) or ""

            full_name = f"{test_file}::{test_name_part}"
            if full_name in seen:
                continue
            seen.add(full_name)

            results.append(
                TestResult(
                    test_file=test_file,
                    test_name=full_name,
                    outcome=outcome,
                    error_message=error_msg.strip(),
                )
            )

        return results

    def parse_summary_line(self, output: str) -> dict[str, int]:
        """Parse the short summary line and return counts by outcome."""
        counts: dict[str, int] = {}
        match = self._SUMMARY_RE.search(output)
        if not match:
            return counts
        summary_text = match.group(1)
        for count_match in self._COUNT_RE.finditer(summary_text):
            count = int(count_match.group(1))
            key = count_match.group(2).rstrip("s")  # normalize "warnings" -> "warning"
            counts[key] = count
        return counts

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_results(self, run_id: str, results: list[TestResult]) -> None:
        """Persist a batch of test results from a single run."""
        for result in results:
            try:
                self.db.save_test_result(
                    {
                        "run_id": run_id,
                        "test_file": result.test_file,
                        "test_name": result.test_name,
                        "outcome": result.outcome,
                        "duration_ms": result.duration_ms,
                        "error_message": result.error_message,
                    }
                )
            except Exception as e:
                logger.warning("Failed to record test result %s: %s", result.test_name, e)

    # ------------------------------------------------------------------
    # Flakiness queries
    # ------------------------------------------------------------------

    def get_flaky_tests(self, window_days: int = 14, min_runs: int = 3) -> list[dict]:
        """Return tests that have both pass and fail within the window.

        Each entry contains: test_name, test_file, total_runs, failures,
        passes, flakiness_score.
        """
        return self.db.get_flaky_tests(window_days=window_days, min_runs=min_runs)

    def is_flaky(self, test_name: str, window_days: int = 14) -> bool:
        """Check whether a specific test is considered flaky."""
        history = self.db.get_test_history(test_name, limit=50)
        if not history:
            return False

        # Filter to window
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        recent: list[dict] = []
        for row in history:
            try:
                created = row.get("created_at", "")
                if isinstance(created, str) and created:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                recent.append(row)
            except (ValueError, TypeError):
                recent.append(row)

        if len(recent) < 2:
            return False

        outcomes = {r["outcome"] for r in recent}
        has_pass = "passed" in outcomes
        has_fail = "failed" in outcomes or "error" in outcomes
        return has_pass and has_fail

    def get_test_history(self, test_name: str, limit: int = 20) -> list[dict]:
        """Return recent results for a specific test."""
        return self.db.get_test_history(test_name, limit=limit)

    def get_flakiness_score(self, test_name: str, window_days: int = 14) -> float:
        """Calculate flakiness score: failures / total for tests with mixed outcomes.

        Returns 0.0 if the test is consistently passing or consistently failing.
        """
        from datetime import datetime, timedelta, timezone

        history = self.db.get_test_history(test_name, limit=100)
        if not history:
            return 0.0

        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        recent: list[dict] = []
        for row in history:
            try:
                created = row.get("created_at", "")
                if isinstance(created, str) and created:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                recent.append(row)
            except (ValueError, TypeError):
                recent.append(row)

        if not recent:
            return 0.0

        outcomes = {r["outcome"] for r in recent}
        has_pass = "passed" in outcomes
        has_fail = "failed" in outcomes or "error" in outcomes

        if not (has_pass and has_fail):
            return 0.0

        failures = sum(1 for r in recent if r["outcome"] in ("failed", "error"))
        return failures / len(recent)
