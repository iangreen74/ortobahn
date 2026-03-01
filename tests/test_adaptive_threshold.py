"""Tests for adaptive confidence threshold computation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.adaptive_threshold import compute_adaptive_threshold


def _insert_calibration(db, client_id, predicted, actual, measured_at=None):
    """Helper to insert a calibration record."""
    if measured_at is None:
        measured_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    record_id = str(uuid.uuid4())[:8]
    post_id = str(uuid.uuid4())[:8]
    run_id = str(uuid.uuid4())[:8]
    calibration_error = abs(predicted - actual)
    db.execute(
        """INSERT INTO confidence_calibration
           (id, post_id, client_id, predicted_confidence, engagement_percentile,
            calibration_error, measured_at, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (record_id, post_id, client_id, predicted, actual, calibration_error, measured_at, run_id),
        commit=True,
    )


def test_insufficient_data_returns_default(test_db):
    """Less than 10 records returns the default threshold of 0.7."""
    client_id = "default"
    for _ in range(9):
        _insert_calibration(test_db, client_id, 0.8, 0.6)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result == 0.7


def test_overconfident_raises_threshold(test_db):
    """When predicted >> actual, threshold should be raised above 0.7."""
    client_id = "default"
    for _ in range(15):
        _insert_calibration(test_db, client_id, 0.9, 0.5)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result > 0.7


def test_underconfident_lowers_threshold(test_db):
    """When predicted << actual, threshold should be lowered below 0.7."""
    client_id = "default"
    for _ in range(15):
        _insert_calibration(test_db, client_id, 0.4, 0.8)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result < 0.7


def test_well_calibrated_stays_near_default(test_db):
    """When predicted ≈ actual, threshold stays near 0.7."""
    client_id = "default"
    for _ in range(15):
        _insert_calibration(test_db, client_id, 0.7, 0.7)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result == 0.7


def test_clamped_to_min(test_db):
    """Extreme underconfidence clamps threshold to minimum 0.5."""
    client_id = "default"
    for _ in range(15):
        _insert_calibration(test_db, client_id, 0.0, 1.0)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result == 0.5


def test_clamped_to_max(test_db):
    """Extreme overconfidence clamps threshold to maximum 0.9."""
    client_id = "default"
    for _ in range(15):
        _insert_calibration(test_db, client_id, 1.0, 0.0)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result == 0.9


def test_old_data_excluded(test_db):
    """Records older than 30 days are excluded, returning default."""
    client_id = "default"
    old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()  # noqa: UP017
    for _ in range(15):
        _insert_calibration(test_db, client_id, 0.9, 0.5, measured_at=old_date)

    result = compute_adaptive_threshold(test_db, client_id)
    assert result == 0.7


def test_per_client_isolation(test_db):
    """Client B data does not affect client A threshold."""
    client_a = "default"
    client_b = str(uuid.uuid4())[:8]

    # Insert a client B row into clients table so FK won't fail
    test_db.execute(
        """INSERT OR IGNORE INTO clients (id, name, industry, target_audience)
           VALUES (?, ?, ?, ?)""",
        (client_b, "Test B", "tech", "developers"),
        commit=True,
    )

    # Client B has strongly overconfident data
    for _ in range(15):
        _insert_calibration(test_db, client_b, 1.0, 0.0)

    # Client A has no data
    result = compute_adaptive_threshold(test_db, client_a)
    assert result == 0.7
