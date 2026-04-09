"""Tests for memory store with pgvector."""

import json
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.memory import MemoryStore


@pytest.fixture
def mock_db_config():
    """Mock database configuration."""
    return {
        "db_host": "localhost",
        "db_port": 5432,
        "db_name": "test_db",
        "db_user": "test_user",
        "db_password": "test_password",
    }


@pytest.fixture
def mock_bedrock_response():
    """Mock Bedrock embedding response."""
    mock_response = MagicMock()
    mock_response["body"].read.return_value = json.dumps(
        {"embedding": [0.1] * 1536}
    ).encode()
    return mock_response


@patch("ortobahn.memory.psycopg2.connect")
@patch("ortobahn.memory.boto3.client")
def test_memory_store_init(mock_boto_client, mock_connect, mock_db_config):
    """Test memory store initialization."""
    mock_conn = MagicMock()
    mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_connect.return_value.__exit__ = MagicMock()

    store = MemoryStore(**mock_db_config)

    assert store.top_k == 10
    mock_connect.assert_called_once()
    mock_boto_client.assert_called_once_with(
        "bedrock-runtime", region_name="us-east-1"
    )


@patch("ortobahn.memory.psycopg2.connect")
@patch("ortobahn.memory.boto3.client")
def test_store_memory(
    mock_boto_client, mock_connect, mock_db_config, mock_bedrock_response
):
    """Test storing a memory."""
    mock_conn = MagicMock()
    mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_connect.return_value.__exit__ = MagicMock()

    mock_bedrock = MagicMock()
    mock_bedrock.invoke_model.return_value = mock_bedrock_response
    mock_boto_client.return_value = mock_bedrock

    store = MemoryStore(**mock_db_config)
    store.store("Test memory", {"key": "value"})

    mock_bedrock.invoke_model.assert_called()


@patch("ortobahn.memory.psycopg2.connect")
@patch("ortobahn.memory.boto3.client")
def test_search_memories(
    mock_boto_client, mock_connect, mock_db_config, mock_bedrock_response
):
    """Test searching memories."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {
            "id": 1,
            "content": "Test memory",
            "metadata": {"key": "value"},
            "created_at": MagicMock(isoformat=lambda: "2023-01-01T00:00:00"),
            "relevance_score": 0.95,
        }
    ]
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock()
    mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_connect.return_value.__exit__ = MagicMock()

    mock_bedrock = MagicMock()
    mock_bedrock.invoke_model.return_value = mock_bedrock_response
    mock_boto_client.return_value = mock_bedrock

    store = MemoryStore(**mock_db_config)
    results = store.search("query")

    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["relevance_score"] == 0.95


@patch("ortobahn.memory.psycopg2.connect")
@patch("ortobahn.memory.boto3.client")
def test_delete_memory(mock_boto_client, mock_connect, mock_db_config):
    """Test deleting a memory."""
    mock_conn = MagicMock()
    mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_connect.return_value.__exit__ = MagicMock()

    store = MemoryStore(**mock_db_config)
    store.delete(1)

    mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called()
