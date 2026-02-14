"""RSS feed reader for tech/business news."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger("ortobahn.rss")


@dataclass
class RSSArticle:
    title: str
    summary: str
    link: str
    source: str


def fetch_feeds(feed_urls: list[str], max_per_feed: int = 5) -> list[RSSArticle]:
    """Fetch articles from RSS feeds. Gracefully handles failures per feed."""
    articles = []
    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.get("title", url)
            for entry in feed.entries[:max_per_feed]:
                articles.append(
                    RSSArticle(
                        title=entry.get("title", ""),
                        summary=entry.get("summary", "")[:200],
                        link=entry.get("link", ""),
                        source=source,
                    )
                )
        except Exception as e:
            logger.warning(f"RSS feed failed ({url}): {e}")
    logger.info(f"Fetched {len(articles)} articles from {len(feed_urls)} RSS feeds")
    return articles
