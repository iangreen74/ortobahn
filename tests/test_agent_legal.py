"""Tests for Legal Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.legal import LegalAgent
from ortobahn.models import LegalReport

VALID_LEGAL_JSON = json.dumps(
    {
        "documents_generated": [
            {
                "document_type": "terms_of_service",
                "title": "Terms of Service",
                "content": "# Terms of Service\n\nThese terms govern your use of Ortobahn...",
                "version": "1.0",
                "effective_date": "2026-02-21",
            },
            {
                "document_type": "privacy_policy",
                "title": "Privacy Policy",
                "content": "# Privacy Policy\n\nOrtobahn respects your privacy...",
                "version": "1.0",
                "effective_date": "2026-02-21",
            },
        ],
        "compliance_gaps": [
            {
                "area": "cookie_consent",
                "severity": "warning",
                "description": "No cookie consent banner implemented",
                "recommendation": "Add cookie consent UI",
            }
        ],
        "recommendations": ["Add GDPR data export endpoint", "Implement cookie consent banner"],
        "summary": "Generated ToS and Privacy Policy. One compliance gap identified.",
    }
)


class TestLegalAgent:
    def test_generates_documents(self, test_db, mock_llm_response):
        agent = LegalAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_LEGAL_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result, LegalReport)
        assert len(result.documents_generated) == 2
        doc_types = [d.document_type for d in result.documents_generated]
        assert "terms_of_service" in doc_types
        assert "privacy_policy" in doc_types

    def test_saves_documents_to_db(self, test_db, mock_llm_response):
        agent = LegalAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_LEGAL_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-1")

        # Verify documents were saved to DB
        tos = test_db.get_legal_document("terms_of_service")
        assert tos is not None
        assert "Terms of Service" in tos["title"]

        pp = test_db.get_legal_document("privacy_policy")
        assert pp is not None
        assert "Privacy Policy" in pp["title"]

    def test_skips_existing_documents(self, test_db, mock_llm_response):
        """If docs already exist, agent should request review instead of generation."""
        # Pre-populate both documents
        test_db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "terms_of_service",
                "title": "Existing ToS",
                "content": "Existing content",
                "version": "1.0",
            }
        )
        test_db.save_legal_document(
            {
                "client_id": "default",
                "document_type": "privacy_policy",
                "title": "Existing PP",
                "content": "Existing content",
                "version": "1.0",
            }
        )

        # Return a review response (no new documents)
        review_json = json.dumps(
            {
                "documents_generated": [],
                "compliance_gaps": [],
                "recommendations": ["All documents up to date"],
                "summary": "All documents current. No gaps.",
            }
        )
        agent = LegalAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=review_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-2")

        assert len(result.documents_generated) == 0

    def test_fallback_on_bad_json(self, test_db, mock_llm_response):
        agent = LegalAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text="Not valid JSON output")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result, LegalReport)

    def test_logs_decision(self, test_db, mock_llm_response):
        agent = LegalAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_LEGAL_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-1")

        logs = test_db.get_recent_agent_logs(limit=5)
        assert any(log["agent_name"] == "legal" for log in logs)
