"""Database core — connection management, query execution, schema setup, and migrations."""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ortobahn.db")

# Default timeout (seconds) when waiting for a connection from the pool.
_POOL_CHECKOUT_TIMEOUT: float = 5.0

# Threshold (seconds) for logging slow queries.
_SLOW_QUERY_THRESHOLD: float = 0.1


class PoolExhaustedError(Exception):
    """Raised when all connections in the pool are in use and the timeout expires."""


def to_datetime(value: Any) -> datetime:
    """Safely convert a value to datetime. Handles str, datetime, date, and None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Cannot convert {type(value).__name__} to datetime: {value!r}")


class _HealthCheckedPool:
    """Wrapper around psycopg2 ThreadedConnectionPool that adds:

    * **Test-on-borrow**: every connection returned from ``getconn`` is verified
      with a lightweight ``SELECT 1`` ping.  Dead connections are discarded and
      a fresh one is transparently acquired.
    * **Wait-with-timeout**: when the underlying pool raises ``PoolError``
      (pool exhausted), we wait on a ``threading.Condition`` up to
      *checkout_timeout* seconds.  If no connection is released in time a clear
      ``PoolExhaustedError`` is raised instead of the cryptic psycopg2 error.
    * **Bookkeeping**: ``checked_out`` tracks how many connections are currently
      lent out, which is useful for tests and monitoring.
    """

    def __init__(
        self,
        minconn: int,
        maxconn: int,
        dsn: str,
        checkout_timeout: float = _POOL_CHECKOUT_TIMEOUT,
    ):
        import psycopg2.pool

        self._inner = psycopg2.pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            dsn=dsn,
        )
        self._maxconn = maxconn
        self._checkout_timeout = checkout_timeout
        self._cond = threading.Condition(threading.Lock())
        self.checked_out = 0

    # -- public API --------------------------------------------------------

    def getconn(self) -> Any:
        """Borrow a connection from the pool, with health-check and timeout."""
        import psycopg2

        while True:
            conn = None
            with self._cond:
                while conn is None:
                    try:
                        conn = self._inner.getconn()
                    except psycopg2.pool.PoolError:
                        # Pool exhausted -- wait for a putconn notification.
                        got_signal = self._cond.wait(timeout=self._checkout_timeout)
                        if not got_signal:
                            raise PoolExhaustedError(
                                f"Could not obtain a database connection within "
                                f"{self._checkout_timeout}s (pool max={self._maxconn})"
                            ) from None
                        continue  # retry after being woken up

            # Health check (test on borrow) -- outside the lock.
            if not self._ping(conn):
                logger.warning("Stale connection detected; discarding and getting a new one")
                self._discard(conn)
                continue  # loop back to get a fresh connection

            with self._cond:
                self.checked_out += 1

            return conn

    def putconn(self, conn: Any, close: bool = False) -> None:
        """Return a connection to the pool and notify waiters."""
        with self._cond:
            self._inner.putconn(conn, close=close)
            self.checked_out = max(0, self.checked_out - 1)
            self._cond.notify()

    def closeall(self) -> None:
        self._inner.closeall()

    @property
    def closed(self) -> bool:
        return self._inner.closed

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _ping(conn: Any) -> bool:
        """Return True if the connection is alive."""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            # If the connection was left in an error state, reset it.
            if conn.info.transaction_status != 0:  # IDLE
                conn.rollback()
            return True
        except Exception:
            return False

    def _discard(self, conn: Any) -> None:
        """Close a bad connection and remove it from the pool."""
        try:
            self._inner.putconn(conn, close=True)
        except Exception:
            # If putconn also fails, just close the raw connection.
            with contextlib.suppress(Exception):
                conn.close()


def _normalize_query(query: str) -> str:
    """Collapse a SQL query into a pattern for stats grouping.

    Replaces literal values (strings, numbers) and parameter placeholders
    with ``?`` so that the same logical query always maps to one key.
    """
    # Strip leading/trailing whitespace and collapse internal whitespace
    q = " ".join(query.split())
    # Replace quoted strings
    q = re.sub(r"'[^']*'", "?", q)
    # Replace numeric literals that stand alone
    q = re.sub(r"\b\d+\b", "?", q)
    # Replace %s placeholders (PostgreSQL)
    q = q.replace("%s", "?")
    # Truncate to first 120 chars for readability
    if len(q) > 120:
        q = q[:120] + "..."
    return q


class _CacheEntry:
    """Internal cache entry with TTL support."""

    __slots__ = ("value", "stored_at")

    def __init__(self, value: Any):
        self.value = value
        self.stored_at: float = time.monotonic()

    def is_expired(self, ttl_seconds: float) -> bool:
        return (time.monotonic() - self.stored_at) > ttl_seconds


class Database:
    def __init__(
        self,
        db_path: Path | None = None,
        database_url: str = "",
        pool_min: int = 2,
        pool_max: int = 10,
    ):
        if database_url:
            self.backend = "postgresql"
            self._pool: _HealthCheckedPool | None = _HealthCheckedPool(
                minconn=pool_min,
                maxconn=pool_max,
                dsn=database_url,
            )
            self._sqlite_conn = None
        else:
            self.backend = "sqlite"
            self._pool = None
            db_path = db_path or Path("data/ortobahn.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._sqlite_conn.row_factory = sqlite3.Row

        # Query performance stats: {pattern: {count, total_ms, avg_ms, max_ms}}
        self._query_stats: dict[str, dict[str, float]] = {}
        self._query_stats_lock = threading.Lock()

        # In-memory cache with TTL
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = threading.Lock()

        self._create_tables()
        self._run_migrations()

    # ------------------------------------------------------------------
    # Connection context manager (PostgreSQL pool checkout/return)
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _pg_conn(self) -> Generator[Any, None, None]:
        """Checkout a PostgreSQL connection, yield it, then return it.

        On exception the transaction is rolled back before the connection is
        returned to the pool.
        """
        assert self._pool is not None
        conn = self._pool.getconn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    # ------------------------------------------------------------------
    # Low-level DB helpers (backend-agnostic)
    # ------------------------------------------------------------------

    def _convert_query(self, query: str) -> str:
        """Convert ? placeholders to %s for PostgreSQL."""
        if self.backend == "postgresql":
            return query.replace("?", "%s")
        return query

    def _record_query_stats(self, query: str, elapsed_ms: float) -> None:
        """Record timing stats for a query pattern."""
        pattern = _normalize_query(query)
        with self._query_stats_lock:
            entry = self._query_stats.get(pattern)
            if entry is None:
                self._query_stats[pattern] = {
                    "count": 1,
                    "total_ms": elapsed_ms,
                    "avg_ms": elapsed_ms,
                    "max_ms": elapsed_ms,
                }
            else:
                entry["count"] += 1
                entry["total_ms"] += elapsed_ms
                entry["avg_ms"] = entry["total_ms"] / entry["count"]
                if elapsed_ms > entry["max_ms"]:
                    entry["max_ms"] = elapsed_ms

    def execute(self, query: str, params: tuple | list = (), *, commit: bool = False) -> Any:
        """Execute a query. Returns the cursor (sqlite) or None (pg)."""
        converted = self._convert_query(query)
        start = time.monotonic()
        try:
            if self.backend == "postgresql":
                with self._pg_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(converted, tuple(params))
                    if commit:
                        conn.commit()
                return None
            else:
                result = self._sqlite_conn.execute(converted, params)  # type: ignore[union-attr]
                if commit:
                    self._sqlite_conn.commit()  # type: ignore[union-attr]
                return result
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._record_query_stats(query, elapsed_ms)
            if elapsed_ms > _SLOW_QUERY_THRESHOLD * 1000:
                logger.warning(
                    "Slow query (%.1fms): %s",
                    elapsed_ms,
                    _normalize_query(query),
                )
            # Auto-invalidate cache for known tables on write queries
            if commit:
                self._auto_invalidate_cache(query)

    def fetchone(self, query: str, params: tuple | list = ()) -> dict | None:
        """Execute and return one row as dict, or None."""
        converted = self._convert_query(query)
        start = time.monotonic()
        try:
            if self.backend == "postgresql":
                import psycopg2.extras

                with self._pg_conn() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(converted, tuple(params))
                        row = cur.fetchone()
                        return dict(row) if row else None
            else:
                row = self._sqlite_conn.execute(converted, params).fetchone()  # type: ignore[union-attr]
                return dict(row) if row else None
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._record_query_stats(query, elapsed_ms)
            if elapsed_ms > _SLOW_QUERY_THRESHOLD * 1000:
                logger.warning(
                    "Slow query (%.1fms): %s",
                    elapsed_ms,
                    _normalize_query(query),
                )

    def fetchall(self, query: str, params: tuple | list = ()) -> list[dict]:
        """Execute and return all rows as list of dicts."""
        converted = self._convert_query(query)
        start = time.monotonic()
        try:
            if self.backend == "postgresql":
                import psycopg2.extras

                with self._pg_conn() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(converted, tuple(params))
                        rows = cur.fetchall()
                        return [dict(r) for r in rows]
            else:
                rows = self._sqlite_conn.execute(converted, params).fetchall()  # type: ignore[union-attr]
                return [dict(r) for r in rows]
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._record_query_stats(query, elapsed_ms)
            if elapsed_ms > _SLOW_QUERY_THRESHOLD * 1000:
                logger.warning(
                    "Slow query (%.1fms): %s",
                    elapsed_ms,
                    _normalize_query(query),
                )

    def commit(self) -> None:
        """Explicit commit (mainly for SQLite; PG commits per-execute when commit=True)."""
        if self.backend == "sqlite" and self._sqlite_conn:
            self._sqlite_conn.commit()

    # ------------------------------------------------------------------
    # Query profiling
    # ------------------------------------------------------------------

    @property
    def query_stats(self) -> dict[str, dict[str, float]]:
        """Return a copy of accumulated query stats.

        Each key is a normalised query pattern; the value dict contains
        ``count``, ``total_ms``, ``avg_ms``, and ``max_ms``.
        """
        with self._query_stats_lock:
            return {k: dict(v) for k, v in self._query_stats.items()}

    def reset_query_stats(self) -> None:
        """Clear accumulated query stats. Call at the start of each pipeline cycle."""
        with self._query_stats_lock:
            self._query_stats.clear()

    # ------------------------------------------------------------------
    # In-memory cache helpers
    # ------------------------------------------------------------------

    def _cache_get(self, key: str, ttl_seconds: float) -> Any:
        """Return cached value if present and not expired, else ``None``."""
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None and not entry.is_expired(ttl_seconds):
                return entry.value
            # Expired or missing — evict if present
            self._cache.pop(key, None)
            return None

    def _cache_set(self, key: str, value: Any) -> None:
        """Store a value in the cache."""
        with self._cache_lock:
            self._cache[key] = _CacheEntry(value)

    def _cache_invalidate(self, *keys: str) -> None:
        """Remove specific keys from the cache."""
        with self._cache_lock:
            for key in keys:
                self._cache.pop(key, None)

    def _cache_invalidate_prefix(self, prefix: str) -> None:
        """Remove all cache keys that start with *prefix*."""
        with self._cache_lock:
            to_delete = [k for k in self._cache if k.startswith(prefix)]
            for k in to_delete:
                del self._cache[k]

    def clear_cache(self) -> None:
        """Drop the entire in-memory cache."""
        with self._cache_lock:
            self._cache.clear()

    # Table-name patterns for automatic cache invalidation on writes.
    _CACHE_TABLE_PREFIXES: dict[str, list[str]] = {
        "clients": ["client:", "all_clients"],
        "strategies": ["strategy:"],
        "pipeline_runs": ["recent_runs"],
    }

    def _auto_invalidate_cache(self, query: str) -> None:
        """Inspect a write query and invalidate related cache entries.

        This is a best-effort heuristic: it looks for table names in the
        query text and invalidates the associated cache prefixes.
        """
        q_upper = query.upper()
        for table, prefixes in self._CACHE_TABLE_PREFIXES.items():
            if table.upper() in q_upper:
                for prefix in prefixes:
                    self._cache_invalidate_prefix(prefix)

    # ------------------------------------------------------------------
    # Database health metrics
    # ------------------------------------------------------------------

    def get_health_metrics(self) -> dict:
        """Return database health metrics for monitoring.

        Includes:
        - ``table_row_counts``: rows per table
        - ``db_size_bytes``: file size (SQLite) or pool stats (PostgreSQL)
        - ``record_age``: oldest/newest records for key tables
        - ``slow_query_count``: number of query patterns that exceeded the threshold
        - ``pool_stats``: connection pool stats (PostgreSQL only)
        """
        metrics: dict[str, Any] = {}

        # Table row counts
        table_counts: dict[str, int] = {}
        core_tables = [
            "clients",
            "posts",
            "strategies",
            "metrics",
            "agent_logs",
            "pipeline_runs",
            "agent_memories",
        ]
        for table in core_tables:
            try:
                row = self.fetchone(f"SELECT COUNT(*) as cnt FROM {table}")  # noqa: S608
                table_counts[table] = row["cnt"] if row else 0
            except Exception:
                table_counts[table] = -1
        metrics["table_row_counts"] = table_counts

        # DB size / pool stats
        if self.backend == "sqlite" and self._sqlite_conn:
            try:
                page_count = self.fetchone("PRAGMA page_count")
                page_size = self.fetchone("PRAGMA page_size")
                if page_count and page_size:
                    metrics["db_size_bytes"] = page_count["page_count"] * page_size["page_size"]
                else:
                    metrics["db_size_bytes"] = 0
            except Exception:
                metrics["db_size_bytes"] = 0
        elif self.backend == "postgresql" and self._pool:
            metrics["pool_stats"] = {
                "checked_out": self._pool.checked_out,
                "max_connections": self._pool._maxconn,
            }

        # Record age for key tables
        record_age: dict[str, dict[str, str | None]] = {}
        for table in ("posts", "pipeline_runs", "agent_logs"):
            try:
                oldest = self.fetchone(f"SELECT MIN(created_at) as ts FROM {table}")  # noqa: S608
                newest = self.fetchone(f"SELECT MAX(created_at) as ts FROM {table}")  # noqa: S608
                record_age[table] = {
                    "oldest": oldest["ts"] if oldest else None,
                    "newest": newest["ts"] if newest else None,
                }
            except Exception:
                record_age[table] = {"oldest": None, "newest": None}
        metrics["record_age"] = record_age

        # Index usage (SQLite only — list all indices)
        if self.backend == "sqlite":
            try:
                rows = self.fetchall("SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY tbl_name")
                metrics["indexes"] = [{"name": r["name"], "table": r["tbl_name"]} for r in rows]
            except Exception:
                metrics["indexes"] = []

        # Slow query count
        with self._query_stats_lock:
            slow_count = sum(
                1 for stats in self._query_stats.values() if stats["max_ms"] > _SLOW_QUERY_THRESHOLD * 1000
            )
        metrics["slow_query_count"] = slow_count

        metrics["collected_at"] = datetime.now(timezone.utc).isoformat()
        return metrics

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _create_tables(self):
        # Each execute() may use a different pool connection, so commit each
        # table individually to ensure foreign key references resolve.
        self.execute(
            """CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                themes TEXT NOT NULL,
                tone TEXT NOT NULL,
                goals TEXT NOT NULL,
                content_guidelines TEXT NOT NULL,
                posting_frequency TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                valid_until TIMESTAMP NOT NULL,
                run_id TEXT NOT NULL,
                raw_llm_response TEXT
            )""",
            commit=True,
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                source_idea TEXT,
                reasoning TEXT,
                confidence REAL,
                status TEXT NOT NULL,
                bluesky_uri TEXT,
                bluesky_cid TEXT,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id TEXT NOT NULL,
                strategy_id TEXT REFERENCES strategies(id)
            )""",
            commit=True,
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id),
                like_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            commit=True,
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS agent_logs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                reasoning TEXT,
                llm_model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                duration_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                raw_llm_response TEXT
            )""",
            commit=True,
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT NOT NULL,
                posts_published INTEGER DEFAULT 0,
                errors TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0
            )""",
            commit=True,
        )

    def _run_migrations(self):
        from ortobahn.migrations import run_migrations

        run_migrations(self)

    def close(self):
        """Close the database. For PostgreSQL this closes all pooled connections."""
        if self.backend == "postgresql" and self._pool:
            self._pool.closeall()
            self._pool = None
        elif self._sqlite_conn:
            self._sqlite_conn.close()
            self._sqlite_conn = None


def create_database(settings) -> Database:
    """Create a Database instance from settings."""
    return Database(
        db_path=settings.db_path if not settings.database_url else None,
        database_url=settings.database_url,
        pool_min=getattr(settings, "db_pool_min", 2),
        pool_max=getattr(settings, "db_pool_max", 10),
    )
