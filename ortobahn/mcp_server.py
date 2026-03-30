"""MCP server with dependency injection pattern."""

import logging
from typing import Optional
from contextlib import asynccontextmanager

from ortobahn.db import DatabasePool
from ortobahn.config import Settings

logger = logging.getLogger(__name__)


class MCPServer:
    """MCP Server with injected dependencies."""

    def __init__(self, settings: Settings, db_pool: DatabasePool):
        """Initialize MCP server with dependencies.
        
        Args:
            settings: Application settings
            db_pool: Database connection pool
        """
        self.settings = settings
        self.db_pool = db_pool
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the MCP server."""
        if self._initialized:
            logger.warning("Server already initialized")
            return

        try:
            # Initialize database pool
            await self.db_pool.initialize(self.settings.database_url)
            
            # Perform health check
            if not await self.db_pool.health_check():
                raise RuntimeError("Database health check failed")
            
            self._initialized = True
            logger.info("MCP server initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize MCP server: {e}")
            raise

    async def shutdown(self) -> None:
        """Shutdown the MCP server."""
        if not self._initialized:
            return

        try:
            await self.db_pool.close()
            self._initialized = False
            logger.info("MCP server shut down successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise

    async def health_check(self) -> dict:
        """Perform comprehensive health check.
        
        Returns:
            Health status dictionary
        """
        db_healthy = await self.db_pool.health_check()
        
        return {
            "status": "healthy" if db_healthy else "unhealthy",
            "database": db_healthy,
            "initialized": self._initialized,
        }


@asynccontextmanager
async def create_mcp_server(
    settings: Optional[Settings] = None,
    min_pool_size: int = 10,
    max_pool_size: int = 50,
):
    """Create and manage MCP server lifecycle.
    
    Args:
        settings: Application settings (uses default if None)
        min_pool_size: Minimum connection pool size
        max_pool_size: Maximum connection pool size
        
    Yields:
        Initialized MCPServer instance
    """
    if settings is None:
        settings = Settings()
    
    db_pool = DatabasePool(min_size=min_pool_size, max_size=max_pool_size)
    server = MCPServer(settings=settings, db_pool=db_pool)
    
    try:
        await server.initialize()
        yield server
    finally:
        await server.shutdown()


async def get_server_dependencies() -> tuple[Settings, DatabasePool]:
    """Factory function for creating server dependencies.
    
    Returns:
        Tuple of (settings, db_pool)
    """
    settings = Settings()
    db_pool = DatabasePool(min_size=10, max_size=50)
    return settings, db_pool
