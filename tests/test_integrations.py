"""Tests for external API integrations (all mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ortobahn.integrations.newsapi_client import get_trending_headlines
from ortobahn.integrations.rss import fetch_feeds
from ortobahn.integrations.trends import get_trending_searches


class TestNewsAPI:
    def test_no_api_key_returns_empty(self):
        result = get_trending_headlines("")
        assert result == []

    @patch("newsapi.NewsApiClient")
    def test_successful_fetch(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_top_headlines.return_value = {
            "articles": [
                {
                    "title": "AI News",
                    "description": "Big AI stuff",
                    "source": {"name": "TechCrunch"},
                    "url": "https://example.com",
                },
            ]
        }
        result = get_trending_headlines("test-key")
        assert len(result) == 1
        assert result[0].title == "AI News"


class TestGoogleTrends:
    @patch("pytrends.request.TrendReq")
    def test_successful_fetch(self, mock_trendreq_cls):
        import pandas as pd

        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends
        mock_pytrends.trending_searches.return_value = pd.DataFrame({0: ["AI", "crypto", "space"]})

        result = get_trending_searches()
        assert len(result) == 3

    @patch("pytrends.request.TrendReq", side_effect=Exception("blocked"))
    def test_failure_returns_empty(self, mock_trendreq):
        result = get_trending_searches()
        assert result == []


class TestRSS:
    @patch("ortobahn.integrations.rss.feedparser.parse")
    def test_successful_fetch(self, mock_parse):
        mock_parse.return_value = MagicMock(
            feed={"title": "Test Feed"},
            entries=[
                {"title": "Article 1", "summary": "Summary 1", "link": "https://example.com/1"},
                {"title": "Article 2", "summary": "Summary 2", "link": "https://example.com/2"},
            ],
        )
        result = fetch_feeds(["https://example.com/feed"])
        assert len(result) == 2
        assert result[0].title == "Article 1"

    @patch("ortobahn.integrations.rss.feedparser.parse")
    def test_failure_returns_empty(self, mock_parse):
        mock_parse.side_effect = Exception("network error")
        result = fetch_feeds(["https://bad.com/feed"])
        assert result == []
