"""Tests for CIFixAgent._build_fix_playbook()."""

from __future__ import annotations

import uuid

import pytest

from ortobahn.agents.cifix import CIFixAgent
from ortobahn.models import CIFailureCategory


class TestCIFixPlaybook:
    @pytest.fixture(autouse=True)
    def setup(self, test_db):
        self.db = test_db
        self.agent = CIFixAgent(db=test_db, api_key="sk-ant-test")

    def _insert_fix_attempt(
        self,
        *,
        category: str = "test",
        strategy: str = "test fix via LLM",
        status: str = "success",
    ) -> None:
        """Insert a ci_fix_attempt row."""
        self.db.log_ci_fix_attempt(
            {
                "run_id": f"run-{uuid.uuid4().hex[:6]}",
                "gh_run_id": 12345,
                "failure_category": category,
                "fix_strategy": strategy,
                "status": status,
                "files_changed": ["ortobahn/foo.py"],
            }
        )

    def test_empty_playbook_no_history(self):
        """With no CI fix history, playbook is empty."""
        result = self.agent._build_fix_playbook(CIFailureCategory.TEST)
        assert result == ""

    def test_playbook_includes_successes(self):
        """Successful fix strategies appear under 'Strategies that WORKED'."""
        self._insert_fix_attempt(category="test", strategy="test fix via LLM", status="success")

        result = self.agent._build_fix_playbook(CIFailureCategory.TEST)

        assert "Strategies that WORKED" in result
        assert "test fix via LLM" in result

    def test_playbook_includes_failures_to_avoid(self):
        """Failed fix strategies appear under 'Strategies that FAILED'."""
        self._insert_fix_attempt(category="test", strategy="bad approach", status="failed")

        result = self.agent._build_fix_playbook(CIFailureCategory.TEST)

        assert "Strategies that FAILED" in result
        assert "bad approach" in result

    def test_playbook_deduplicates_strategies(self):
        """Duplicate strategies should only appear once."""
        self._insert_fix_attempt(category="lint", strategy="ruff auto-fix", status="success")
        self._insert_fix_attempt(category="lint", strategy="ruff auto-fix", status="success")
        self._insert_fix_attempt(category="lint", strategy="ruff auto-fix", status="success")

        result = self.agent._build_fix_playbook(CIFailureCategory.LINT)

        # "ruff auto-fix" should appear exactly once in WORKED section
        assert result.count("ruff auto-fix") == 1

    def test_playbook_success_rate_shown(self):
        """The historical success rate should be displayed."""
        self._insert_fix_attempt(category="test", strategy="strategy A", status="success")
        self._insert_fix_attempt(category="test", strategy="strategy B", status="failed")

        result = self.agent._build_fix_playbook(CIFailureCategory.TEST)

        assert "50%" in result

    def test_fix_context_injected(self, mock_llm_response):
        """When there is fix history, the context should be passed to fix methods."""
        # Insert some history
        self._insert_fix_attempt(category="test", strategy="read test + source", status="success")

        # Build the playbook directly and confirm it's non-empty
        playbook = self.agent._build_fix_playbook(CIFailureCategory.TEST)
        assert playbook != ""
        assert "read test + source" in playbook

    def test_successful_strategy_not_in_failures(self):
        """A strategy that succeeded should not appear in the FAILED list even if it also failed."""
        self._insert_fix_attempt(category="test", strategy="shared strategy", status="success")
        self._insert_fix_attempt(category="test", strategy="shared strategy", status="failed")

        result = self.agent._build_fix_playbook(CIFailureCategory.TEST)

        assert "Strategies that WORKED" in result
        assert "shared strategy" in result
        # It should NOT appear in the FAILED section
        if "Strategies that FAILED" in result:
            failed_section = result.split("Strategies that FAILED")[1]
            assert "shared strategy" not in failed_section
