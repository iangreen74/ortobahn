"""Tests for flaky test detection (TestTracker)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ortobahn.db import Database
from ortobahn.test_tracker import TestResult, TestTracker


@pytest.fixture
def tracker_db(tmp_path):
    """Fresh SQLite DB with migrations applied."""
    db = Database(tmp_path / "tracker_test.db")
    yield db
    db.close()


@pytest.fixture
def tracker(tracker_db):
    return TestTracker(tracker_db)


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestParseOutput:
    def test_parse_pytest_passed(self, tracker):
        output = "PASSED tests/test_foo.py::test_bar"
        results = tracker.parse_pytest_output(output)
        assert len(results) == 1
        assert results[0].test_file == "tests/test_foo.py"
        assert results[0].test_name == "tests/test_foo.py::test_bar"
        assert results[0].outcome == "passed"

    def test_parse_pytest_failed(self, tracker):
        output = "FAILED tests/test_foo.py::test_bar - AssertionError"
        results = tracker.parse_pytest_output(output)
        assert len(results) == 1
        assert results[0].outcome == "failed"
        assert "AssertionError" in results[0].error_message

    def test_parse_pytest_error(self, tracker):
        output = "ERROR tests/test_foo.py::test_bar"
        results = tracker.parse_pytest_output(output)
        assert len(results) == 1
        assert results[0].outcome == "error"

    def test_parse_pytest_mixed(self, tracker):
        output = (
            "PASSED tests/test_foo.py::test_alpha\n"
            "FAILED tests/test_foo.py::test_beta - KeyError\n"
            "ERROR tests/test_bar.py::test_gamma\n"
            "SKIPPED tests/test_bar.py::test_delta\n"
        )
        results = tracker.parse_pytest_output(output)
        assert len(results) == 4
        outcomes = {r.outcome for r in results}
        assert outcomes == {"passed", "failed", "error", "skipped"}

    def test_parse_summary_line(self, tracker):
        output = "some prefix\n= 10 passed, 2 failed in 5.3s ="
        counts = tracker.parse_summary_line(output)
        assert counts.get("passed") == 10
        assert counts.get("failed") == 2

    def test_parse_empty_output(self, tracker):
        results = tracker.parse_pytest_output("")
        assert results == []


# ---------------------------------------------------------------------------
# Recording and retrieval
# ---------------------------------------------------------------------------


class TestRecordAndRetrieve:
    def test_record_and_retrieve(self, tracker):
        results = [
            TestResult(
                test_file="tests/test_foo.py",
                test_name="tests/test_foo.py::test_one",
                outcome="passed",
            ),
            TestResult(
                test_file="tests/test_foo.py",
                test_name="tests/test_foo.py::test_two",
                outcome="failed",
                error_message="assert 1 == 2",
            ),
        ]
        tracker.record_results("run-1", results)

        history = tracker.get_test_history("tests/test_foo.py::test_one")
        assert len(history) == 1
        assert history[0]["outcome"] == "passed"

        history2 = tracker.get_test_history("tests/test_foo.py::test_two")
        assert len(history2) == 1
        assert history2[0]["outcome"] == "failed"


# ---------------------------------------------------------------------------
# Flakiness detection
# ---------------------------------------------------------------------------


class TestFlakiness:
    def test_flaky_detection(self, tracker):
        """Record alternating pass/fail — should be detected as flaky."""
        for i in range(6):
            outcome = "passed" if i % 2 == 0 else "failed"
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_flaky",
                    outcome=outcome,
                )
            ]
            tracker.record_results(f"run-{i}", results)

        assert tracker.is_flaky("tests/test_x.py::test_flaky")

    def test_not_flaky_all_pass(self, tracker):
        """Consistently passing test should not be flagged."""
        for i in range(5):
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_stable",
                    outcome="passed",
                )
            ]
            tracker.record_results(f"run-{i}", results)

        assert not tracker.is_flaky("tests/test_x.py::test_stable")

    def test_not_flaky_all_fail(self, tracker):
        """Consistently failing test should not be flagged as flaky."""
        for i in range(5):
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_broken",
                    outcome="failed",
                )
            ]
            tracker.record_results(f"run-{i}", results)

        assert not tracker.is_flaky("tests/test_x.py::test_broken")

    def test_flakiness_score(self, tracker):
        """Verify correct flakiness score calculation."""
        for i in range(10):
            # 4 failures out of 10 = 0.4
            outcome = "failed" if i < 4 else "passed"
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_scoring",
                    outcome=outcome,
                )
            ]
            tracker.record_results(f"run-{i}", results)

        score = tracker.get_flakiness_score("tests/test_x.py::test_scoring")
        assert abs(score - 0.4) < 0.01

    def test_min_runs_threshold(self, tracker_db):
        """Test with too few runs should not be flagged as flaky by get_flaky_tests."""
        tracker = TestTracker(tracker_db)
        # Only 2 runs — below the default min_runs=3
        for i, outcome in enumerate(["passed", "failed"]):
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_few_runs",
                    outcome=outcome,
                )
            ]
            tracker.record_results(f"run-{i}", results)

        flaky = tracker.get_flaky_tests(min_runs=3)
        assert all(t["test_name"] != "tests/test_x.py::test_few_runs" for t in flaky)

    def test_window_filtering(self, tracker_db):
        """Old results outside the window should not count toward flakiness."""
        tracker = TestTracker(tracker_db)
        # Insert some results
        for i in range(4):
            results = [
                TestResult(
                    test_file="tests/test_x.py",
                    test_name="tests/test_x.py::test_old",
                    outcome="passed" if i % 2 == 0 else "failed",
                )
            ]
            tracker.record_results(f"run-{i}", results)

        # Manually backdate all results to 30 days ago
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        tracker_db.execute(
            "UPDATE test_results SET created_at = ? WHERE test_name = ?",
            (old_ts, "tests/test_x.py::test_old"),
            commit=True,
        )

        # With a 14-day window, these old results should not appear
        flaky = tracker.get_flaky_tests(window_days=14)
        assert all(t["test_name"] != "tests/test_x.py::test_old" for t in flaky)

    def test_flakiness_score_no_history(self, tracker):
        """Test with no history should have score 0.0."""
        score = tracker.get_flakiness_score("tests/test_nonexistent.py::test_x")
        assert score == 0.0
