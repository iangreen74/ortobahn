from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Settings(BaseModel):
    """Application settings."""

    database_url: str = "sqlite:///ortobahn.db"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000


class Database:
    """Database connection manager with connection pooling."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: list[Any] = []
        logger.info(f"Database initialized with URL: {database_url}")

    async def get_connection(self):
        """Get a connection from the pool (request-scoped)."""
        # Placeholder for actual connection pooling logic
        logger.debug("Getting database connection from pool")
        return {"url": self.database_url, "connected": True}

    async def release_connection(self, conn: Any) -> None:
        """Release connection back to pool."""
        logger.debug("Releasing database connection to pool")

    async def close(self) -> None:
        """Close all connections in pool."""
        logger.info("Closing database connection pool")
        self._pool.clear()


class AppState:
    """Application state container."""

    def __init__(self):
        self.db: Database | None = None
        self.settings: Settings | None = None


# Global app state container
_app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = Settings()
    db = Database(settings.database_url)

    _app_state.settings = settings
    _app_state.db = db

    logger.info("MCP server startup complete")
    yield

    # Shutdown
    if _app_state.db:
        await _app_state.db.close()
    logger.info("MCP server shutdown complete")


def get_settings() -> Settings:
    """Dependency injection for settings."""
    if _app_state.settings is None:
        raise RuntimeError("Settings not initialized")
    return _app_state.settings


def get_database() -> Database:
    """Dependency injection for database."""
    if _app_state.db is None:
        raise RuntimeError("Database not initialized")
    return _app_state.db


async def get_db_connection(db: Database = Depends(get_database)):
    """Request-scoped database connection dependency."""
    conn = await db.get_connection()
    try:
        yield conn
    finally:
        await db.release_connection(conn)


app = FastAPI(title="Ortobahn MCP Server", lifespan=lifespan)


@app.get("/health")
async def health_check(
    settings: Settings = Depends(get_settings),
    conn=Depends(get_db_connection),
) -> dict[str, str]:
    """Health check endpoint with dependency injection."""
    return {
        "status": "healthy",
        "database": "connected" if conn.get("connected") else "disconnected",
        "log_level": settings.log_level,
    }


@app.get("/status")
async def status(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_database),
) -> dict[str, Any]:
    """Status endpoint showing application info."""
    return {
        "service": "ortobahn-mcp",
        "version": "0.1.0",
        "settings": {
            "host": settings.host,
            "port": settings.port,
            "database_url": settings.database_url,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
