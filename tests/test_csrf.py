"""Tests for CSRF protection."""

from __future__ import annotations

from starlette.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.web.csrf import generate_csrf_token, validate_csrf_token
from tests.conftest import csrf_form_data


def _make_app(tmp_path):
    from ortobahn.auth import create_session_token
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "csrf_test.db",
        secret_key="test-secret-for-csrf",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings

    client_id = db.create_client({"name": "CSRFTestCo"})
    token = create_session_token(client_id, settings.secret_key)

    tc = TestClient(app)
    tc.cookies.set("session", token)
    return tc, token, settings.secret_key, client_id, db


class TestCSRFToken:
    def test_generate_deterministic(self):
        """Same inputs produce same token."""
        t1 = generate_csrf_token("secret", "session123")
        t2 = generate_csrf_token("secret", "session123")
        assert t1 == t2

    def test_different_sessions(self):
        """Different sessions produce different tokens."""
        t1 = generate_csrf_token("secret", "session1")
        t2 = generate_csrf_token("secret", "session2")
        assert t1 != t2

    def test_validate_correct(self):
        """Valid token passes validation."""
        token = generate_csrf_token("secret", "session123")
        assert validate_csrf_token(token, "secret", "session123") is True

    def test_validate_wrong_token(self):
        """Wrong token fails validation."""
        assert validate_csrf_token("badtoken", "secret", "session123") is False

    def test_validate_empty_token(self):
        """Empty token fails validation."""
        assert validate_csrf_token("", "secret", "session123") is False


class TestCSRFMiddleware:
    def test_get_requests_pass(self, tmp_path):
        """GET requests are not CSRF-protected."""
        tc, _tok, _sk, _cid, _db = _make_app(tmp_path)
        resp = tc.get("/my/dashboard")
        assert resp.status_code == 200

    def test_post_without_csrf_blocked(self, tmp_path):
        """POST without CSRF token returns 403."""
        tc, _tok, _sk, cid, db = _make_app(tmp_path)
        resp = tc.post(
            "/my/settings/listening",
            data={"listening_enabled": "on"},
        )
        assert resp.status_code == 403

    def test_post_with_csrf_passes(self, tmp_path):
        """POST with valid CSRF token passes."""
        tc, tok, sk, cid, db = _make_app(tmp_path)
        resp = tc.post(
            "/my/settings/listening",
            data=csrf_form_data(tok, sk, {"listening_enabled": "on"}),
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_post_with_wrong_csrf_blocked(self, tmp_path):
        """POST with wrong CSRF token returns 403."""
        tc, _tok, _sk, cid, db = _make_app(tmp_path)
        resp = tc.post(
            "/my/settings/listening",
            data={"_csrf": "wrong-token", "listening_enabled": "on"},
        )
        assert resp.status_code == 403

    def test_post_with_header_csrf_passes(self, tmp_path):
        """POST with CSRF token in X-CSRF-Token header passes."""
        tc, tok, sk, cid, db = _make_app(tmp_path)
        csrf = generate_csrf_token(sk, tok)
        resp = tc.post(
            "/my/settings/listening",
            data={"listening_enabled": "on"},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_api_key_bypasses_csrf(self, tmp_path):
        """API key auth bypasses CSRF validation."""
        from ortobahn.auth import generate_api_key, hash_api_key, key_prefix
        from ortobahn.web.app import create_app

        settings = Settings(
            anthropic_api_key="sk-ant-test",
            db_path=tmp_path / "csrf_api_test.db",
            secret_key="test-secret-for-csrf-api",
        )
        app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
        db = Database(settings.db_path)
        app.state.db = db
        app.state.settings = settings

        client_id = db.create_client({"name": "APITestCo"})
        raw_key = generate_api_key()
        db.create_api_key(client_id, hash_api_key(raw_key), key_prefix(raw_key), "test")

        tc = TestClient(app)
        resp = tc.post(
            "/my/settings/listening",
            data={"listening_enabled": "on"},
            headers={"X-API-Key": raw_key},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_csrf_meta_tag_in_template(self, tmp_path):
        """CSRF meta tag is included in authenticated pages."""
        tc, _tok, _sk, _cid, _db = _make_app(tmp_path)
        resp = tc.get("/my/dashboard")
        assert resp.status_code == 200
        assert 'name="csrf-token"' in resp.text

    def test_non_tenant_post_not_csrf_protected(self, tmp_path):
        """POST to non-/my/ routes is not CSRF-protected."""
        from ortobahn.web.app import create_app

        settings = Settings(
            anthropic_api_key="sk-ant-test",
            db_path=tmp_path / "csrf_public_test.db",
            secret_key="test-secret",
        )
        app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
        db = Database(settings.db_path)
        app.state.db = db
        app.state.settings = settings

        tc = TestClient(app)
        resp = tc.get("/health")
        assert resp.status_code == 200


class TestArticleFrequencyGuard:
    def test_no_previous_article_allows_generation(self, tmp_path):
        """First article generation should always succeed."""
        tc, tok, sk, cid, db = _make_app(tmp_path)
        resp = tc.post(
            "/my/generate-article",
            data=csrf_form_data(tok, sk),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "msg=generating" in resp.headers["location"]

    def test_recent_article_blocks_generation(self, tmp_path):
        """Article within frequency window is blocked."""
        import uuid
        from datetime import datetime, timezone

        tc, tok, sk, cid, db = _make_app(tmp_path)
        # Insert a recent article
        db.execute(
            "INSERT INTO articles (id, client_id, run_id, title, body_markdown, word_count, status, created_at) "
            "VALUES (?, ?, 'test-run', 'Test Article', 'Body', 500, 'published', ?)",
            (str(uuid.uuid4()), cid, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )
        resp = tc.post(
            "/my/generate-article",
            data=csrf_form_data(tok, sk),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "detail=frequency" in resp.headers["location"]

    def test_override_bypasses_frequency(self, tmp_path):
        """Override flag bypasses frequency guard."""
        import uuid
        from datetime import datetime, timezone

        tc, tok, sk, cid, db = _make_app(tmp_path)
        db.execute(
            "INSERT INTO articles (id, client_id, run_id, title, body_markdown, word_count, status, created_at) "
            "VALUES (?, ?, 'test-run', 'Test Article', 'Body', 500, 'published', ?)",
            (str(uuid.uuid4()), cid, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )
        resp = tc.post(
            "/my/generate-article",
            data=csrf_form_data(tok, sk, {"_override": "1"}),
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "msg=generating" in resp.headers["location"]
