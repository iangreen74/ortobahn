"""Tests for content guardrails system."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ortobahn.content_guardrails import (
    GLOBAL_RULES,
    GuardrailResult,
    _build_rules_text,
    evaluate_draft,
    evaluate_drafts,
    get_custom_guardrails,
    get_global_rules,
    needs_recheck,
    save_custom_guardrails,
    save_guardrail_result,
)
from ortobahn.models import DraftPost, Platform


class TestGlobalRules:
    def test_global_rules_not_empty(self):
        assert len(GLOBAL_RULES) > 0

    def test_each_rule_has_required_fields(self):
        for rule in GLOBAL_RULES:
            assert "id" in rule
            assert "rule" in rule
            assert "severity" in rule
            assert rule["severity"] in ("block", "warn")

    def test_get_global_rules(self):
        rules = get_global_rules()
        assert rules is GLOBAL_RULES


class TestGuardrailResult:
    def test_clean_result(self):
        r = GuardrailResult(violations=[], clean=True)
        assert r.clean
        assert not r.has_blocks
        assert not r.has_warnings

    def test_warn_result(self):
        r = GuardrailResult(
            violations=[{"rule_id": "no-spam", "severity": "warn", "explanation": "clickbait"}],
            clean=False,
        )
        assert not r.clean
        assert not r.has_blocks
        assert r.has_warnings

    def test_block_result(self):
        r = GuardrailResult(
            violations=[{"rule_id": "no-hate", "severity": "block", "explanation": "slur"}],
            clean=False,
        )
        assert r.has_blocks
        assert not r.has_warnings

    def test_json_roundtrip(self):
        r = GuardrailResult(
            violations=[{"rule_id": "test", "severity": "warn", "explanation": "test reason"}],
            clean=False,
        )
        serialized = r.to_json()
        parsed = GuardrailResult.from_json(serialized)
        assert parsed.violations == r.violations
        assert parsed.clean == r.clean


class TestBuildRulesText:
    def test_global_only(self):
        text = _build_rules_text()
        assert "Global Platform Rules" in text
        assert "no-hate-speech" in text

    def test_with_custom(self):
        text = _build_rules_text("Never mention competitors\nNo pricing talk")
        assert "Custom Client Rules" in text
        assert "custom-1" in text
        assert "custom-2" in text
        assert "Never mention competitors" in text

    def test_empty_custom_ignored(self):
        text = _build_rules_text("")
        assert "Custom Client Rules" not in text

    def test_whitespace_custom_ignored(self):
        text = _build_rules_text("   \n  \n  ")
        assert "Custom Client Rules" not in text


class TestCustomGuardrails:
    def test_save_and_get(self, test_db):
        test_db.create_client({"id": "gr_test", "name": "Test"}, start_trial=False)
        save_custom_guardrails(test_db, "gr_test", "No competitor mentions")
        result = get_custom_guardrails(test_db, "gr_test")
        assert result == "No competitor mentions"

    def test_truncates_at_1000(self, test_db):
        test_db.create_client({"id": "gr_trunc", "name": "Test"}, start_trial=False)
        long_text = "x" * 2000
        save_custom_guardrails(test_db, "gr_trunc", long_text)
        result = get_custom_guardrails(test_db, "gr_trunc")
        assert len(result) == 1000

    def test_empty_for_new_client(self, test_db):
        test_db.create_client({"id": "gr_empty", "name": "Test"}, start_trial=False)
        assert get_custom_guardrails(test_db, "gr_empty") == ""


class TestEvaluateDraft:
    @patch("ortobahn.content_guardrails.call_llm")
    def test_clean_draft(self, mock_llm):
        mock_response = MagicMock()
        mock_response.text = '{"violations": [], "clean": true}'
        mock_llm.return_value = mock_response

        draft = DraftPost(
            text="Check out our new product launch!",
            source_idea="product",
            reasoning="standard",
            confidence=0.9,
            platform=Platform.BLUESKY,
        )
        result = evaluate_draft(draft)
        assert result.clean
        assert result.violations == []
        mock_llm.assert_called_once()

    @patch("ortobahn.content_guardrails.call_llm")
    def test_violation_detected(self, mock_llm):
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "violations": [
                    {"rule_id": "no-spam-manipulation", "severity": "warn", "explanation": "engagement bait"}
                ],
                "clean": False,
            }
        )
        mock_llm.return_value = mock_response

        draft = DraftPost(
            text="YOU WON'T BELIEVE THIS!!!",
            source_idea="clickbait",
            reasoning="test",
            confidence=0.8,
            platform=Platform.TWITTER,
        )
        result = evaluate_draft(draft)
        assert not result.clean
        assert len(result.violations) == 1
        assert result.has_warnings

    @patch("ortobahn.content_guardrails.call_llm")
    def test_llm_failure_returns_clean(self, mock_llm):
        mock_llm.side_effect = RuntimeError("API down")
        draft = DraftPost(
            text="Normal post", source_idea="t", reasoning="r", confidence=0.8
        )
        result = evaluate_draft(draft)
        assert result.clean
        assert result.violations == []


class TestEvaluateDrafts:
    @patch("ortobahn.content_guardrails.call_llm")
    def test_batch_evaluation(self, mock_llm):
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            [
                {"violations": [], "clean": True},
                {
                    "violations": [{"rule_id": "no-hate-speech", "severity": "block", "explanation": "slur"}],
                    "clean": False,
                },
            ]
        )
        mock_llm.return_value = mock_response

        drafts = [
            DraftPost(text="Good post", source_idea="t", reasoning="r", confidence=0.9),
            DraftPost(text="Bad post", source_idea="t", reasoning="r", confidence=0.9),
        ]
        results = evaluate_drafts(drafts)
        assert len(results) == 2
        assert results[0].clean
        assert not results[1].clean
        assert results[1].has_blocks

    def test_empty_drafts(self):
        assert evaluate_drafts([]) == []

    @patch("ortobahn.content_guardrails.call_llm")
    def test_batch_failure_returns_clean(self, mock_llm):
        mock_llm.side_effect = RuntimeError("fail")
        drafts = [DraftPost(text="x", source_idea="t", reasoning="r", confidence=0.8)]
        results = evaluate_drafts(drafts)
        assert len(results) == 1
        assert results[0].clean


class TestSaveGuardrailResult:
    def test_saves_violations(self, test_db):
        test_db.create_client({"id": "gr_save", "name": "T"}, start_trial=False)
        pid = test_db.save_post(text="test", run_id="r1", client_id="gr_save")
        result = GuardrailResult(
            violations=[{"rule_id": "no-spam", "severity": "warn", "explanation": "bait"}],
            clean=False,
        )
        save_guardrail_result(test_db, pid, result)
        row = test_db.fetchone(
            "SELECT guardrail_violations, guardrail_checked_at FROM posts WHERE id=?", (pid,)
        )
        assert row["guardrail_checked_at"] is not None
        violations = json.loads(row["guardrail_violations"])
        assert len(violations["violations"]) == 1
        assert violations["violations"][0]["rule_id"] == "no-spam"

    def test_clean_result_saves_null_violations(self, test_db):
        test_db.create_client({"id": "gr_clean", "name": "T"}, start_trial=False)
        pid = test_db.save_post(text="test", run_id="r1", client_id="gr_clean")
        result = GuardrailResult(violations=[], clean=True)
        save_guardrail_result(test_db, pid, result)
        row = test_db.fetchone("SELECT guardrail_violations FROM posts WHERE id=?", (pid,))
        assert row["guardrail_violations"] is None


class TestNeedsRecheck:
    def test_never_checked(self):
        assert needs_recheck({"guardrail_checked_at": None, "edited_at": None})

    def test_checked_no_edit(self):
        assert not needs_recheck({"guardrail_checked_at": "2024-01-01T00:00:00", "edited_at": None})

    def test_edit_after_check(self):
        assert needs_recheck(
            {"guardrail_checked_at": "2024-01-01T00:00:00", "edited_at": "2024-01-02T00:00:00"}
        )

    def test_check_after_edit(self):
        assert not needs_recheck(
            {"guardrail_checked_at": "2024-01-02T00:00:00", "edited_at": "2024-01-01T00:00:00"}
        )


class TestMigration047:
    def test_guardrail_columns_on_posts(self, test_db):
        test_db.fetchall("SELECT guardrail_violations, guardrail_checked_at FROM posts LIMIT 1")

    def test_custom_guardrails_on_clients(self, test_db):
        test_db.fetchall("SELECT custom_guardrails FROM clients LIMIT 1")
