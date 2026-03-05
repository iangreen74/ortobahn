"""Tests for tenant listening and engagement queue routes."""

from __future__ import annotations

import uuid

from starlette.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database


def _make_authenticated(tmp_path):
    """Create test app with authenticated session."""
    from ortobahn.auth import create_session_token
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "listening_test.db",
        secret_key="test-secret-key-for-listening-test",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings

    client_id = db.create_client({"name": "ListeningTestCo"})
    token = create_session_token(client_id, settings.secret_key)

    test_client = TestClient(app)
    test_client.cookies.set("session", token)
    return app, test_client, client_id, db


class TestTenantListening:
    def test_listening_page_renders(self, tmp_path):
        """Listening page renders 200."""
        _app, client, _cid, _db = _make_authenticated(tmp_path)
        resp = client.get("/my/listening")
        assert resp.status_code == 200
        assert "Listening" in resp.text

    def test_add_rule(self, tmp_path):
        """Adding a listening rule works."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        resp = client.post(
            "/my/listening/rules",
            data={"platform": "bluesky", "rule_type": "keyword", "value": "test_keyword", "priority": "2"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        rule = db.fetchone(
            "SELECT * FROM listening_rules WHERE client_id=? AND value='test_keyword'",
            (cid,),
        )
        assert rule is not None
        assert rule["platform"] == "bluesky"

    def test_add_rule_empty_rejected(self, tmp_path):
        """Empty value is rejected."""
        _app, client, _cid, _db = _make_authenticated(tmp_path)
        resp = client.post(
            "/my/listening/rules",
            data={"platform": "bluesky", "rule_type": "keyword", "value": "", "priority": "3"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers.get("location", "")

    def test_delete_rule(self, tmp_path):
        """Deleting a rule deactivates it."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        rule_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO listening_rules (id, client_id, platform, rule_type, value, priority, active) "
            "VALUES (?, ?, 'bluesky', 'keyword', 'delete_me', 3, 1)",
            (rule_id, cid),
            commit=True,
        )
        resp = client.post(f"/my/listening/rules/{rule_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        rule = db.fetchone("SELECT active FROM listening_rules WHERE id=?", (rule_id,))
        assert rule["active"] == 0

    def test_add_account(self, tmp_path):
        """Adding a tracked account works."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        resp = client.post(
            "/my/listening/accounts",
            data={"platform": "twitter", "handle": "competitor.x", "account_type": "competitor"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        acct = db.fetchone(
            "SELECT * FROM tracked_accounts WHERE client_id=? AND account_handle='competitor.x'",
            (cid,),
        )
        assert acct is not None
        assert acct["account_type"] == "competitor"

    def test_duplicate_account_rejected(self, tmp_path):
        """Duplicate tracked account is rejected."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        db.execute(
            "INSERT INTO tracked_accounts (id, client_id, platform, account_handle, account_type, active) "
            "VALUES (?, ?, 'twitter', 'dup.x', 'influencer', 1)",
            (str(uuid.uuid4()), cid),
            commit=True,
        )
        resp = client.post(
            "/my/listening/accounts",
            data={"platform": "twitter", "handle": "dup.x", "account_type": "influencer"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=duplicate_account" in resp.headers.get("location", "")


class TestTenantEngagementQueue:
    def test_engagement_page_renders(self, tmp_path):
        """Engagement page renders 200."""
        _app, client, _cid, _db = _make_authenticated(tmp_path)
        resp = client.get("/my/engagement")
        assert resp.status_code == 200
        assert "Engagement" in resp.text

    def test_skip_conversation(self, tmp_path):
        """Skipping a queued conversation works."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        conv_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status)
            VALUES (?, ?, 'bluesky', 'keyword', 'test',
                    'ext_1', 'uri_1', 'author.bsky', 'test post', 10, 0.8, 'queued')""",
            (conv_id, cid),
            commit=True,
        )
        resp = client.post(f"/my/engagement/{conv_id}/skip", follow_redirects=False)
        assert resp.status_code == 303
        conv = db.fetchone("SELECT status FROM discovered_conversations WHERE id=?", (conv_id,))
        assert conv["status"] == "skipped"


class TestListeningSettings:
    def test_listening_settings_toggle(self, tmp_path):
        """Listening settings toggle works."""
        _app, client, cid, db = _make_authenticated(tmp_path)
        resp = client.post(
            "/my/settings/listening",
            data={
                "listening_enabled": "on",
                "proactive_engagement_enabled": "on",
                "listening_max_conversations_per_cycle": "100",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        c = db.fetchone("SELECT * FROM clients WHERE id=?", (cid,))
        assert c["listening_enabled"] == 1
        assert c["proactive_engagement_enabled"] == 1
        assert c["listening_max_conversations_per_cycle"] == 100
