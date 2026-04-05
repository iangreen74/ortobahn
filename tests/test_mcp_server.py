from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ortobahn.mcp_server import (
    Database,
    Settings,
    app,
    get_database,
    get_settings,
)


@pytest.fixture
def test_settings():
    """Fixture for test settings."""
    return Settings(
        database_url="sqlite:///:memory:",
        log_level="DEBUG",
        host="localhost",
        port=8001,
    )


@pytest.fixture
def test_database(test_settings):
    """Fixture for test database."""
    return Database(test_settings.database_url)


@pytest.fixture
def client(test_settings, test_database):
    """Test client with dependency overrides."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_database] = lambda: test_database

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["log_level"] == "DEBUG"


def test_status(client):
    """Test status endpoint."""
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "ortobahn-mcp"
    assert data["version"] == "0.1.0"
    assert "settings" in data
    assert data["settings"]["host"] == "localhost"
    assert data["settings"]["port"] == 8001


@pytest.mark.asyncio
async def test_database_connection(test_database):
    """Test database connection management."""
    conn = await test_database.get_connection()
    assert conn["connected"] is True
    assert conn["url"] == "sqlite:///:memory:"

    await test_database.release_connection(conn)
    await test_database.close()


def test_settings_model():
    """Test settings model validation."""
    settings = Settings(
        database_url="postgresql://localhost/test",
        log_level="WARNING",
    )
    assert settings.database_url == "postgresql://localhost/test"
    assert settings.log_level == "WARNING"
    assert settings.host == "127.0.0.1"  # Default value
    assert settings.port == 8000  # Default value
