"""Auto-graduation — autonomously promote/demote clients from review to auto-publish."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.auto_graduation")

# Graduation thresholds
GRADUATION_VOICE_CONFIDENCE = 0.8
GRADUATION_MIN_REVIEWS = 20
GRADUATION_APPROVAL_RATE = 0.9  # 90% of last 10
GRADUATION_MIN_AGE_DAYS = 14
GRADUATION_RECENT_WINDOW = 10

# Regression thresholds
REGRESSION_APPROVAL_RATE = 0.7  # Below 70% triggers regression
REGRESSION_RECENT_WINDOW = 10


def evaluate_auto_graduation(db: Database, client_id: str) -> dict:
    """Evaluate whether a client should graduate to or regress from auto-publish.

    Returns a dict with:
      - action: "graduate" | "regress" | "none"
      - reason: human-readable explanation
      - details: dict of metrics used in the decision
    """
    client = db.fetchone("SELECT * FROM clients WHERE id=?", (client_id,))
    if not client:
        return {"action": "none", "reason": "client not found", "details": {}}

    auto_publish = client.get("auto_publish", 0)
    voice_confidence = client.get("voice_confidence") or 0.0
    graduation_status = client.get("auto_graduation_status", "manual")

    # Do not touch clients that explicitly locked manual mode
    if graduation_status == "locked_manual":
        return {"action": "none", "reason": "manually locked to review mode", "details": {}}

    # Get total review count
    total_row = db.fetchone(
        "SELECT COUNT(*) as c FROM content_reviews WHERE client_id=?",
        (client_id,),
    )
    total_reviews = total_row["c"] if total_row else 0

    # Recent reviews (last GRADUATION_RECENT_WINDOW)
    recent_reviews = db.fetchall(
        "SELECT action FROM content_reviews "
        "WHERE client_id=? AND action IN ('approved', 'rejected') "
        "ORDER BY reviewed_at DESC LIMIT ?",
        (client_id, GRADUATION_RECENT_WINDOW),
    )
    recent_count = len(recent_reviews)
    recent_approvals = sum(1 for r in recent_reviews if r["action"] == "approved")
    recent_approval_rate = recent_approvals / recent_count if recent_count > 0 else 0.0

    # Client age
    created_at = client.get("created_at")
    client_age_days = 0
    if created_at:
        try:
            from ortobahn.db import to_datetime

            created_dt = to_datetime(created_at)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)  # noqa: UP017
            client_age_days = (datetime.now(timezone.utc) - created_dt).days  # noqa: UP017
        except (ValueError, TypeError):
            pass

    details = {
        "voice_confidence": voice_confidence,
        "total_reviews": total_reviews,
        "recent_approval_rate": round(recent_approval_rate, 3),
        "recent_count": recent_count,
        "client_age_days": client_age_days,
        "current_auto_publish": auto_publish,
        "graduation_status": graduation_status,
    }

    # --- REGRESSION CHECK (for currently auto-publish clients) ---
    if auto_publish:
        if recent_count >= REGRESSION_RECENT_WINDOW and recent_approval_rate < REGRESSION_APPROVAL_RATE:
            reason = (
                f"Approval rate dropped to {recent_approval_rate:.0%} "
                f"(threshold: {REGRESSION_APPROVAL_RATE:.0%}) in last {REGRESSION_RECENT_WINDOW} reviews"
            )
            _apply_regression(db, client_id, details, reason)
            return {"action": "regress", "reason": reason, "details": details}
        return {"action": "none", "reason": "auto-publish client in good standing", "details": details}

    # --- GRADUATION CHECK (for currently review-mode clients) ---
    failures = []
    if voice_confidence < GRADUATION_VOICE_CONFIDENCE:
        failures.append(f"voice_confidence {voice_confidence:.2f} < {GRADUATION_VOICE_CONFIDENCE}")
    if total_reviews < GRADUATION_MIN_REVIEWS:
        failures.append(f"total_reviews {total_reviews} < {GRADUATION_MIN_REVIEWS}")
    if recent_count < GRADUATION_RECENT_WINDOW:
        failures.append(f"recent_reviews {recent_count} < {GRADUATION_RECENT_WINDOW}")
    elif recent_approval_rate < GRADUATION_APPROVAL_RATE:
        failures.append(f"recent_approval_rate {recent_approval_rate:.0%} < {GRADUATION_APPROVAL_RATE:.0%}")
    if client_age_days < GRADUATION_MIN_AGE_DAYS:
        failures.append(f"client_age {client_age_days}d < {GRADUATION_MIN_AGE_DAYS}d")

    if failures:
        reason = f"Not ready: {'; '.join(failures)}"
        return {"action": "none", "reason": reason, "details": details}

    # All checks passed
    reason = (
        f"Voice confidence {voice_confidence:.2f}, "
        f"approval rate {recent_approval_rate:.0%} "
        f"over {total_reviews} reviews, "
        f"client age {client_age_days} days"
    )
    _apply_graduation(db, client_id, details, reason)
    return {"action": "graduate", "reason": reason, "details": details}


def _apply_graduation(db, client_id, details, reason):
    db.execute(
        "UPDATE clients SET auto_publish=1, auto_graduation_status='graduated' WHERE id=?",
        (client_id,),
        commit=True,
    )
    _record_event(db, client_id, "graduation", 0, 1, details, reason)
    logger.info("Client %s GRADUATED to auto-publish: %s", client_id, reason)


def _apply_regression(db, client_id, details, reason):
    db.execute(
        "UPDATE clients SET auto_publish=0, auto_graduation_status='regressed' WHERE id=?",
        (client_id,),
        commit=True,
    )
    _record_event(db, client_id, "regression", 1, 0, details, reason)
    logger.info("Client %s REGRESSED to review mode: %s", client_id, reason)


def _record_event(db, client_id, event_type, prev, new, details, reason):
    db.execute(
        """INSERT INTO graduation_events
           (id, client_id, event_type, previous_auto_publish, new_auto_publish,
            voice_confidence, approval_rate, review_count, adaptive_threshold, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4())[:8],
            client_id,
            event_type,
            prev,
            new,
            details.get("voice_confidence"),
            details.get("recent_approval_rate"),
            details.get("total_reviews"),
            details.get("adaptive_threshold"),
            reason,
        ),
        commit=True,
    )
