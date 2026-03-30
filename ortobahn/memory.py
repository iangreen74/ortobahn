"""Memory store with pgvector semantic similarity search."""

import json
import logging
from typing import Any, Dict, List, Optional

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class MemoryStore:
    """Memory store using PostgreSQL with pgvector for semantic search."""

    def __init__(
        self,
        db_host: str,
        db_port: int,
        db_name: str,
        db_user: str,
        db_password: str,
        aws_region: str = "us-east-1",
        top_k: int = 10,
    ):
        """Initialize memory store with database and AWS Bedrock.

        Args:
            db_host: PostgreSQL host
            db_port: PostgreSQL port
            db_name: Database name
            db_user: Database user
            db_password: Database password
            aws_region: AWS region for Bedrock
            top_k: Number of top results to retrieve
        """
        self.db_config = {
            "host": db_host,
            "port": db_port,
            "database": db_name,
            "user": db_user,
            "password": db_password,
        }
        self.top_k = top_k
        self.bedrock_client = boto3.client(
            "bedrock-runtime", region_name=aws_region
        )
        self._init_db()

    def _get_connection(self):
        """Get database connection."""
        return psycopg2.connect(**self.db_config)

    def _init_db(self):
        """Initialize database with pgvector extension and tables."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        metadata JSONB,
                        embedding vector(1536),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS memories_embedding_idx
                    ON memories USING hnsw (embedding vector_cosine_ops);
                """)
            conn.commit()

    def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using AWS Bedrock Titan.

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
        try:
            response = self.bedrock_client.invoke_model(
                modelId="amazon.titan-embed-text-v1",
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"inputText": text}),
            )
            result = json.loads(response["body"].read())
            return result["embedding"]
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    def store(self, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Store memory with embedding.

        Args:
            content: Memory content
            metadata: Optional metadata
        """
        embedding = self._generate_embedding(content)
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memories (content, metadata, embedding)
                    VALUES (%s, %s, %s)
                    """,
                    (content, json.dumps(metadata or {}), embedding),
                )
            conn.commit()

    def search(
        self, query: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Search memories by semantic similarity.

        Args:
            query: Search query
            limit: Max results (default: top_k)

        Returns:
            List of memories with relevance scores
        """
        limit = limit or self.top_k
        query_embedding = self._generate_embedding(query)

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        content,
                        metadata,
                        created_at,
                        1 - (embedding <=> %s::vector) AS relevance_score
                    FROM memories
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_embedding, query_embedding, limit),
                )
                results = cur.fetchall()

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "metadata": row["metadata"],
                "created_at": row["created_at"].isoformat(),
                "relevance_score": float(row["relevance_score"]),
            }
            for row in results
        ]

    def delete(self, memory_id: int):
        """Delete memory by ID.

        Args:
            memory_id: Memory ID
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
            conn.commit()
