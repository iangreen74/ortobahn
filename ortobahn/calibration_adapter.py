"""Calibration Adapter - bridges calibration data to creator prompt context.

Reads confidence_calibration records and generates specific instructions
for the Creator agent to adjust its confidence scoring.
Zero LLM calls - pure computation.
"""

from __future__ import annotations

import logging

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.calibration_adapter")


def get_calibration_context(db: Database, client_id: str = "default", sample_limit: int = 50) -> str:
    """Generate calibration adjustment instructions for the Creator agent."""
    rows = db.fetchall(
        """SELECT predicted_confidence, engagement_percentile, calibration_error
           FROM confidence_calibration
           WHERE client_id = ?
           ORDER BY measured_at DESC
           LIMIT ?""",
        (client_id, sample_limit),
    )

    if len(rows) < 5:
        return ""

    errors = [r["calibration_error"] for r in rows if r.get("calibration_error") is not None]
    if not errors:
        return ""

    mean_error = sum(errors) / len(errors)
    mae = sum(abs(e) for e in errors) / len(errors)

    if mean_error > 0.15:
        bias = "significantly overconfident"
        adjustment = f"Reduce your confidence scores by approximately {abs(mean_error):.0%}"
    elif mean_error > 0.05:
        bias = "slightly overconfident"
        adjustment = f"Consider lowering confidence scores by {abs(mean_error):.0%}"
    elif mean_error < -0.15:
        bias = "significantly underconfident"
        adjustment = f"Increase your confidence scores by approximately {abs(mean_error):.0%}"
    elif mean_error < -0.05:
        bias = "slightly underconfident"
        adjustment = f"Consider raising confidence scores by {abs(mean_error):.0%}"
    else:
        bias = "well-calibrated"
        adjustment = "Your confidence scoring is accurate, maintain current approach"

    lines = [
        "## Confidence Calibration Feedback",
        f"Based on {len(errors)} recent posts, your confidence scoring is {bias}.",
        f"Mean absolute error: {mae:.3f}",
        f"Adjustment: {adjustment}",
    ]

    worst = sorted(rows, key=lambda r: abs(r.get("calibration_error") or 0), reverse=True)[:3]
    if worst:
        lines.append("Largest miscalibrations:")
        for w in worst:
            pred = w.get("predicted_confidence", 0)
            actual = w.get("engagement_percentile", 0)
            if pred is not None and actual is not None:
                lines.append(f"  - Predicted {pred:.2f}, actual performance percentile {actual:.2f}")

    return "\n".join(lines)
