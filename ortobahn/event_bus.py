"""Internal event bus — typed, persistent, auditable agent triggers."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.config import Settings
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.event_bus")

# Event type constants
EVENT_ENGAGEMENT_SPIKE = "engagement.spike"
EVENT_ENGAGEMENT_DROP = "engagement.drop"
EVENT_POST_VIRAL = "post.viral"
EVENT_POST_FAILED = "post.failed"
EVENT_STRATEGY_EXPIRED = "strategy.expired"
EVENT_THRESHOLD_SHIFTED = "threshold.shifted"
EVENT_GRADUATION_CHANGED = "graduation.changed"

EventHandler = Callable[["Database", "Settings", dict], str]
_HANDLERS: dict[str, tuple[str, EventHandler]] = {}


def register_handler(event_type: str, agent_name: str, handler: EventHandler) -> None:
    _HANDLERS[event_type] = (agent_name, handler)


class EventBus:
    def __init__(self, db: Database) -> None:
        self.db = db

    def emit(self, event_type: str, client_id: str, payload: dict | None = None) -> str:
        event_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        self.db.execute(
            """INSERT INTO agent_events (id, event_type, client_id, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event_id, event_type, client_id, json.dumps(payload or {}), now),
            commit=True,
        )
        logger.info("Event emitted: %s [%s] client=%s", event_id, event_type, client_id)
        return event_id

    def get_pending(self, limit: int = 50) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_events WHERE processed_at IS NULL ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def mark_processed(self, event_id: str, handler_agent: str, result: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        self.db.execute(
            "UPDATE agent_events SET processed_at=?, handler_agent=?, handler_result=? WHERE id=?",
            (now, handler_agent, result, event_id),
            commit=True,
        )

    def get_recent(self, client_id: str | None = None, limit: int = 20) -> list[dict]:
        if client_id:
            return self.db.fetchall(
                "SELECT * FROM agent_events WHERE client_id=? ORDER BY created_at DESC LIMIT ?",
                (client_id, limit),
            )
        return self.db.fetchall(
            "SELECT * FROM agent_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )


# --- Built-in handlers ---


def _handle_strategy_expired(db: Database, settings: Settings, event: dict) -> str:
    """Publish insight so CEO picks it up next cycle."""
    from ortobahn.shared_insights import SharedInsightBus

    bus = SharedInsightBus(db)
    bus.publish(
        source_agent="event_bus",
        insight_type="client_health",
        content=f"Strategy expired for client {event['client_id']} — urgent refresh needed",
        confidence=0.9,
        metadata={"event_id": event["id"], "client_id": event["client_id"]},
    )
    return "insight_published"


def _handle_engagement_spike(db: Database, settings: Settings, event: dict) -> str:
    """Publish content trend insight."""
    payload = (
        json.loads(event.get("payload", "{}")) if isinstance(event.get("payload"), str) else event.get("payload", {})
    )
    from ortobahn.shared_insights import SharedInsightBus

    bus = SharedInsightBus(db)
    bus.publish(
        source_agent="event_bus",
        insight_type="content_trend",
        content=f"Engagement event: {payload.get('detail', event['event_type'])}",
        confidence=0.8,
        metadata=payload,
    )
    return "insight_published"


def _handle_post_failed(db: Database, settings: Settings, event: dict) -> str:
    """Count recent failures, alert if threshold exceeded."""
    client_id = event["client_id"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()  # noqa: UP017
    row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM agent_events WHERE event_type=? AND client_id=? AND created_at>=?",
        (EVENT_POST_FAILED, client_id, cutoff),
    )
    failure_count = row["cnt"] if row else 0
    result = f"failure_count_24h={failure_count}"

    if failure_count >= 3 and getattr(settings, "slack_webhook_url", None):
        try:
            from ortobahn.integrations.slack import send_slack_message_deduped

            send_slack_message_deduped(
                settings.slack_webhook_url,
                f":warning: {failure_count} post failures in 24h for client {client_id}",
                fingerprint=f"post-fail-alert-{client_id}",
                cooldown_minutes=60,
            )
            result += ", alert_sent"
        except Exception:
            pass

    return result


def _handle_graduation_changed(db: Database, settings: Settings, event: dict) -> str:
    """Notify via Slack."""
    payload = (
        json.loads(event.get("payload", "{}")) if isinstance(event.get("payload"), str) else event.get("payload", {})
    )
    new_status = payload.get("new_status", "unknown")
    if getattr(settings, "slack_webhook_url", None):
        try:
            from ortobahn.integrations.slack import send_slack_message

            send_slack_message(
                settings.slack_webhook_url,
                f"Client {event['client_id']} auto-publish graduation: {new_status}",
            )
        except Exception:
            pass
    return f"notified: {new_status}"


def _handle_threshold_shifted(db: Database, settings: Settings, event: dict) -> str:
    """Store observation memory for strategist."""
    payload = (
        json.loads(event.get("payload", "{}")) if isinstance(event.get("payload"), str) else event.get("payload", {})
    )
    try:
        from ortobahn.memory import MemoryStore
        from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

        store = MemoryStore(db)
        store.remember(
            AgentMemory(
                agent_name="strategist",
                client_id=event["client_id"],
                memory_type=MemoryType.OBSERVATION,
                category=MemoryCategory.CALIBRATION,
                content={
                    "summary": f"Confidence threshold shifted: {payload.get('detail', 'see payload')}",
                    "payload": payload,
                },
                confidence=0.7,
            )
        )
    except Exception as e:
        logger.warning("Memory store failed: %s", e)
        return f"memory_failed: {e}"
    return "memory_stored"


# Register handlers
register_handler(EVENT_STRATEGY_EXPIRED, "ceo", _handle_strategy_expired)
register_handler(EVENT_ENGAGEMENT_SPIKE, "creator", _handle_engagement_spike)
register_handler(EVENT_ENGAGEMENT_DROP, "creator", _handle_engagement_spike)
register_handler(EVENT_POST_VIRAL, "creator", _handle_engagement_spike)
register_handler(EVENT_POST_FAILED, "sre", _handle_post_failed)
register_handler(EVENT_GRADUATION_CHANGED, "ops", _handle_graduation_changed)
register_handler(EVENT_THRESHOLD_SHIFTED, "strategist", _handle_threshold_shifted)


def process_pending_events(db: Database, settings: Settings, limit: int = 50) -> dict:
    """Process all pending events. Returns {"processed": N, "errors": N}."""
    bus = EventBus(db)
    pending = bus.get_pending(limit=limit)
    if not pending:
        return {"processed": 0, "errors": 0}

    processed = 0
    errors = 0
    for event in pending:
        event_type = event["event_type"]
        handler_entry = _HANDLERS.get(event_type)
        if not handler_entry:
            bus.mark_processed(event["id"], "none", f"no_handler_for_{event_type}")
            processed += 1
            continue
        agent_name, handler_fn = handler_entry
        try:
            result = handler_fn(db, settings, event)
            bus.mark_processed(event["id"], agent_name, result)
            processed += 1
        except Exception as e:
            logger.warning("Event handler failed for %s [%s]: %s", event["id"], event_type, e)
            bus.mark_processed(event["id"], agent_name, f"error: {e}")
            errors += 1

    return {"processed": processed, "errors": errors}
