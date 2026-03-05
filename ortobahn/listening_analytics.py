"""Listening Analytics — daily aggregation of listening and engagement data."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.listening_analytics")


def aggregate_daily(db: Database, client_id: str, date_str: str | None = None) -> int:
    """Aggregate listening/engagement data for a single day. Returns rows created."""
    if not date_str:
        date_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    day_start = f"{date_str}T00:00:00"
    day_end = f"{date_str}T23:59:59"

    # Get platforms with activity
    platforms = db.fetchall(
        """SELECT DISTINCT platform FROM discovered_conversations
           WHERE client_id=? AND discovered_at BETWEEN ? AND ?""",
        (client_id, day_start, day_end),
    )

    created = 0
    for row in platforms:
        platform = row["platform"]

        # Check if already aggregated
        existing = db.fetchone(
            "SELECT id FROM listening_analytics WHERE client_id=? AND date=? AND platform=?",
            (client_id, date_str, platform),
        )
        if existing:
            continue

        # Count discovered
        discovered_row = db.fetchone(
            """SELECT COUNT(*) as cnt FROM discovered_conversations
               WHERE client_id=? AND platform=? AND discovered_at BETWEEN ? AND ?""",
            (client_id, platform, day_start, day_end),
        )
        discovered = discovered_row["cnt"] if discovered_row else 0

        # Count replied
        replied_row = db.fetchone(
            """SELECT COUNT(*) as cnt FROM discovered_conversations
               WHERE client_id=? AND platform=? AND status='replied'
               AND discovered_at BETWEEN ? AND ?""",
            (client_id, platform, day_start, day_end),
        )
        replied = replied_row["cnt"] if replied_row else 0

        # Average relevance score
        rel_row = db.fetchone(
            """SELECT AVG(relevance_score) as avg_rel FROM discovered_conversations
               WHERE client_id=? AND platform=? AND discovered_at BETWEEN ? AND ?""",
            (client_id, platform, day_start, day_end),
        )
        avg_relevance = round(rel_row["avg_rel"], 3) if rel_row and rel_row["avg_rel"] else 0.0

        # Top keywords from listening rules that generated discoveries
        keywords_row = db.fetchall(
            """SELECT source_query, COUNT(*) as cnt FROM discovered_conversations
               WHERE client_id=? AND platform=? AND discovered_at BETWEEN ? AND ?
               GROUP BY source_query ORDER BY cnt DESC LIMIT 5""",
            (client_id, platform, day_start, day_end),
        )
        top_keywords = [r["source_query"] for r in keywords_row]

        db.execute(
            """INSERT INTO listening_analytics
               (id, client_id, date, platform, conversations_discovered,
                conversations_replied, avg_relevance_score, top_keywords)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                client_id,
                date_str,
                platform,
                discovered,
                replied,
                avg_relevance,
                json.dumps(top_keywords),
            ),
            commit=True,
        )
        created += 1

    return created


def get_listening_summary(db: Database, client_id: str, days: int = 7) -> dict:
    """Get listening analytics summary for a client."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetchall(
        "SELECT * FROM listening_analytics WHERE client_id=? AND date >= ? ORDER BY date DESC",
        (client_id, cutoff),
    )
    if not rows:
        return {
            "total_discovered": 0,
            "total_replied": 0,
            "avg_relevance": 0.0,
            "reply_rate": 0.0,
            "platforms": {},
            "daily": [],
        }

    total_discovered = sum(r["conversations_discovered"] for r in rows)
    total_replied = sum(r["conversations_replied"] for r in rows)

    # Per-platform breakdown
    platforms: dict[str, dict] = {}
    for r in rows:
        p = r["platform"]
        if p not in platforms:
            platforms[p] = {"discovered": 0, "replied": 0}
        platforms[p]["discovered"] += r["conversations_discovered"]
        platforms[p]["replied"] += r["conversations_replied"]

    # Daily trend (most recent first)
    daily: list[dict] = []
    seen_dates: set[str] = set()
    for r in rows:
        d = r["date"]
        if d not in seen_dates:
            day_rows = [x for x in rows if x["date"] == d]
            daily.append(
                {
                    "date": d,
                    "discovered": sum(x["conversations_discovered"] for x in day_rows),
                    "replied": sum(x["conversations_replied"] for x in day_rows),
                }
            )
            seen_dates.add(d)

    avg_rel_rows = [r["avg_relevance_score"] for r in rows if r["avg_relevance_score"]]
    avg_relevance = round(sum(avg_rel_rows) / len(avg_rel_rows), 3) if avg_rel_rows else 0.0

    return {
        "total_discovered": total_discovered,
        "total_replied": total_replied,
        "avg_relevance": avg_relevance,
        "reply_rate": round(total_replied / total_discovered, 3) if total_discovered else 0.0,
        "platforms": platforms,
        "daily": daily[:7],
    }
