"""Tests for ortobahn.auto_graduation — graduation and regression logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.auto_graduation import evaluate_auto_graduation


def _insert_reviews(db, client_id: str, approved: int, rejected: int) -> None:
    """Insert N approved and M rejected content reviews for a client."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    for i in range(approved):
        db.execute(
            "INSERT INTO content_reviews (id, client_id, content_type, content_id, action, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4())[:8],
                client_id,
                "post",
                f"post-a-{i}",
                "approved",
                (now - timedelta(seconds=i)).isoformat(),
            ),
            commit=True,
        )
    for i in range(rejected):
        db.execute(
            "INSERT INTO content_reviews (id, client_id, content_type, content_id, action, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4())[:8],
                client_id,
                "post",
                f"post-r-{i}",
                "rejected",
                (now - timedelta(seconds=approved + i)).isoformat(),
            ),
            commit=True,
        )


def _set_client_for_graduation(db, client_id: str, **overrides) -> None:
    """Set the default client to a state ready for graduation evaluation.

    Defaults: voice_confidence=0.85, auto_publish=0, created 30 days ago, status='manual'.
    """
    defaults = {
        "voice_confidence": 0.85,
        "auto_publish": 0,
        "auto_graduation_status": "manual",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),  # noqa: UP017
    }
    defaults.update(overrides)
    db.execute(
        "UPDATE clients SET voice_confidence=?, auto_publish=?, auto_graduation_status=?, created_at=? WHERE id=?",
        (
            defaults["voice_confidence"],
            defaults["auto_publish"],
            defaults["auto_graduation_status"],
            defaults["created_at"],
            client_id,
        ),
        commit=True,
    )


def test_insufficient_reviews_no_graduation(test_db):
    """Only 5 reviews — not enough for graduation."""
    _set_client_for_graduation(test_db, "default")
    _insert_reviews(test_db, "default", approved=5, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "total_reviews" in result["reason"]
    assert result["details"]["total_reviews"] == 5


def test_low_voice_confidence_no_graduation(test_db):
    """20 reviews but voice_confidence is too low."""
    _set_client_for_graduation(test_db, "default", voice_confidence=0.3)
    _insert_reviews(test_db, "default", approved=20, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "voice_confidence" in result["reason"]


def test_client_too_young_no_graduation(test_db):
    """All criteria met except the client was created today."""
    _set_client_for_graduation(
        test_db,
        "default",
        created_at=datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    )
    _insert_reviews(test_db, "default", approved=20, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "client_age" in result["reason"]


def test_graduation_succeeds(test_db):
    """All criteria met — client should graduate."""
    _set_client_for_graduation(test_db, "default")
    _insert_reviews(test_db, "default", approved=20, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "graduate"
    assert result["details"]["voice_confidence"] == 0.85
    assert result["details"]["recent_approval_rate"] == 1.0

    # Verify the client was updated
    client = test_db.fetchone("SELECT auto_publish, auto_graduation_status FROM clients WHERE id=?", ("default",))
    assert client["auto_publish"] == 1
    assert client["auto_graduation_status"] == "graduated"


def test_locked_manual_prevents_graduation(test_db):
    """A client with locked_manual status cannot graduate."""
    _set_client_for_graduation(test_db, "default", auto_graduation_status="locked_manual")
    _insert_reviews(test_db, "default", approved=20, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "locked" in result["reason"]


def test_already_auto_publish_no_action(test_db):
    """Client already on auto_publish with good stats — no action needed."""
    _set_client_for_graduation(test_db, "default", auto_publish=1)
    _insert_reviews(test_db, "default", approved=10, rejected=0)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "good standing" in result["reason"]


def test_regression_on_low_approval_rate(test_db):
    """Auto-publish client with 3 approved + 7 rejected should regress."""
    _set_client_for_graduation(test_db, "default", auto_publish=1)
    _insert_reviews(test_db, "default", approved=3, rejected=7)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "regress"
    assert "30%" in result["reason"] or "Approval rate" in result["reason"]

    # Verify the client was updated
    client = test_db.fetchone("SELECT auto_publish, auto_graduation_status FROM clients WHERE id=?", ("default",))
    assert client["auto_publish"] == 0
    assert client["auto_graduation_status"] == "regressed"


def test_no_regression_above_threshold(test_db):
    """Auto-publish client with 8 approved + 2 rejected stays in good standing."""
    _set_client_for_graduation(test_db, "default", auto_publish=1)
    _insert_reviews(test_db, "default", approved=8, rejected=2)

    result = evaluate_auto_graduation(test_db, "default")

    assert result["action"] == "none"
    assert "good standing" in result["reason"]


def test_regression_records_event(test_db):
    """After regression, a graduation_events row is recorded."""
    _set_client_for_graduation(test_db, "default", auto_publish=1)
    _insert_reviews(test_db, "default", approved=2, rejected=8)

    evaluate_auto_graduation(test_db, "default")

    event = test_db.fetchone(
        "SELECT * FROM graduation_events WHERE client_id=? AND event_type=?",
        ("default", "regression"),
    )
    assert event is not None
    assert event["previous_auto_publish"] == 1
    assert event["new_auto_publish"] == 0
    assert event["client_id"] == "default"
    assert "Approval rate" in event["reason"]
