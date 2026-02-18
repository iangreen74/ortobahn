"""Tests for PostgreSQL connection pooling in ortobahn.db."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from ortobahn.config import Settings
from ortobahn.db import Database, PoolExhaustedError, _HealthCheckedPool, create_database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeCursor:
    """Minimal cursor that records execute calls."""

    def __init__(self, rows=None):
        self._rows = rows or []

    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    """Mimics a psycopg2 connection with health-check support."""

    def __init__(self, *, alive: bool = True):
        self._alive = alive
        self._committed = False
        self._rolled_back = False
        self.info = MagicMock()
        self.info.transaction_status = 0  # IDLE

    def cursor(self, cursor_factory=None):
        if not self._alive:
            raise Exception("connection is dead")
        return FakeCursor()

    def commit(self):
        self._committed = True

    def rollback(self):
        self._rolled_back = True

    def close(self):
        self._alive = False


# ---------------------------------------------------------------------------
# 1. Pool is created when database_url is set
# ---------------------------------------------------------------------------


class TestPoolCreation:
    def test_sqlite_mode_has_no_pool(self, tmp_path):
        """When no database_url is provided, backend is sqlite and no pool exists."""
        db = Database(db_path=tmp_path / "test.db")
        try:
            assert db.backend == "sqlite"
            assert db._pool is None
            assert db._sqlite_conn is not None
        finally:
            db.close()

    @patch("ortobahn.db._HealthCheckedPool")
    def test_pg_mode_creates_pool(self, mock_pool_cls):
        """When database_url is set, a _HealthCheckedPool is created with correct params."""
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool

        # We need to patch _create_tables and _run_migrations because they would
        # try to use the mock pool to actually run SQL.
        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://user:pass@localhost/testdb", pool_min=3, pool_max=15)

        assert db.backend == "postgresql"
        assert db._pool is mock_pool
        mock_pool_cls.assert_called_once_with(
            minconn=3,
            maxconn=15,
            dsn="postgresql://user:pass@localhost/testdb",
        )

    @patch("ortobahn.db._HealthCheckedPool")
    def test_create_database_passes_pool_settings(self, mock_pool_cls):
        """create_database() forwards db_pool_min / db_pool_max from Settings."""
        mock_pool_cls.return_value = MagicMock()
        settings = Settings(
            anthropic_api_key="sk-ant-test-key-1234567890",
            database_url="postgresql://u:p@host/db",
            db_pool_min=4,
            db_pool_max=20,
        )
        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = create_database(settings)

        mock_pool_cls.assert_called_once_with(
            minconn=4,
            maxconn=20,
            dsn="postgresql://u:p@host/db",
        )
        assert db.backend == "postgresql"


# ---------------------------------------------------------------------------
# 2. Connections are properly returned to the pool
# ---------------------------------------------------------------------------


class TestConnectionReturn:
    @patch("ortobahn.db._HealthCheckedPool")
    def test_execute_returns_conn_on_success(self, mock_pool_cls):
        """After execute(), the connection is returned to the pool."""
        mock_pool = MagicMock()
        fake_conn = FakeConnection()
        mock_pool.getconn.return_value = fake_conn
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        db.execute("SELECT 1", commit=True)

        mock_pool.getconn.assert_called_once()
        mock_pool.putconn.assert_called_once_with(fake_conn)
        assert fake_conn._committed

    @patch("ortobahn.db._HealthCheckedPool")
    def test_execute_returns_conn_on_error(self, mock_pool_cls):
        """On exception, the connection is still returned after rollback."""
        mock_pool = MagicMock()
        bad_conn = MagicMock()
        bad_cursor = MagicMock()
        bad_cursor.__enter__ = MagicMock(return_value=bad_cursor)
        bad_cursor.__exit__ = MagicMock(return_value=False)
        bad_cursor.execute.side_effect = Exception("syntax error")
        bad_conn.cursor.return_value = bad_cursor
        mock_pool.getconn.return_value = bad_conn
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        with pytest.raises(Exception, match="syntax error"):
            db.execute("BAD SQL")

        bad_conn.rollback.assert_called_once()
        mock_pool.putconn.assert_called_once_with(bad_conn)

    @patch("ortobahn.db._HealthCheckedPool")
    def test_fetchone_returns_conn(self, mock_pool_cls):
        """fetchone() properly returns the connection."""
        mock_pool = MagicMock()

        # Build a fake connection whose cursor supports cursor_factory kwarg
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = None
        fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
        fake_cursor.__exit__ = MagicMock(return_value=False)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        mock_pool.getconn.return_value = fake_conn
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        db.fetchone("SELECT 1")

        mock_pool.putconn.assert_called_once_with(fake_conn)

    @patch("ortobahn.db._HealthCheckedPool")
    def test_fetchall_returns_conn(self, mock_pool_cls):
        """fetchall() properly returns the connection."""
        mock_pool = MagicMock()

        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = []
        fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
        fake_cursor.__exit__ = MagicMock(return_value=False)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        mock_pool.getconn.return_value = fake_conn
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        db.fetchall("SELECT 1")

        mock_pool.putconn.assert_called_once_with(fake_conn)


# ---------------------------------------------------------------------------
# 3. Pool respects max_connections (exhaustion + timeout)
# ---------------------------------------------------------------------------


class TestPoolExhaustion:
    def test_pool_exhausted_error_is_raised(self):
        """When the pool is exhausted and timeout expires, PoolExhaustedError is raised."""
        import psycopg2.pool

        mock_inner = MagicMock()
        mock_inner.getconn.side_effect = psycopg2.pool.PoolError("exhausted")

        pool = _HealthCheckedPool.__new__(_HealthCheckedPool)
        pool._inner = mock_inner
        pool._maxconn = 2
        pool._checkout_timeout = 0.1  # very short for fast test
        pool._cond = threading.Condition(threading.Lock())
        pool.checked_out = 0

        with pytest.raises(PoolExhaustedError, match="Could not obtain a database connection"):
            pool.getconn()

    def test_pool_waits_then_succeeds(self):
        """When pool is temporarily exhausted, getconn waits and succeeds after putconn."""
        import psycopg2.pool

        call_count = 0
        fake_conn = FakeConnection()

        def getconn_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise psycopg2.pool.PoolError("exhausted")
            return fake_conn

        mock_inner = MagicMock()
        mock_inner.getconn.side_effect = getconn_side_effect

        pool = _HealthCheckedPool.__new__(_HealthCheckedPool)
        pool._inner = mock_inner
        pool._maxconn = 2
        pool._checkout_timeout = 2.0
        pool._cond = threading.Condition(threading.Lock())
        pool.checked_out = 0

        # Simulate another thread returning a connection after a short delay
        def release_later():
            time.sleep(0.15)
            with pool._cond:
                pool._cond.notify()

        t = threading.Thread(target=release_later)
        t.start()

        conn = pool.getconn()
        t.join()
        assert conn is fake_conn

    def test_checked_out_tracking(self):
        """checked_out counter increments on getconn and decrements on putconn."""
        fake_conn = FakeConnection()

        mock_inner = MagicMock()
        mock_inner.getconn.return_value = fake_conn

        pool = _HealthCheckedPool.__new__(_HealthCheckedPool)
        pool._inner = mock_inner
        pool._maxconn = 5
        pool._checkout_timeout = 1.0
        pool._cond = threading.Condition(threading.Lock())
        pool.checked_out = 0

        conn = pool.getconn()
        assert pool.checked_out == 1

        pool.putconn(conn)
        assert pool.checked_out == 0


# ---------------------------------------------------------------------------
# 4. Health check (test on borrow)
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy_connection_passes(self):
        """A healthy connection is returned without issue."""
        fake_conn = FakeConnection(alive=True)
        mock_inner = MagicMock()
        mock_inner.getconn.return_value = fake_conn

        pool = _HealthCheckedPool.__new__(_HealthCheckedPool)
        pool._inner = mock_inner
        pool._maxconn = 5
        pool._checkout_timeout = 1.0
        pool._cond = threading.Condition(threading.Lock())
        pool.checked_out = 0

        conn = pool.getconn()
        assert conn is fake_conn

    def test_dead_connection_is_discarded(self):
        """A dead connection triggers discard and a fresh one is obtained."""
        dead_conn = FakeConnection(alive=False)
        good_conn = FakeConnection(alive=True)

        call_count = 0

        def getconn_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return dead_conn
            return good_conn

        mock_inner = MagicMock()
        mock_inner.getconn.side_effect = getconn_side_effect

        pool = _HealthCheckedPool.__new__(_HealthCheckedPool)
        pool._inner = mock_inner
        pool._maxconn = 5
        pool._checkout_timeout = 1.0
        pool._cond = threading.Condition(threading.Lock())
        pool.checked_out = 0

        conn = pool.getconn()
        assert conn is good_conn
        # The dead connection should have been passed back with close=True
        mock_inner.putconn.assert_called_once_with(dead_conn, close=True)

    def test_ping_detects_dead_connection(self):
        """_ping returns False for a dead connection."""
        dead = FakeConnection(alive=False)
        assert _HealthCheckedPool._ping(dead) is False

    def test_ping_detects_alive_connection(self):
        """_ping returns True for a healthy connection."""
        alive = FakeConnection(alive=True)
        assert _HealthCheckedPool._ping(alive) is True


# ---------------------------------------------------------------------------
# 5. SQLite mode is unaffected
# ---------------------------------------------------------------------------


class TestSQLiteUnaffected:
    def test_sqlite_operations_work(self, tmp_path):
        """Full CRUD cycle on SQLite to confirm no regressions."""
        db = Database(db_path=tmp_path / "test.db")
        try:
            assert db.backend == "sqlite"
            assert db._pool is None

            # Tables should have been created by __init__
            rows = db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r["name"] for r in rows]
            assert "posts" in tables
            assert "strategies" in tables

            # Basic insert + fetch
            db.execute(
                "INSERT INTO agent_logs (id, run_id, agent_name) VALUES (?, ?, ?)",
                ("log-1", "run-1", "test-agent"),
                commit=True,
            )
            row = db.fetchone("SELECT * FROM agent_logs WHERE id=?", ("log-1",))
            assert row is not None
            assert row["agent_name"] == "test-agent"

            # fetchall
            logs = db.fetchall("SELECT * FROM agent_logs")
            assert len(logs) == 1

            # commit() is a no-op-like call
            db.commit()
        finally:
            db.close()

    def test_sqlite_create_database_factory(self, tmp_path):
        """create_database with no database_url uses SQLite."""
        settings = Settings(
            anthropic_api_key="sk-ant-test-key-1234567890",
            database_url="",
            db_path=tmp_path / "factory.db",
        )
        db = create_database(settings)
        try:
            assert db.backend == "sqlite"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 6. close() cleans up properly
# ---------------------------------------------------------------------------


class TestClose:
    def test_sqlite_close(self, tmp_path):
        """close() on SQLite closes the connection and sets it to None."""
        db = Database(db_path=tmp_path / "close_test.db")
        assert db._sqlite_conn is not None
        db.close()
        assert db._sqlite_conn is None

    @patch("ortobahn.db._HealthCheckedPool")
    def test_pg_close_calls_closeall(self, mock_pool_cls):
        """close() on PostgreSQL calls closeall() on the pool and sets pool to None."""
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        assert db._pool is mock_pool
        db.close()

        mock_pool.closeall.assert_called_once()
        assert db._pool is None

    @patch("ortobahn.db._HealthCheckedPool")
    def test_double_close_is_safe(self, mock_pool_cls):
        """Calling close() twice does not raise."""
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool

        with patch.object(Database, "_create_tables"), patch.object(Database, "_run_migrations"):
            db = Database(database_url="postgresql://u:p@h/d")

        db.close()
        db.close()  # should not raise

    def test_sqlite_double_close_is_safe(self, tmp_path):
        """Calling close() twice on SQLite does not raise."""
        db = Database(db_path=tmp_path / "double.db")
        db.close()
        db.close()  # should not raise


# ---------------------------------------------------------------------------
# 7. Config integration
# ---------------------------------------------------------------------------


class TestConfigPoolSettings:
    def test_default_pool_settings(self):
        """Default pool settings are 2 and 10."""
        s = Settings(anthropic_api_key="sk-ant-test-key-1234567890")
        assert s.db_pool_min == 2
        assert s.db_pool_max == 10

    def test_custom_pool_settings(self):
        """Pool settings can be overridden."""
        s = Settings(
            anthropic_api_key="sk-ant-test-key-1234567890",
            db_pool_min=5,
            db_pool_max=25,
        )
        assert s.db_pool_min == 5
        assert s.db_pool_max == 25

    def test_pool_settings_from_env(self, monkeypatch):
        """Pool settings are read from environment variables."""
        monkeypatch.setenv("DB_POOL_MIN", "3")
        monkeypatch.setenv("DB_POOL_MAX", "15")

        from ortobahn.config import load_settings

        settings = load_settings()
        assert settings.db_pool_min == 3
        assert settings.db_pool_max == 15
