"""Tests for AI support chatbot routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ortobahn.auth import generate_api_key, hash_api_key, key_prefix
from ortobahn.llm import LLMResponse
from ortobahn.web.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "chat_test.db"))
    monkeypatch.setenv("ORTOBAHN_SECRET_KEY", "test-secret-key-chat-tests!!!!!")
    monkeypatch.chdir(tmp_path)
    return create_app()


def _create_tenant(app) -> tuple[str, str]:
    """Create a test tenant and return (client_id, api_key)."""
    db = app.state.db
    client_id = db.create_client(
        {
            "name": "ChatTestCo",
            "description": "Test company for chat",
            "industry": "Testing",
            "email": "chat@test.com",
            "status": "active",
        }
    )
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key(client_id, hashed, prefix, "default")
    return client_id, raw_key


@pytest_asyncio.fixture
async def tenant_client(app):
    client_id, api_key = _create_tenant(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": api_key},
    ) as c:
        c._test_client_id = client_id
        yield c


class TestChatAuth:
    @pytest.mark.asyncio
    async def test_history_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/my/chat/history")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_send_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/my/chat/send", data={"message": "hello"})
            assert resp.status_code == 401


class TestChatHistory:
    @pytest.mark.asyncio
    async def test_empty_history_shows_greeting(self, tenant_client):
        resp = await tenant_client.get("/my/chat/history")
        assert resp.status_code == 200
        assert "support assistant" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_history_shows_messages(self, app, tenant_client):
        db = app.state.db
        cid = tenant_client._test_client_id
        db.save_chat_message(cid, "user", "Hello there")
        db.save_chat_message(cid, "assistant", "Hi! How can I help?")

        resp = await tenant_client.get("/my/chat/history")
        assert resp.status_code == 200
        assert "Hello there" in resp.text
        assert "How can I help" in resp.text


class TestChatSend:
    @pytest.mark.asyncio
    async def test_send_message(self, app, tenant_client):
        mock_response = LLMResponse(
            text="I can help with that!",
            input_tokens=100,
            output_tokens=50,
            model="claude-sonnet-4-5-20250929",
        )
        with patch("ortobahn.web.routes.chat.call_llm", return_value=mock_response):
            resp = await tenant_client.post(
                "/my/chat/send",
                data={"message": "How do I connect Bluesky?"},
            )
        assert resp.status_code == 200
        assert "How do I connect Bluesky?" in resp.text
        assert "I can help with that!" in resp.text

    @pytest.mark.asyncio
    async def test_send_empty_message(self, tenant_client):
        resp = await tenant_client.post(
            "/my/chat/send",
            data={"message": "   "},
        )
        assert resp.status_code == 200
        assert resp.text == ""

    @pytest.mark.asyncio
    async def test_llm_error_returns_graceful_message(self, tenant_client):
        with patch("ortobahn.web.routes.chat.call_llm", side_effect=RuntimeError("API down")):
            resp = await tenant_client.post(
                "/my/chat/send",
                data={"message": "test"},
            )
        assert resp.status_code == 200
        assert "sorry" in resp.text.lower() or "error" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_messages_persisted(self, app, tenant_client):
        mock_response = LLMResponse(
            text="Persisted response",
            input_tokens=100,
            output_tokens=50,
            model="claude-sonnet-4-5-20250929",
        )
        with patch("ortobahn.web.routes.chat.call_llm", return_value=mock_response):
            await tenant_client.post(
                "/my/chat/send",
                data={"message": "Persisted question"},
            )

        db = app.state.db
        cid = tenant_client._test_client_id
        history = db.get_chat_history(cid)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Persisted question"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Persisted response"


class TestChatDataScoping:
    @pytest.mark.asyncio
    async def test_cannot_see_other_clients_messages(self, app, tenant_client):
        db = app.state.db
        db.save_chat_message("default", "user", "SECRET MESSAGE")

        resp = await tenant_client.get("/my/chat/history")
        assert "SECRET MESSAGE" not in resp.text
