"""Database connection pool management."""

import asyncpg
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DatabasePool:
    """Manages asyncpg connection pool with lifecycle management."""

    def __init__(self, min_size: int = 10, max_size: int = 50):
        self._pool: Optional[asyncpg.Pool] = None
        self._min_size = min_size
        self._max_size = max_size

    async def initialize(self, dsn: str) -> None:
        """Initialize the connection pool.
        
        Args:
            dsn: Database connection string
        """
        if self._pool is not None:
            logger.warning("Pool already initialized")
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=60,
            )
            logger.info(
                f"Database pool initialized (min={self._min_size}, max={self._max_size})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")
            raise

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed")

    async def health_check(self) -> bool:
        """Perform health check on the connection pool.
        
        Returns:
            True if pool is healthy, False otherwise
        """
        if self._pool is None:
            return False

        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                return result == 1
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def get_pool(self) -> asyncpg.Pool:
        """Get the connection pool.
        
        Returns:
            The asyncpg connection pool
            
        Raises:
            RuntimeError: If pool is not initialized
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        return self._pool

    async def acquire(self):
        """Acquire a connection from the pool.
        
        Returns:
            Connection context manager
        """
        return self.get_pool().acquire()

    async def execute(self, query: str, *args, timeout: Optional[float] = None):
        """Execute a query.
        
        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout
            
        Returns:
            Query result
        """
        async with self.acquire() as conn:
            return await conn.execute(query, *args, timeout=timeout)

    async def fetch(self, query: str, *args, timeout: Optional[float] = None):
        """Fetch multiple rows.
        
        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout
            
        Returns:
            List of records
        """
        async with self.acquire() as conn:
            return await conn.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args, timeout: Optional[float] = None):
        """Fetch a single row.
        
        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout
            
        Returns:
            Single record or None
        """
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args, timeout: Optional[float] = None):
        """Fetch a single value.
        
        Args:
            query: SQL query
            *args: Query parameters
            timeout: Query timeout
            
        Returns:
            Single value or None
        """
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args, timeout=timeout)
