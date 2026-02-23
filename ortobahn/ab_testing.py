"""A/B testing framework for content variants."""

from __future__ import annotations

import uuid
from typing import Any

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

    rows = db.fetchall(query, params)

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


def _extract_temporal_bucket(published_at: str | None) -> str | None:
    """Parse ISO timestamp into 'dayOfWeek_hourBucket' key.

    Hour buckets: 0-5, 6-11, 12-17, 18-23.
    Day of week: 0=Mon, 6=Sun.
    Returns e.g. "2_12" for Wednesday afternoon, or None if unparseable.
    """
    if not published_at:
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        hour = dt.hour
        if hour < 6:
            bucket = "0"
        elif hour < 12:
            bucket = "6"
        elif hour < 18:
            bucket = "12"
        else:
            bucket = "18"
        return f"{dt.weekday()}_{bucket}"
    except (ValueError, TypeError):
        return None


def get_ab_results_causal(db: Database, client_id: str | None = None) -> dict:
    """Causal A/B analysis that controls for time-of-day and day-of-week effects.

    Groups pairs by temporal bucket and checks if a variant wins consistently
    across different time contexts (not just in aggregate).

    Returns dict with standard fields plus:
    - causal_winner: "A", "B", or None (if confounded or insufficient data)
    - confounded: True if aggregate winner doesn't hold across time buckets, or None if insufficient data
    - temporal_breakdown: list of {bucket, a_wins, b_wins} dicts
    """
    # Fetch pairs with published_at timestamps
    query = """
        SELECT p.ab_pair_id, p.ab_group, p.published_at,
               COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0) as engagement
        FROM posts p
        LEFT JOIN metrics m ON p.id = m.post_id
        WHERE p.ab_pair_id IS NOT NULL AND p.status = 'published'
    """
    params: list = []
    if client_id:
        query += " AND p.client_id=?"
        params.append(client_id)

    rows = db.fetchall(query, params)

    # Group by pair_id
    pairs: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        pid = r["ab_pair_id"]
        if pid not in pairs:
            pairs[pid] = {}
        pairs[pid][r["ab_group"]] = r

    # Standard aggregate results
    a_wins = 0
    b_wins = 0
    ties = 0
    completed_pairs = []

    for pair_id, pair in pairs.items():
        if "A" in pair and "B" in pair:
            a_eng = pair["A"]["engagement"]
            b_eng = pair["B"]["engagement"]
            # Use published_at from variant A for temporal bucketing
            bucket = _extract_temporal_bucket(pair["A"].get("published_at"))

            if a_eng > b_eng:
                a_wins += 1
                completed_pairs.append({"pair_id": pair_id, "winner": "A", "bucket": bucket})
            elif b_eng > a_eng:
                b_wins += 1
                completed_pairs.append({"pair_id": pair_id, "winner": "B", "bucket": bucket})
            else:
                ties += 1
                completed_pairs.append({"pair_id": pair_id, "winner": "tie", "bucket": bucket})

    total_completed = a_wins + b_wins + ties
    base_result: dict[str, Any] = {
        "total_pairs": len(pairs),
        "completed_pairs": total_completed,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
    }

    # Need at least 3 completed pairs for causal analysis
    if total_completed < 3:
        base_result["causal_winner"] = None
        base_result["confounded"] = None
        base_result["temporal_breakdown"] = []
        return base_result

    # Group wins by temporal bucket
    bucket_results: dict[str, dict] = {}
    for cp in completed_pairs:
        b = cp["bucket"] or "unknown"
        if b not in bucket_results:
            bucket_results[b] = {"bucket": b, "a_wins": 0, "b_wins": 0}
        if cp["winner"] == "A":
            bucket_results[b]["a_wins"] += 1
        elif cp["winner"] == "B":
            bucket_results[b]["b_wins"] += 1

    temporal_breakdown = list(bucket_results.values())

    # Count how many buckets each variant wins
    buckets_with_data = [b for b in temporal_breakdown if b["a_wins"] + b["b_wins"] > 0]
    if not buckets_with_data:
        base_result["causal_winner"] = None
        base_result["confounded"] = None
        base_result["temporal_breakdown"] = temporal_breakdown
        return base_result

    a_bucket_wins = sum(1 for b in buckets_with_data if b["a_wins"] > b["b_wins"])
    b_bucket_wins = sum(1 for b in buckets_with_data if b["b_wins"] > b["a_wins"])
    total_buckets = len(buckets_with_data)

    # A variant wins causally if it wins in >60% of temporal buckets
    causal_winner = None
    if total_buckets > 0:
        if a_bucket_wins / total_buckets > 0.6:
            causal_winner = "A"
        elif b_bucket_wins / total_buckets > 0.6:
            causal_winner = "B"

    # Detect confounding: aggregate winner doesn't match causal winner
    aggregate_winner = "A" if a_wins > b_wins else ("B" if b_wins > a_wins else None)
    confounded = aggregate_winner is not None and causal_winner is not None and aggregate_winner != causal_winner
    # Also confounded if aggregate has a winner but causal doesn't
    if aggregate_winner is not None and causal_winner is None and total_buckets >= 2:
        confounded = True

    base_result["causal_winner"] = causal_winner
    base_result["confounded"] = confounded
    base_result["temporal_breakdown"] = temporal_breakdown
    return base_result
