"""Tests for Slack bidirectional integration."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database


def _create_slack_app(tmp_path):
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "slack_test.db",
        secret_key="test-secret-key-for-slack-tests!",
        slack_signing_secret="test-signing-secret",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    app.state.cognito = MagicMock()
    return app, db


def _slack_signature(body: str, secret: str = "test-signing-secret") -> dict:
    """Generate valid Slack signature headers."""
    ts = str(int(time.time()))
    sig_base = f"v0:{ts}:{body}"
    sig = "v0=" + hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


class TestSlackCommands:
    def test_status_no_runs(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        client = TestClient(app)
        body = "command=%2Fortobahn&text=status"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "No recent" in resp.json()["text"]
        db.close()

    def test_status_with_runs(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        db.start_pipeline_run("run-1", mode="single")
        db.complete_pipeline_run("run-1", posts_published=3)
        client = TestClient(app)
        body = "command=%2Fortobahn&text=status"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "completed" in resp.json()["text"]
        db.close()

    def test_approve_draft(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        pid = db.save_post(text="Test draft", run_id="r1", status="draft")
        client = TestClient(app)
        body = f"command=%2Fortobahn&text=approve+{pid}"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "approved" in resp.json()["text"]
        post = db.get_post(pid)
        assert post["status"] == "approved"
        db.close()

    def test_reject_draft(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        pid = db.save_post(text="Bad draft", run_id="r1", status="draft")
        client = TestClient(app)
        body = f"command=%2Fortobahn&text=reject+{pid}"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "rejected" in resp.json()["text"]
        db.close()

    def test_approve_nonexistent(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        client = TestClient(app)
        body = "command=%2Fortobahn&text=approve+nonexistent"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.json()["text"]
        db.close()

    def test_help_text(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        client = TestClient(app)
        body = "command=%2Fortobahn&text="
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={**_slack_signature(body), "content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "Usage" in resp.json()["text"]
        db.close()

    def test_invalid_signature_rejected(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        client = TestClient(app)
        body = "command=%2Fortobahn&text=status"
        resp = client.post(
            "/api/slack/commands",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": "1",
                "X-Slack-Signature": "v0=bad",
                "content-type": "application/x-www-form-urlencoded",
            },
        )
        assert resp.status_code == 401
        db.close()


class TestSlackInteractions:
    def test_approve_button(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        pid = db.save_post(text="Draft post", run_id="r1", status="draft")
        client = TestClient(app)
        payload = json.dumps({"actions": [{"action_id": "approve_post", "value": pid}]})
        resp = client.post("/api/slack/interactions", data={"payload": payload})
        assert resp.status_code == 200
        assert "approved" in resp.json()["text"]
        db.close()

    def test_reject_button(self, tmp_path):
        app, db = _create_slack_app(tmp_path)
        pid = db.save_post(text="Draft post", run_id="r1", status="draft")
        client = TestClient(app)
        payload = json.dumps({"actions": [{"action_id": "reject_post", "value": pid}]})
        resp = client.post("/api/slack/interactions", data={"payload": payload})
        assert resp.status_code == 200
        assert "rejected" in resp.json()["text"]
        db.close()
