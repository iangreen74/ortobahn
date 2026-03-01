"""Adaptive confidence threshold — self-tuning per-client publish gate."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.adaptive_threshold")

MIN_THRESHOLD = 0.5
MAX_THRESHOLD = 0.9
DEFAULT_THRESHOLD = 0.7
MIN_RECORDS = 10
LOOKBACK_DAYS = 30
ADJUSTMENT_RATE = 0.5


def compute_adaptive_threshold(
    db: Database,
    client_id: str,
    default: float = DEFAULT_THRESHOLD,
) -> float:
    """Compute adaptive confidence threshold for a client.

    Uses signed bias (predicted - actual percentile) from calibration records:
    - Positive bias (overconfident): raise threshold to be more selective
    - Negative bias (underconfident): lower threshold to publish more
    - Insufficient data: return default (0.7)

    Returns a float clamped to [0.5, 0.9].
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()  # noqa: UP017

    rows = db.fetchall(
        """SELECT predicted_confidence, engagement_percentile
           FROM confidence_calibration
           WHERE client_id = ? AND measured_at >= ?
           ORDER BY measured_at DESC
           LIMIT 100""",
        (client_id, cutoff),
    )

    if len(rows) < MIN_RECORDS:
        return default

    biases = []
    for r in rows:
        predicted = r["predicted_confidence"]
        actual = r["engagement_percentile"]
        if predicted is not None and actual is not None:
            biases.append(predicted - actual)

    if not biases:
        return default

    avg_bias = sum(biases) / len(biases)
    adjusted = default + (avg_bias * ADJUSTMENT_RATE)
    return float(max(MIN_THRESHOLD, min(MAX_THRESHOLD, round(adjusted, 3))))
