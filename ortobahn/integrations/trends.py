"""Google Trends wrapper (best-effort, may be rate limited)."""

from __future__ import annotations

import logging

logger = logging.getLogger("ortobahn.trends")


def get_trending_searches(country: str = "united_states") -> list[str]:
    """Get currently trending searches from Google Trends. Returns empty list on failure."""
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US")
        df = pytrends.trending_searches(pn=country)
        results: list[str] = df[0].tolist()[:10]
        logger.info(f"Fetched {len(results)} trending searches from Google Trends")
        return results
    except Exception as e:
        logger.warning(f"Google Trends failed (this is expected sometimes): {e}")
        return []
