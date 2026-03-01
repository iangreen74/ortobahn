"""Structured content feature extraction — zero LLM calls.

Analyzes recent published posts to extract quantitative features
and correlates them with performance tiers. Produces a brief for the Creator agent.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.content_features")

LENGTH_BUCKETS = {
    "short": (0, 100),
    "medium": (101, 250),
    "long": (251, float("inf")),
}

TIME_OF_DAY = {
    "morning": range(6, 12),
    "afternoon": range(12, 17),
    "evening": range(17, 21),
}
# "night" covers 21-23 and 0-5

EMOJI_PATTERN = re.compile(
    r"[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    r"\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0000fe0f]"
)
HASHTAG_PATTERN = re.compile(r"#\w+")
URL_PATTERN = re.compile(r"https?://\S+")
CTA_PATTERNS = re.compile(
    r"\b(check out|sign up|learn more|click|subscribe|join|try|get started|"
    r"read more|follow|share|reply|comment|let me know|what do you think)\b",
    re.IGNORECASE,
)

MIN_POSTS = 10


def extract_features(text: str, published_at: str | None = None, platform: str = "generic") -> dict:
    """Extract quantitative features from a single post."""
    features: dict = {}

    length = len(text)
    for bucket_name, (lo, hi) in LENGTH_BUCKETS.items():
        if lo <= length <= hi:
            features["length_bucket"] = bucket_name
            break

    features["has_question"] = "?" in text
    features["has_cta"] = bool(CTA_PATTERNS.search(text))
    features["has_emoji"] = bool(EMOJI_PATTERN.search(text))
    features["has_hashtag"] = bool(HASHTAG_PATTERN.search(text))
    features["has_url"] = bool(URL_PATTERN.search(text))
    features["platform"] = platform

    if published_at:
        try:
            from ortobahn.db import to_datetime

            dt = to_datetime(published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
            features["day_of_week"] = dt.strftime("%A")
            hour = dt.hour
            for period, hours in TIME_OF_DAY.items():
                if hour in hours:
                    features["time_of_day"] = period
                    break
            else:
                features["time_of_day"] = "night"
        except (ValueError, TypeError):
            pass

    return features


def build_content_brief(db: Database, client_id: str = "default", lookback_days: int = 30) -> str:
    """Analyze published posts and produce a structured content brief.

    Returns a formatted string for injection into the Creator prompt.
    Returns empty string if insufficient data (< 10 published posts with metrics).
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)  # noqa: UP017
    ).isoformat()

    rows = db.fetchall(
        """SELECT p.id, p.text, p.platform, p.published_at,
                  COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0)
                  + COALESCE(m.reply_count, 0) AS engagement
           FROM posts p
           LEFT JOIN metrics m ON p.id = m.post_id
               AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id
                           ORDER BY m2.measured_at DESC LIMIT 1)
           WHERE p.status = 'published' AND p.client_id = ?
               AND p.published_at >= ?
           ORDER BY p.published_at DESC
           LIMIT 200""",
        (client_id, cutoff),
    )

    if len(rows) < MIN_POSTS:
        return ""

    # Sort by engagement to compute percentiles
    engagements = sorted(r["engagement"] for r in rows)
    p25_idx = max(0, len(engagements) // 4 - 1)
    p75_idx = min(len(engagements) - 1, len(engagements) * 3 // 4)
    p25 = engagements[p25_idx]
    p75 = engagements[p75_idx]

    # Extract features and classify by tier
    tier_features: dict[str, list[dict]] = {
        "top_25": [],
        "middle_50": [],
        "bottom_25": [],
    }

    for row in rows:
        eng = row["engagement"]
        if eng >= p75 and p75 > p25:  # Avoid degenerate case where all engagement is 0
            tier = "top_25"
        elif eng <= p25:
            tier = "bottom_25"
        else:
            tier = "middle_50"

        features = extract_features(
            row["text"] or "",
            row["published_at"],
            row["platform"] or "generic",
        )
        tier_features[tier].append(features)

    # Compute boolean feature prevalence per tier
    bool_features = [
        "has_question",
        "has_cta",
        "has_emoji",
        "has_hashtag",
        "has_url",
    ]
    feature_labels = {
        "has_question": "Include questions",
        "has_cta": "Include call-to-action",
        "has_emoji": "Use emoji",
        "has_hashtag": "Use hashtags",
        "has_url": "Include links",
    }

    def _pct(tier_name: str, feature: str) -> float:
        items = tier_features[tier_name]
        if not items:
            return 0.0
        return sum(1 for f in items if f.get(feature)) / len(items) * 100

    # Compute length bucket distribution per tier
    def _top_bucket(tier_name: str) -> str:
        items = tier_features[tier_name]
        if not items:
            return "medium"
        counts: dict[str, int] = defaultdict(int)
        for f in items:
            counts[f.get("length_bucket", "medium")] += 1
        return max(counts, key=counts.get) if counts else "medium"

    # Build the brief
    parts = [f"## Content Performance Brief (auto-generated, last {lookback_days} days)"]
    parts.append(f"Analyzed {len(rows)} published posts.\n")

    # Winning patterns
    winning = []
    for feat in bool_features:
        top_pct = _pct("top_25", feat)
        bot_pct = _pct("bottom_25", feat)
        if top_pct - bot_pct >= 20:
            winning.append(f"- {feature_labels[feat]} ({top_pct:.0f}% of top posts vs {bot_pct:.0f}% of bottom)")

    top_bucket = _top_bucket("top_25")
    bot_bucket = _top_bucket("bottom_25")
    if top_bucket != bot_bucket:
        winning.append(f"- Use {top_bucket}-length posts (most common in top performers)")

    if winning:
        parts.append("### What works (top-performing posts tend to):")
        parts.extend(winning)

    # Losing patterns
    losing = []
    for feat in bool_features:
        bot_pct = _pct("bottom_25", feat)
        top_pct = _pct("top_25", feat)
        if bot_pct - top_pct >= 20:
            losing.append(f"- {feature_labels[feat]} ({bot_pct:.0f}% of bottom posts vs {top_pct:.0f}% of top)")

    if losing:
        parts.append("\n### What to avoid (bottom-performing posts tend to):")
        parts.extend(losing)

    # Timing insights
    def _top_time(tier_name: str) -> str | None:
        items = tier_features[tier_name]
        if not items:
            return None
        counts: dict[str, int] = defaultdict(int)
        for f in items:
            if "time_of_day" in f:
                counts[f["time_of_day"]] += 1
        return max(counts, key=counts.get) if counts else None

    best_time = _top_time("top_25")
    if best_time:
        parts.append(f"\n### Timing: Best results posting in the {best_time}")

    parts.append("\nUse these data-driven patterns to inform your content creation.")

    return "\n".join(parts)
