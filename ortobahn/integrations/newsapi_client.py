"""NewsAPI client for trending headlines."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.newsapi")

_breaker = get_breaker("newsapi", failure_threshold=5, reset_timeout_seconds=120)


@dataclass
class Article:
    title: str
    description: str
    source: str
    url: str


def _check_breaker() -> None:
    """Raise CircuitOpenError if the breaker is OPEN."""
    state = _breaker.state
    if state == CircuitState.OPEN:
        raise CircuitOpenError(
            _breaker.name,
            _breaker._last_failure_time + _breaker.reset_timeout,
        )


def get_trending_headlines(
    api_key: str, category: str = "technology", country: str = "us", page_size: int = 10
) -> list[Article]:
    """Fetch trending headlines from NewsAPI. Returns empty list on failure."""
    if not api_key:
        logger.info("No NewsAPI key configured, skipping")
        return []

    _check_breaker()
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
        _breaker.record_success()
        return articles
    except CircuitOpenError:
        raise
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"NewsAPI failed: {e}")
        return []


def search_news(api_key: str, query: str, page_size: int = 5, sort_by: str = "relevancy") -> list[Article]:
    """Search for articles matching keywords via the 'everything' endpoint."""
    if not api_key or not query:
        return []

    _check_breaker()
    try:
        from newsapi import NewsApiClient

        client = NewsApiClient(api_key=api_key)
        response = client.get_everything(q=query, language="en", sort_by=sort_by, page_size=page_size)
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
        logger.info(f"Searched {len(articles)} articles for '{query}'")
        _breaker.record_success()
        return articles
    except CircuitOpenError:
        raise
    except Exception as e:
        _breaker.record_failure()
        logger.warning(f"NewsAPI search failed: {e}")
        return []
