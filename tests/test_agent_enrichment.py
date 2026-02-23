"""Tests for the Enrichment Agent — profile auto-enrichment logic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.enrichment import EnrichmentAgent, _TextExtractor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enrichment_agent(test_db):
    return EnrichmentAgent(db=test_db, api_key="sk-ant-test")


# ---------------------------------------------------------------------------
# TestTextExtractor — HTML-to-text extraction
# ---------------------------------------------------------------------------


class TestTextExtractor:
    """Test the _TextExtractor HTMLParser subclass."""

    def test_basic_extraction(self):
        extractor = _TextExtractor()
        extractor.feed("<html><body><p>Hello World</p></body></html>")
        assert "Hello World" in " ".join(extractor.parts)

    def test_skips_script_tags(self):
        extractor = _TextExtractor()
        extractor.feed("<html><body><script>var x = 1;</script><p>Visible</p></body></html>")
        text = " ".join(extractor.parts)
        assert "var x" not in text
        assert "Visible" in text

    def test_skips_style_tags(self):
        extractor = _TextExtractor()
        extractor.feed("<html><style>.red { color: red; }</style><p>Content</p></html>")
        text = " ".join(extractor.parts)
        assert "color" not in text
        assert "Content" in text

    def test_skips_nav_and_footer(self):
        extractor = _TextExtractor()
        extractor.feed("<html><nav>Nav links</nav><main>Main content</main><footer>Footer stuff</footer></html>")
        text = " ".join(extractor.parts)
        assert "Nav links" not in text
        assert "Footer stuff" not in text
        assert "Main content" in text

    def test_skips_noscript(self):
        extractor = _TextExtractor()
        extractor.feed("<noscript>Enable JS</noscript><p>Real content</p>")
        text = " ".join(extractor.parts)
        assert "Enable JS" not in text
        assert "Real content" in text

    def test_strips_whitespace(self):
        extractor = _TextExtractor()
        extractor.feed("<p>   </p><p>Actual</p>")
        # Empty whitespace-only text should not be included
        assert "Actual" in extractor.parts
        assert len(extractor.parts) == 1

    def test_multiple_paragraphs(self):
        extractor = _TextExtractor()
        extractor.feed("<p>First</p><p>Second</p><p>Third</p>")
        assert len(extractor.parts) == 3


# ---------------------------------------------------------------------------
# TestFetchWebsite — network fetch (mocked)
# ---------------------------------------------------------------------------


class TestFetchWebsite:
    """Test _fetch_website with mocked HTTP requests."""

    def test_successful_fetch(self, enrichment_agent):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>We build great software</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            text = enrichment_agent._fetch_website("https://example.com")

        assert "great software" in text

    def test_empty_url_returns_empty(self, enrichment_agent):
        text = enrichment_agent._fetch_website("")
        assert text == ""

    def test_none_url_returns_empty(self, enrichment_agent):
        # URL is falsy
        text = enrichment_agent._fetch_website("")
        assert text == ""

    def test_http_error_returns_empty(self, enrichment_agent):
        import requests as req

        with patch("requests.get", side_effect=req.ConnectionError("Connection refused")):
            text = enrichment_agent._fetch_website("https://down.example.com")

        assert text == ""

    def test_timeout_returns_empty(self, enrichment_agent):
        import requests as req

        with patch("requests.get", side_effect=req.Timeout("Request timed out")):
            text = enrichment_agent._fetch_website("https://slow.example.com")

        assert text == ""

    def test_truncates_to_5000_chars(self, enrichment_agent):
        long_text = "A" * 10000
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = f"<html><body><p>{long_text}</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            text = enrichment_agent._fetch_website("https://long.example.com")

        assert len(text) <= 5000


# ---------------------------------------------------------------------------
# TestEnrichmentRun — full run() with mocked LLM
# ---------------------------------------------------------------------------


class TestEnrichmentRun:
    """Test the run() method with various client data and LLM responses."""

    def _make_llm_response(self, enrichment_dict: dict):
        from ortobahn.llm import LLMResponse

        return LLMResponse(
            text=json.dumps(enrichment_dict),
            input_tokens=200,
            output_tokens=300,
            model="test",
        )

    def test_full_enrichment_with_website(self, enrichment_agent):
        enrichment_data = {
            "target_audience": "SaaS founders and CTOs",
            "products": "API management platform, Developer portal",
            "competitive_positioning": "Faster setup than competitors",
            "key_messages": "Ship faster|Scale effortlessly|Developer-first",
            "content_pillars": "API best practices|Developer experience|Cloud scaling|Security",
            "company_story": "Founded to make APIs accessible. Now powers 10k companies.",
        }
        llm_resp = self._make_llm_response(enrichment_data)

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value="We build API tools for developers."),
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            result = enrichment_agent.run(
                run_id="enrich-1",
                client_data={
                    "name": "ApiCorp",
                    "industry": "SaaS",
                    "website": "https://apicorp.com",
                    "description": "API tools company",
                    "brand_voice": "",
                },
            )

        assert result["target_audience"] == "SaaS founders and CTOs"
        assert "API management" in result["products"]
        assert len(result) == 6

    def test_enrichment_without_website(self, enrichment_agent):
        enrichment_data = {
            "target_audience": "Small businesses",
            "products": "Marketing automation",
            "competitive_positioning": "Affordable and simple",
            "key_messages": "Easy marketing|Low cost",
            "content_pillars": "Small biz tips|Marketing 101",
            "company_story": "Built for the little guy.",
        }
        llm_resp = self._make_llm_response(enrichment_data)

        with (
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            result = enrichment_agent.run(
                run_id="enrich-2",
                client_data={
                    "name": "SmallBizTool",
                    "industry": "Marketing",
                    "website": "",
                    "description": "",
                    "brand_voice": "",
                },
            )

        assert result["target_audience"] == "Small businesses"

    def test_enrichment_bad_json_returns_empty(self, enrichment_agent):
        from ortobahn.llm import LLMResponse

        bad_resp = LLMResponse(
            text="Sorry, I cannot help with that.",
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value=""),
            patch.object(enrichment_agent, "call_llm", return_value=bad_resp),
        ):
            result = enrichment_agent.run(
                run_id="enrich-bad",
                client_data={"name": "BadCo", "industry": "Unknown"},
            )

        assert result == {}

    def test_enrichment_json_in_markdown_fences(self, enrichment_agent):
        from ortobahn.llm import LLMResponse

        inner = json.dumps({"target_audience": "Devs", "products": "SDK"})
        # LLM wraps in ```json fences — the agent strips backticks
        wrapped = f"```json\n{inner}\n```"
        llm_resp = LLMResponse(text=wrapped, input_tokens=100, output_tokens=100, model="test")

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value=""),
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            result = enrichment_agent.run(
                run_id="enrich-fenced",
                client_data={"name": "FencedCo", "industry": "Tech"},
            )

        assert result.get("target_audience") == "Devs"

    def test_enrichment_logs_decision(self, enrichment_agent, test_db):
        enrichment_data = {"target_audience": "Everyone"}
        llm_resp = self._make_llm_response(enrichment_data)

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value=""),
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            enrichment_agent.run(
                run_id="enrich-log",
                client_data={"name": "LogCo", "industry": "Logs"},
            )

        logs = test_db.get_recent_agent_logs(limit=5)
        assert any(log["agent_name"] == "enrichment" for log in logs)

    def test_missing_client_data_key_raises(self, enrichment_agent):
        """run() expects client_data kwarg; missing it should raise."""
        with pytest.raises(KeyError):
            enrichment_agent.run(run_id="enrich-no-data")

    def test_enrichment_with_minimal_client(self, enrichment_agent):
        """Client with only name — everything else blank."""
        enrichment_data = {
            "target_audience": "General public",
            "products": "Unknown",
            "competitive_positioning": "New entrant",
            "key_messages": "Coming soon",
            "content_pillars": "Updates",
            "company_story": "Just getting started.",
        }
        llm_resp = self._make_llm_response(enrichment_data)

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value=""),
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            result = enrichment_agent.run(
                run_id="enrich-minimal",
                client_data={"name": "MinimalCo"},
            )

        assert "target_audience" in result

    def test_idempotent_enrichment(self, enrichment_agent):
        """Running enrichment on an already-enriched profile should still work."""
        enrichment_data = {
            "target_audience": "Updated audience",
            "products": "Updated products",
            "competitive_positioning": "Still the best",
            "key_messages": "New msg 1|New msg 2",
            "content_pillars": "Updated pillars",
            "company_story": "Updated story.",
        }
        llm_resp = self._make_llm_response(enrichment_data)

        with (
            patch.object(enrichment_agent, "_fetch_website", return_value=""),
            patch.object(enrichment_agent, "call_llm", return_value=llm_resp),
        ):
            # First enrichment
            result1 = enrichment_agent.run(
                run_id="enrich-idem-1",
                client_data={
                    "name": "AlreadyRich",
                    "industry": "Finance",
                    "target_audience": "Old audience",
                    "products": "Old products",
                    "brand_voice": "Professional",
                },
            )
            # Second enrichment — same profile, should still return fresh data
            result2 = enrichment_agent.run(
                run_id="enrich-idem-2",
                client_data={
                    "name": "AlreadyRich",
                    "industry": "Finance",
                    "target_audience": result1["target_audience"],
                    "products": result1["products"],
                    "brand_voice": "Professional",
                },
            )

        assert result2["target_audience"] == "Updated audience"
