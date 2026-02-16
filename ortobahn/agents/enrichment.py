"""Enrichment Agent - auto-fills client profiles by analyzing their website."""

from __future__ import annotations

import json
import logging
from html.parser import HTMLParser

import requests

from ortobahn.agents.base import BaseAgent

logger = logging.getLogger("ortobahn.enrichment")


class _TextExtractor(HTMLParser):
    """Simple HTML-to-text extractor that skips scripts, styles, and navigation."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


class EnrichmentAgent(BaseAgent):
    name = "enrichment"
    prompt_file = "enrichment.txt"

    def _fetch_website(self, url: str, timeout: int = 15) -> str:
        """Fetch website text content. Returns empty string on failure."""
        if not url:
            return ""
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Ortobahn/1.0"})
            resp.raise_for_status()
            extractor = _TextExtractor()
            extractor.feed(resp.text)
            return " ".join(extractor.parts)[:5000]
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return ""

    def run(self, run_id: str, **kwargs) -> dict:
        """Enrich a single client. Expects client_data kwarg."""
        client_data = kwargs["client_data"]
        website_text = self._fetch_website(client_data.get("website", ""))

        user_message = f"""## Client Info
Name: {client_data.get("name", "")}
Industry: {client_data.get("industry", "")}
Description: {client_data.get("description", "")}
Brand Voice: {client_data.get("brand_voice", "")}
Website: {client_data.get("website", "")}

## Website Content (extracted text)
{website_text if website_text else "No website content available."}

Analyze this company and generate the following fields. Be specific — use real product names, actual competitors, and concrete details from the website content. If no website is available, generate reasonable defaults based on the company name and industry.

Respond with valid JSON only:
{{
    "target_audience": "Who they should be marketing to (specific segments)",
    "products": "Their main products/services (comma-separated)",
    "competitive_positioning": "How they differ from competitors",
    "key_messages": "3-4 core marketing messages (pipe-separated)",
    "content_pillars": "4-5 themes for content strategy (pipe-separated)",
    "company_story": "A brief brand narrative (2-3 sentences)"
}}"""

        response = self.call_llm(user_message)

        enrichment: dict = {}
        try:
            enrichment = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
        except (json.JSONDecodeError, KeyError):
            logger.error(f"Failed to parse enrichment response for {client_data.get('name')}")

        self.log_decision(
            run_id=run_id,
            input_summary=f"Enriching {client_data.get('name')}, website: {bool(website_text)}",
            output_summary=f"Filled {len(enrichment)} fields",
            reasoning="Auto-enrichment from website analysis",
            llm_response=response,
        )
        return enrichment
