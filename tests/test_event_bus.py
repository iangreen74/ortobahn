"""Tests for ortobahn.event_bus — EventBus, handlers, and process_pending_events."""

from __future__ import annotations

import json

from ortobahn.event_bus import (
    _HANDLERS,
    EVENT_ENGAGEMENT_SPIKE,
    EVENT_POST_FAILED,
    EVENT_STRATEGY_EXPIRED,
    EventBus,
    _handle_post_failed,
    _handle_strategy_expired,
    process_pending_events,
)


class TestEventBus:
    """Core EventBus CRUD operations."""

    def test_emit_and_get_pending(self, test_db):
        bus = EventBus(test_db)
        event_id = bus.emit(EVENT_POST_FAILED, "client-1", {"reason": "timeout"})

        pending = bus.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == event_id
        assert pending[0]["event_type"] == EVENT_POST_FAILED
        assert pending[0]["client_id"] == "client-1"
        assert pending[0]["processed_at"] is None
        payload = json.loads(pending[0]["payload"])
        assert payload["reason"] == "timeout"

    def test_mark_processed_removes_from_pending(self, test_db):
        bus = EventBus(test_db)
        event_id = bus.emit(EVENT_POST_FAILED, "client-1")

        bus.mark_processed(event_id, "sre", "handled")

        pending = bus.get_pending()
        assert len(pending) == 0

    def test_get_recent_returns_all(self, test_db):
        bus = EventBus(test_db)
        bus.emit(EVENT_POST_FAILED, "client-1")
        bus.emit(EVENT_ENGAGEMENT_SPIKE, "client-2")

        recent = bus.get_recent()
        assert len(recent) == 2

    def test_get_recent_filters_by_client(self, test_db):
        bus = EventBus(test_db)
        bus.emit(EVENT_POST_FAILED, "clientA")
        bus.emit(EVENT_ENGAGEMENT_SPIKE, "clientB")
        bus.emit(EVENT_STRATEGY_EXPIRED, "clientA")

        recent_a = bus.get_recent(client_id="clientA")
        assert len(recent_a) == 2
        assert all(r["client_id"] == "clientA" for r in recent_a)

        recent_b = bus.get_recent(client_id="clientB")
        assert len(recent_b) == 1
        assert recent_b[0]["client_id"] == "clientB"


class TestProcessPendingEvents:
    """Integration tests for process_pending_events."""

    def test_processes_known_event_type(self, test_db, test_settings):
        bus = EventBus(test_db)
        bus.emit(EVENT_POST_FAILED, "client-1")

        result = process_pending_events(test_db, test_settings)
        assert result["processed"] == 1
        assert result["errors"] == 0

        # Should be empty after processing
        assert len(bus.get_pending()) == 0

    def test_handles_unknown_event_type(self, test_db, test_settings):
        bus = EventBus(test_db)
        bus.emit("unknown.type", "client-1")

        result = process_pending_events(test_db, test_settings)
        assert result["processed"] == 1
        assert result["errors"] == 0

        # Verify marked with no_handler result
        recent = bus.get_recent()
        assert recent[0]["handler_result"] == "no_handler_for_unknown.type"
        assert recent[0]["handler_agent"] == "none"

    def test_no_pending_returns_zero(self, test_db, test_settings):
        result = process_pending_events(test_db, test_settings)
        assert result == {"processed": 0, "errors": 0}

    def test_handler_error_counts_as_error(self, test_db, test_settings, monkeypatch):
        bus = EventBus(test_db)
        bus.emit(EVENT_STRATEGY_EXPIRED, "client-1")

        def _boom(db, settings, event):
            raise RuntimeError("kaboom")

        # Temporarily replace the handler
        monkeypatch.setitem(_HANDLERS, EVENT_STRATEGY_EXPIRED, ("ceo", _boom))

        result = process_pending_events(test_db, test_settings)
        assert result["errors"] == 1
        assert result["processed"] == 0

        # Event should still be marked processed (with error result)
        assert len(bus.get_pending()) == 0


class TestHandlers:
    """Tests for built-in event handlers."""

    def test_strategy_expired_publishes_insight(self, test_db, test_settings):
        event = {
            "id": "evt-001",
            "event_type": EVENT_STRATEGY_EXPIRED,
            "client_id": "client-1",
            "payload": "{}",
        }

        result = _handle_strategy_expired(test_db, test_settings, event)
        assert result == "insight_published"

        # Verify insight was inserted into shared_insights table
        rows = test_db.fetchall(
            "SELECT * FROM shared_insights WHERE source_agent=? AND insight_type=?",
            ("event_bus", "client_health"),
        )
        assert len(rows) >= 1
        assert "client-1" in rows[0]["content"]

    def test_post_failed_counts_failures(self, test_db, test_settings):
        bus = EventBus(test_db)
        # Emit 3 post.failed events for the same client
        for _ in range(3):
            bus.emit(EVENT_POST_FAILED, "client-1")

        # Build a fake event dict matching what process_pending_events would pass
        event = {
            "id": "evt-count",
            "event_type": EVENT_POST_FAILED,
            "client_id": "client-1",
            "payload": "{}",
        }

        result = _handle_post_failed(test_db, test_settings, event)
        assert "failure_count_24h=3" in result
