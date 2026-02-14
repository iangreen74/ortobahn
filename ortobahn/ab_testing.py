"""A/B testing framework for content variants."""

from __future__ import annotations

import uuid

from ortobahn.db import Database


def generate_pair_id() -> str:
    """Generate a unique ID to link A/B variants."""
    return str(uuid.uuid4())


def get_ab_results(db: Database, client_id: str | None = None) -> dict:
    """Analyze A/B test results. Returns winning patterns.

    Compares pairs of posts (tagged with ab_pair_id and ab_group A/B)
    by total engagement to determine which variant performs better.
    """
    query = """
        SELECT p.ab_pair_id, p.ab_group, p.text, p.platform,
               COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0) as engagement
        FROM posts p
        LEFT JOIN metrics m ON p.id = m.post_id
        WHERE p.ab_pair_id IS NOT NULL AND p.status = 'published'
    """
    params: list = []
    if client_id:
        query += " AND p.client_id=?"
        params.append(client_id)

    rows = db.conn.execute(query, params).fetchall()

    # Group by pair_id, compare A vs B
    pairs: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        pid = r["ab_pair_id"]
        if pid not in pairs:
            pairs[pid] = {}
        pairs[pid][r["ab_group"]] = r

    a_wins = 0
    b_wins = 0
    ties = 0
    for pair in pairs.values():
        if "A" in pair and "B" in pair:
            if pair["A"]["engagement"] > pair["B"]["engagement"]:
                a_wins += 1
            elif pair["B"]["engagement"] > pair["A"]["engagement"]:
                b_wins += 1
            else:
                ties += 1

    completed_pairs = a_wins + b_wins + ties
    return {
        "total_pairs": len(pairs),
        "completed_pairs": completed_pairs,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
    }
