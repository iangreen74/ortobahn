from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import boto3
from pydantic import BaseModel, Field


class Environment(str, Enum):
    """Environment types."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SecretConfig(BaseModel):
    """Configuration for a single secret."""

    key: str
    value: str
    cached_at: datetime
    ttl_seconds: int = 300  # 5 minutes

    def is_expired(self) -> bool:
        """Check if secret cache is expired."""
        return datetime.utcnow() > self.cached_at + timedelta(seconds=self.ttl_seconds)


class SecretsManager:
    """AWS Secrets Manager integration with caching."""

    def __init__(self, region_name: str | None = None, cache_ttl: int = 300) -> None:
        """Initialize secrets manager.

        Args:
            region_name: AWS region name
            cache_ttl: Cache time-to-live in seconds
        """
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self.cache_ttl = cache_ttl
        self._cache: dict[str, SecretConfig] = {}
        self._client = None

    @property
    def client(self):
        """Lazy initialize boto3 client."""
        if self._client is None:
            self._client = boto3.client("secretsmanager", region_name=self.region_name)
        return self._client

    def get_secret(self, secret_name: str, force_refresh: bool = False) -> str:
        """Get secret value with caching.

        Args:
            secret_name: Name of the secret in AWS Secrets Manager
            force_refresh: Force refresh from AWS, bypass cache

        Returns:
            Secret value as string
        """
        # Check cache first
        if not force_refresh and secret_name in self._cache:
            cached = self._cache[secret_name]
            if not cached.is_expired():
                return cached.value

        # Fetch from AWS
        try:
            response = self.client.get_secret_value(SecretId=secret_name)
            secret_value = response["SecretString"]

            # Try to parse as JSON and extract value
            try:
                parsed = json.loads(secret_value)
                if isinstance(parsed, dict) and "value" in parsed:
                    secret_value = parsed["value"]
            except json.JSONDecodeError:
                pass  # Use raw string value

            # Cache the secret
            self._cache[secret_name] = SecretConfig(
                key=secret_name,
                value=secret_value,
                cached_at=datetime.utcnow(),
                ttl_seconds=self.cache_ttl,
            )

            return secret_value
        except Exception as e:
            # Fallback to environment variable if AWS fails
            fallback = os.environ.get(secret_name.upper().replace("-", "_"))
            if fallback:
                return fallback
            raise RuntimeError(f"Failed to retrieve secret {secret_name}: {e}") from e

    def invalidate_cache(self, secret_name: str | None = None) -> None:
        """Invalidate secret cache.

        Args:
            secret_name: Specific secret to invalidate, or None for all
        """
        if secret_name:
            self._cache.pop(secret_name, None)
        else:
            self._cache.clear()


class Config(BaseModel):
    """Application configuration."""

    environment: Environment = Field(default=Environment.DEVELOPMENT)
    database_url: str
    api_key: str
    secret_key: str
    aws_region: str = "us-east-1"
    enable_secrets_manager: bool = True

    @classmethod
    def from_secrets_manager(cls, secrets_manager: SecretsManager | None = None) -> Config:
        """Load configuration from AWS Secrets Manager.

        Args:
            secrets_manager: Optional SecretsManager instance

        Returns:
            Config instance
        """
        sm = secrets_manager or SecretsManager()

        # Determine environment
        env_name = os.environ.get("ENVIRONMENT", "development")
        environment = Environment(env_name)

        # Load secrets based on environment
        prefix = f"ortobahn/{environment.value}"

        return cls(
            environment=environment,
            database_url=sm.get_secret(f"{prefix}/database-url"),
            api_key=sm.get_secret(f"{prefix}/api-key"),
            secret_key=sm.get_secret(f"{prefix}/secret-key"),
            aws_region=sm.region_name,
            enable_secrets_manager=True,
        )

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables (fallback).

        Returns:
            Config instance
        """
        env_name = os.environ.get("ENVIRONMENT", "development")
        return cls(
            environment=Environment(env_name),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///ortobahn.db"),
            api_key=os.environ.get("API_KEY", ""),
            secret_key=os.environ.get("SECRET_KEY", ""),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            enable_secrets_manager=False,
        )


# Global configuration instance
_config: Config | None = None


def get_config(force_refresh: bool = False) -> Config:
    """Get application configuration.

    Args:
        force_refresh: Force reload configuration

    Returns:
        Config instance
    """
    global _config
    if _config is None or force_refresh:
        # Try AWS Secrets Manager first, fallback to env vars
        try:
            _config = Config.from_secrets_manager()
        except Exception:
            _config = Config.from_env()
    return _config
