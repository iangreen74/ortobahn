"""Webhook dispatch — sends event notifications to registered webhook URLs."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.webhooks")

# Event types
EVENT_POST_PUBLISHED = "post.published"
EVENT_POST_FAILED = "post.failed"
EVENT_PIPELINE_COMPLETED = "pipeline.completed"
EVENT_PIPELINE_FAILED = "pipeline.failed"
EVENT_ARTICLE_PUBLISHED = "article.published"
EVENT_STRATEGY_CREATED = "strategy.created"
EVENT_DRAFT_CREATED = "draft.created"

ALL_EVENTS = [
    EVENT_POST_PUBLISHED,
    EVENT_POST_FAILED,
    EVENT_PIPELINE_COMPLETED,
    EVENT_PIPELINE_FAILED,
    EVENT_ARTICLE_PUBLISHED,
    EVENT_STRATEGY_CREATED,
    EVENT_DRAFT_CREATED,
]


def register_webhook(
    db: Database,
    client_id: str,
    url: str,
    events: list[str] | None = None,
    secret: str | None = None,
) -> str:
    """Register a new webhook. Returns webhook ID."""
    webhook_id = str(uuid.uuid4())
    event_str = ",".join(events) if events else "*"
    if not secret:
        secret = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:32]
    db.execute(
        "INSERT INTO webhooks (id, client_id, url, events, secret) VALUES (?, ?, ?, ?, ?)",
        (webhook_id, client_id, url, event_str, secret),
        commit=True,
    )
    return webhook_id


def delete_webhook(db: Database, webhook_id: str, client_id: str) -> bool:
    """Delete a webhook. Returns True if deleted."""
    db.execute(
        "DELETE FROM webhooks WHERE id=? AND client_id=?",
        (webhook_id, client_id),
        commit=True,
    )
    return True


def list_webhooks(db: Database, client_id: str) -> list[dict]:
    """List all webhooks for a client."""
    return db.fetchall(
        "SELECT id, url, events, active, created_at, last_triggered_at, failure_count "
        "FROM webhooks WHERE client_id=? ORDER BY created_at DESC",
        (client_id,),
    )


def dispatch_event(db: Database, client_id: str, event_type: str, payload: dict) -> int:
    """Dispatch an event to all matching webhooks. Returns number of webhooks notified."""
    webhooks = db.fetchall(
        "SELECT id, url, events, secret, failure_count FROM webhooks WHERE client_id=? AND active=1",
        (client_id,),
    )

    notified = 0
    for wh in webhooks:
        events = wh["events"]
        if events != "*" and event_type not in events.split(","):
            continue

        body = json.dumps(
            {
                "event": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "client_id": client_id,
                "data": payload,
            },
            default=str,
        )

        headers = {"Content-Type": "application/json"}
        if wh["secret"]:
            sig = hmac.new(wh["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Ortobahn-Signature"] = f"sha256={sig}"

        try:
            resp = httpx.post(wh["url"], content=body, headers=headers, timeout=10)
            resp.raise_for_status()
            db.execute(
                "UPDATE webhooks SET last_triggered_at=?, failure_count=0 WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), wh["id"]),
                commit=True,
            )
            notified += 1
        except Exception as e:
            logger.warning(f"Webhook {wh['id']} delivery failed: {e}")
            db.execute(
                "UPDATE webhooks SET failure_count=failure_count+1 WHERE id=?",
                (wh["id"],),
                commit=True,
            )
            # Disable after 10 consecutive failures
            if (wh.get("failure_count", 0) or 0) + 1 >= 10:
                db.execute("UPDATE webhooks SET active=0 WHERE id=?", (wh["id"],), commit=True)
                logger.warning(f"Webhook {wh['id']} disabled after 10 consecutive failures")

    return notified
