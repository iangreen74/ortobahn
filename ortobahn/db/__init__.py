"""Database package — re-exports everything so ``from ortobahn.db import Database`` still works.

The Database class is assembled from focused mixin modules:
- ``core.py``      — connection management, execute(), schema setup, migrations, query profiling, caching, health
- ``posts.py``     — Post CRUD, metrics, approval workflow
- ``clients.py``   — Client CRUD, subscriptions, API keys
- ``pipeline.py``  — Pipeline runs, deployments, agent logs, watchdog helpers
- ``analytics.py`` — Analytics report, strategy CRUD, cost tracking
- ``memory.py``    — Engineering tasks, CI tracking, chat, legal, articles, directives
"""

from __future__ import annotations

from ortobahn.db.analytics import AnalyticsMixin
from ortobahn.db.clients import ClientsMixin
from ortobahn.db.core import (
    _POOL_CHECKOUT_TIMEOUT,
    _SLOW_QUERY_THRESHOLD,
    PoolExhaustedError,
    _HealthCheckedPool,
    _normalize_query,
    to_datetime,
)
from ortobahn.db.core import (
    Database as _CoreDatabase,
)
from ortobahn.db.memory import MemoryMixin
from ortobahn.db.pipeline import PipelineMixin
from ortobahn.db.posts import PostsMixin


class Database(
    PostsMixin,
    ClientsMixin,
    PipelineMixin,
    AnalyticsMixin,
    MemoryMixin,
    _CoreDatabase,
):
    """Full Database class assembled from domain-specific mixins.

    All existing ``from ortobahn.db import Database`` imports continue to work
    and the class exposes every method previously on the monolithic ``Database``.
    """


def create_database(settings) -> Database:
    """Create a Database instance from settings."""
    return Database(
        db_path=settings.db_path if not settings.database_url else None,
        database_url=settings.database_url,
        pool_min=getattr(settings, "db_pool_min", 2),
        pool_max=getattr(settings, "db_pool_max", 10),
    )


__all__ = [
    "Database",
    "PoolExhaustedError",
    "_HealthCheckedPool",
    "_POOL_CHECKOUT_TIMEOUT",
    "_SLOW_QUERY_THRESHOLD",
    "_normalize_query",
    "create_database",
    "to_datetime",
]
