"""Tests for input validation on public routes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ortobahn.web.app import create_app
from ortobahn.web.routes.onboard import _is_internal_hostname, _validate_url

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "validation_test.db"))
    monkeypatch.chdir(tmp_path)
    application = create_app()
    cognito = MagicMock()
    cognito.sign_up.return_value = "mock-cognito-sub"
    application.state.cognito = cognito
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _valid_payload(**overrides) -> dict:
    """Return a valid onboard payload with optional field overrides."""
    base = {
        "name": "Jane Smith",
        "company": "AcmeCorp",
        "email": "jane@acme.com",
        "password": "SecurePass1",
        "industry": "SaaS",
        "website": "https://acme.com",
        "brand_voice": "Professional",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# OnboardRequest field length validation
# ---------------------------------------------------------------------------


class TestOnboardFieldLengths:
    """Verify min_length / max_length constraints on OnboardRequest fields."""

    @pytest.mark.asyncio
    async def test_empty_name_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(name=""))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_name_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(name="A" * 201))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_company_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(company=""))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_company_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(company="C" * 201))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_password_too_short_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(password="Short1"))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_password_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(password="P" * 129))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_industry_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(industry=""))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_industry_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(industry="I" * 101))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_website_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(website="https://x.com/" + "a" * 500))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_brand_voice_too_long_rejected(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload(brand_voice="V" * 501))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_payload_accepted(self, client):
        resp = await client.post("/api/onboard", json=_valid_payload())
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestURLValidation:
    """Test the _validate_url helper and its integration via the onboard endpoint."""

    def test_valid_url_passes(self):
        assert _validate_url("https://example.com") == "https://example.com"

    def test_bare_domain_gets_https(self):
        assert _validate_url("example.com") == "https://example.com"

    def test_empty_string_passes(self):
        assert _validate_url("") == ""

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="localhost"):
            _validate_url("http://localhost:8000")

    def test_127_0_0_1_rejected(self):
        with pytest.raises(ValueError, match="localhost|internal"):
            _validate_url("http://127.0.0.1/admin")

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="localhost|internal"):
            _validate_url("http://192.168.1.1")

    def test_10_x_ip_rejected(self):
        with pytest.raises(ValueError, match="localhost|internal"):
            _validate_url("http://10.0.0.1")

    def test_no_dot_in_hostname_rejected(self):
        with pytest.raises(ValueError, match="dot"):
            _validate_url("http://intranet/secret")

    def test_dot_local_rejected(self):
        with pytest.raises(ValueError, match="localhost|internal"):
            _validate_url("http://myhost.local")

    def test_dot_internal_rejected(self):
        with pytest.raises(ValueError, match="localhost|internal"):
            _validate_url("http://service.internal")

    @pytest.mark.asyncio
    async def test_localhost_url_rejected_via_api(self, client):
        resp = await client.post(
            "/api/onboard",
            json=_valid_payload(website="http://localhost:3000"),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_internal_ip_rejected_via_api(self, client):
        resp = await client.post(
            "/api/onboard",
            json=_valid_payload(website="http://192.168.1.1"),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_url_accepted_via_api(self, client):
        resp = await client.post(
            "/api/onboard",
            json=_valid_payload(website="https://acme.com"),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _is_internal_hostname unit tests
# ---------------------------------------------------------------------------


class TestIsInternalHostname:
    def test_localhost(self):
        assert _is_internal_hostname("localhost") is True

    def test_loopback_ipv4(self):
        assert _is_internal_hostname("127.0.0.1") is True

    def test_loopback_ipv6(self):
        assert _is_internal_hostname("::1") is True

    def test_private_10(self):
        assert _is_internal_hostname("10.0.0.1") is True

    def test_private_172(self):
        assert _is_internal_hostname("172.16.0.1") is True

    def test_private_192(self):
        assert _is_internal_hostname("192.168.0.1") is True

    def test_link_local(self):
        assert _is_internal_hostname("169.254.1.1") is True

    def test_public_ip(self):
        assert _is_internal_hostname("8.8.8.8") is False

    def test_regular_domain(self):
        assert _is_internal_hostname("example.com") is False

    def test_dot_local_domain(self):
        assert _is_internal_hostname("myhost.local") is True

    def test_zero_address(self):
        assert _is_internal_hostname("0.0.0.0") is True

    def test_empty_string(self):
        assert _is_internal_hostname("") is True


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_health_has_security_headers(self, client):
        resp = await client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in resp.headers.get("Permissions-Policy", "")

    @pytest.mark.asyncio
    async def test_api_endpoint_has_security_headers(self, client):
        resp = await client.get("/api/public/stats")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
