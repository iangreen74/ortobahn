"""NewsAPI client for trending headlines."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("ortobahn.newsapi")


@dataclass
class Article:
    title: str
    description: str
    source: str
    url: str


def get_trending_headlines(
    api_key: str, category: str = "technology", country: str = "us", page_size: int = 10
) -> list[Article]:
    """Fetch trending headlines from NewsAPI. Returns empty list on failure."""
    if not api_key:
        logger.info("No NewsAPI key configured, skipping")
        return []

    try:
        from newsapi import NewsApiClient

        client = NewsApiClient(api_key=api_key)
        response = client.get_top_headlines(category=category, country=country, page_size=page_size)
        articles = []
        for a in response.get("articles", []):
            articles.append(
                Article(
                    title=a.get("title", ""),
                    description=a.get("description") or "",
                    source=a.get("source", {}).get("name", ""),
                    url=a.get("url", ""),
                )
            )
        logger.info(f"Fetched {len(articles)} headlines from NewsAPI")
        return articles
    except Exception as e:
        logger.warning(f"NewsAPI failed: {e}")
        return []
