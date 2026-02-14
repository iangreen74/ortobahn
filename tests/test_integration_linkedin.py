"""Tests for LinkedIn integration (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestLinkedInClient:
    @patch("requests.post")
    def test_post_returns_url_and_urn(self, mock_post):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_resp = MagicMock()
        mock_resp.headers = {"X-RestLi-Id": "urn:li:share:123"}
        mock_resp.json.return_value = {"id": "urn:li:share:123"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        url, urn = client.post("Hello LinkedIn")

        assert urn == "urn:li:share:123"
        assert "linkedin.com" in url
        mock_post.assert_called_once()

    @patch("requests.get")
    def test_get_metrics(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "likesSummary": {"totalLikes": 5},
            "commentsSummary": {"totalFirstLevelComments": 2},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        metrics = client.get_post_metrics("urn:li:share:123")
        assert metrics.like_count == 5
        assert metrics.comment_count == 2

    @patch("requests.get")
    def test_get_metrics_failure_returns_empty(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_get.side_effect = Exception("API error")

        client = LinkedInClient("token", "urn:li:person:abc")
        metrics = client.get_post_metrics("urn:li:share:123")
        assert metrics.like_count == 0
        assert metrics.comment_count == 0

    def test_headers_set_correctly(self):
        from ortobahn.integrations.linkedin import LinkedInClient

        client = LinkedInClient("my-token", "urn:li:person:abc")
        assert client._headers["Authorization"] == "Bearer my-token"
        assert "X-Restli-Protocol-Version" in client._headers
