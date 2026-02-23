"""Tests for LinkedIn integration (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest


class TestLinkedInClient:
    @patch("httpx.post")
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

    @patch("httpx.get")
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

    @patch("httpx.get")
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


class TestVerifyPostExists:
    @patch("httpx.get")
    def test_returns_true_when_post_found(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        result = client.verify_post_exists("urn:li:share:123")
        assert result is True
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert "ugcPosts/urn:li:share:123" in call_url

    @patch("httpx.get")
    def test_returns_false_when_post_not_found(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        result = client.verify_post_exists("urn:li:share:999")
        assert result is False

    @patch("httpx.get")
    def test_returns_none_on_network_error(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_get.side_effect = httpx.ConnectError("connection refused")

        client = LinkedInClient("token", "urn:li:person:abc")
        result = client.verify_post_exists("urn:li:share:123")
        assert result is None

    @patch("httpx.get")
    def test_raises_on_401(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_request = httpx.Request("GET", "https://api.linkedin.com/v2/ugcPosts/urn:li:share:123")
        mock_response = httpx.Response(401, request=mock_request)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=mock_request, response=mock_response
        )
        mock_get.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        with pytest.raises(httpx.HTTPStatusError):
            client.verify_post_exists("urn:li:share:123")
        assert client._credentials_valid is False


class TestGetProfile:
    @patch("httpx.get")
    def test_returns_profile_info(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "localizedFirstName": "Jane",
            "localizedLastName": "Doe",
            "vanityName": "janedoe",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = LinkedInClient("token", "urn:li:person:abc")
        profile = client.get_profile()

        assert profile["first_name"] == "Jane"
        assert profile["last_name"] == "Doe"
        assert profile["vanity_name"] == "janedoe"
        call_url = mock_get.call_args[0][0]
        assert call_url.endswith("/v2/me")

    @patch("httpx.get")
    def test_returns_empty_dict_on_network_error(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_get.side_effect = httpx.ConnectError("connection refused")

        client = LinkedInClient("token", "urn:li:person:abc")
        profile = client.get_profile()
        assert profile == {}

    @patch("httpx.get")
    def test_raises_on_401(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_request = httpx.Request("GET", "https://api.linkedin.com/v2/me")
        mock_response = httpx.Response(401, request=mock_request)
        mock_get.side_effect = httpx.HTTPStatusError("401 Unauthorized", request=mock_request, response=mock_response)

        client = LinkedInClient("token", "urn:li:person:abc")
        with pytest.raises(httpx.HTTPStatusError):
            client.get_profile()
        assert client._credentials_valid is False


class TestAuthRetryDecorator:
    @patch("httpx.post")
    def test_401_marks_credentials_invalid_on_post(self, mock_post):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_request = httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts")
        mock_response = httpx.Response(401, request=mock_request)
        mock_post.side_effect = httpx.HTTPStatusError("401 Unauthorized", request=mock_request, response=mock_response)

        client = LinkedInClient("token", "urn:li:person:abc")
        assert client._credentials_valid is True

        with pytest.raises(httpx.HTTPStatusError):
            client.post("Hello")
        assert client._credentials_valid is False

    @patch("httpx.get")
    def test_401_marks_credentials_invalid_on_metrics(self, mock_get):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_request = httpx.Request("GET", "https://api.linkedin.com/v2/socialActions/x")
        mock_response = httpx.Response(401, request=mock_request)
        mock_get.side_effect = httpx.HTTPStatusError("401 Unauthorized", request=mock_request, response=mock_response)

        client = LinkedInClient("token", "urn:li:person:abc")
        with pytest.raises(httpx.HTTPStatusError):
            client.get_post_metrics("urn:li:share:123")
        assert client._credentials_valid is False

    @patch("httpx.post")
    def test_non_401_error_does_not_mark_credentials(self, mock_post):
        from ortobahn.integrations.linkedin import LinkedInClient

        mock_request = httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts")
        mock_response = httpx.Response(500, request=mock_request)
        mock_post.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=mock_request, response=mock_response
        )

        client = LinkedInClient("token", "urn:li:person:abc")
        with pytest.raises(httpx.HTTPStatusError):
            client.post("Hello")
        assert client._credentials_valid is True

    def test_credentials_valid_true_by_default(self):
        from ortobahn.integrations.linkedin import LinkedInClient

        client = LinkedInClient("token", "urn:li:person:abc")
        assert client._credentials_valid is True
