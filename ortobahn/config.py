"""Configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    # Required
    anthropic_api_key: str = ""
    bluesky_handle: str = ""
    bluesky_app_password: str = ""

    # Optional APIs
    newsapi_key: str | None = None

    # Twitter/X
    twitter_api_key: str = ""
    twitter_api_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""

    # LinkedIn
    linkedin_access_token: str = ""
    linkedin_person_urn: str = ""

    # Autonomous mode (auto-publish above confidence threshold)
    autonomous_mode: bool = True

    # Claude settings
    claude_model: str = "claude-sonnet-4-5-20250929"
    claude_max_tokens: int = 4096
    thinking_budget_ceo: int = 10_000
    thinking_budget_strategist: int = 8_000
    thinking_budget_creator: int = 6_000
    thinking_budget_legal: int = 10_000
    thinking_budget_security: int = 8_000

    # Bedrock (uses IAM auth instead of API key)
    use_bedrock: bool = False
    bedrock_region: str = "us-west-2"

    # Database
    database_url: str = ""  # PostgreSQL: postgresql://user:pass@host:5432/dbname
    db_path: Path = Path("data/ortobahn.db")  # SQLite fallback (ignored if database_url set)
    db_pool_min: int = 2  # Minimum connections in PostgreSQL pool
    db_pool_max: int = 10  # Maximum connections in PostgreSQL pool

    # Pipeline
    post_confidence_threshold: float = 0.7
    pipeline_interval_hours: int = 8
    max_posts_per_cycle: int = 4

    # Default client
    default_client_id: str = "default"

    # Web dashboard
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Logging
    log_level: str = "INFO"

    # Budget enforcement
    default_monthly_budget: float = 0.0  # 0 = unlimited

    # Rate limiting
    post_delay_seconds: int = 30
    rate_limit_enabled: bool = True
    rate_limit_default: int = 60

    # Slack alerting
    slack_webhook_url: str = ""

    # Backups
    backup_enabled: bool = True
    backup_dir: Path = Path("data/backups")
    backup_max_count: int = 10

    # Authentication
    secret_key: str = ""
    admin_api_key: str = ""

    # Cognito
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_region: str = "us-west-2"

    # Intelligence system
    thinking_budget_reflection: int = 8_000
    enable_self_critique: bool = True
    memory_max_per_agent: int = 100
    memory_prune_days: int = 90
    ab_testing_enabled: bool = True
    min_ab_pairs: int = 5
    creator_critique_threshold: float = 0.8

    # Preflight intelligence
    preflight_enabled: bool = True

    # CI/CD self-healing
    cifix_enabled: bool = True
    cifix_auto_pr: bool = True
    cifix_max_llm_attempts: int = 2

    # Ortobahn self-marketing Bluesky credentials
    ortobahn_bluesky_handle: str = ""
    ortobahn_bluesky_app_password: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""

    # Watchdog
    watchdog_enabled: bool = True
    watchdog_stale_run_minutes: int = 60
    watchdog_post_verify_hours: int = 6
    watchdog_credential_check: bool = True
    watchdog_max_verify_posts: int = 5

    # Auto-rollback
    auto_rollback_enabled: bool = True
    auto_rollback_window_minutes: int = 30  # Only rollback if deploy was within this window
    auto_rollback_health_failures: int = 3  # Consecutive health failures before rollback

    # RSS feeds
    rss_feeds: list[str] = field(
        default_factory=lambda: [
            "https://feeds.arstechnica.com/arstechnica/technology-lab",
            "https://news.ycombinator.com/rss",
            "https://techcrunch.com/feed/",
            "https://www.theverge.com/rss/index.xml",
        ]
    )

    def validate(self, require_bluesky: bool = False) -> list[str]:
        """Validate configuration. Returns list of error strings (empty = valid)."""
        errors = []

        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set")
        elif not self.anthropic_api_key.startswith("sk-ant-"):
            errors.append("ANTHROPIC_API_KEY does not look valid (should start with 'sk-ant-')")

        if require_bluesky:
            if not self.bluesky_handle:
                errors.append("BLUESKY_HANDLE is not set")
            elif "." not in self.bluesky_handle:
                errors.append("BLUESKY_HANDLE format looks wrong (expected: user.bsky.social)")
            if not self.bluesky_app_password:
                errors.append("BLUESKY_APP_PASSWORD is not set")

        if not (0.0 <= self.post_confidence_threshold <= 1.0):
            errors.append(f"POST_CONFIDENCE_THRESHOLD must be 0-1, got {self.post_confidence_threshold}")

        if self.pipeline_interval_hours < 1:
            errors.append(f"PIPELINE_INTERVAL_HOURS must be >= 1, got {self.pipeline_interval_hours}")

        if self.max_posts_per_cycle < 1:
            errors.append(f"MAX_POSTS_PER_CYCLE must be >= 1, got {self.max_posts_per_cycle}")

        return errors

    def has_twitter(self) -> bool:
        return bool(
            self.twitter_api_key
            and self.twitter_api_secret
            and self.twitter_access_token
            and self.twitter_access_token_secret
        )

    def has_linkedin(self) -> bool:
        return bool(self.linkedin_access_token and self.linkedin_person_urn)


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        bluesky_handle=os.environ.get("BLUESKY_HANDLE", ""),
        bluesky_app_password=os.environ.get("BLUESKY_APP_PASSWORD", ""),
        newsapi_key=os.environ.get("NEWSAPI_KEY") or None,
        twitter_api_key=os.environ.get("TWITTER_API_KEY", ""),
        twitter_api_secret=os.environ.get("TWITTER_API_SECRET", ""),
        twitter_access_token=os.environ.get("TWITTER_ACCESS_TOKEN", ""),
        twitter_access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""),
        linkedin_access_token=os.environ.get("LINKEDIN_ACCESS_TOKEN", ""),
        linkedin_person_urn=os.environ.get("LINKEDIN_PERSON_URN", ""),
        autonomous_mode=os.environ.get("AUTONOMOUS_MODE", "true").lower() in ("true", "1", "yes"),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
        claude_max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "4096")),
        thinking_budget_ceo=int(os.environ.get("THINKING_BUDGET_CEO", "10000")),
        thinking_budget_strategist=int(os.environ.get("THINKING_BUDGET_STRATEGIST", "8000")),
        thinking_budget_creator=int(os.environ.get("THINKING_BUDGET_CREATOR", "6000")),
        thinking_budget_legal=int(os.environ.get("THINKING_BUDGET_LEGAL", "10000")),
        thinking_budget_security=int(os.environ.get("THINKING_BUDGET_SECURITY", "8000")),
        use_bedrock=os.environ.get("USE_BEDROCK", "false").lower() in ("true", "1", "yes"),
        bedrock_region=os.environ.get("BEDROCK_REGION", "us-west-2"),
        database_url=os.environ.get("DATABASE_URL", ""),
        db_path=Path(os.environ.get("DB_PATH", "data/ortobahn.db")),
        db_pool_min=int(os.environ.get("DB_POOL_MIN", "2")),
        db_pool_max=int(os.environ.get("DB_POOL_MAX", "10")),
        post_confidence_threshold=float(os.environ.get("POST_CONFIDENCE_THRESHOLD", "0.7")),
        pipeline_interval_hours=int(os.environ.get("PIPELINE_INTERVAL_HOURS", "8")),
        max_posts_per_cycle=int(os.environ.get("MAX_POSTS_PER_CYCLE", "4")),
        default_client_id=os.environ.get("DEFAULT_CLIENT_ID", "default"),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
        web_port=int(os.environ.get("WEB_PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        default_monthly_budget=float(os.environ.get("DEFAULT_MONTHLY_BUDGET", "0")),
        post_delay_seconds=int(os.environ.get("POST_DELAY_SECONDS", "30")),
        rate_limit_enabled=os.environ.get("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes"),
        rate_limit_default=int(os.environ.get("RATE_LIMIT_DEFAULT", "60")),
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        backup_enabled=os.environ.get("BACKUP_ENABLED", "true").lower() in ("true", "1", "yes"),
        backup_dir=Path(os.environ.get("BACKUP_DIR", "data/backups")),
        backup_max_count=int(os.environ.get("BACKUP_MAX_COUNT", "10")),
        secret_key=os.environ.get("ORTOBAHN_SECRET_KEY", ""),
        admin_api_key=os.environ.get("ADMIN_API_KEY", ""),
        thinking_budget_reflection=int(os.environ.get("THINKING_BUDGET_REFLECTION", "8000")),
        enable_self_critique=os.environ.get("ENABLE_SELF_CRITIQUE", "true").lower() in ("true", "1", "yes"),
        memory_max_per_agent=int(os.environ.get("MEMORY_MAX_PER_AGENT", "100")),
        memory_prune_days=int(os.environ.get("MEMORY_PRUNE_DAYS", "90")),
        ab_testing_enabled=os.environ.get("AB_TESTING_ENABLED", "true").lower() in ("true", "1", "yes"),
        min_ab_pairs=int(os.environ.get("MIN_AB_PAIRS", "5")),
        creator_critique_threshold=float(os.environ.get("CREATOR_CRITIQUE_THRESHOLD", "0.8")),
        preflight_enabled=os.environ.get("PREFLIGHT_ENABLED", "true").lower() in ("true", "1", "yes"),
        cifix_enabled=os.environ.get("CIFIX_ENABLED", "true").lower() in ("true", "1", "yes"),
        cifix_auto_pr=os.environ.get("CIFIX_AUTO_PR", "true").lower() in ("true", "1", "yes"),
        cifix_max_llm_attempts=int(os.environ.get("CIFIX_MAX_LLM_ATTEMPTS", "2")),
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
        stripe_publishable_key=os.environ.get("STRIPE_PUBLISHABLE_KEY", ""),
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        stripe_price_id=os.environ.get("STRIPE_PRICE_ID", ""),
        cognito_user_pool_id=os.environ.get("COGNITO_USER_POOL_ID", ""),
        cognito_client_id=os.environ.get("COGNITO_CLIENT_ID", ""),
        cognito_region=os.environ.get("COGNITO_REGION", "us-west-2"),
        ortobahn_bluesky_handle=os.environ.get("ORTOBAHN_BLUESKY_HANDLE", ""),
        ortobahn_bluesky_app_password=os.environ.get("ORTOBAHN_BLUESKY_APP_PASSWORD", ""),
        watchdog_enabled=os.environ.get("WATCHDOG_ENABLED", "true").lower() in ("true", "1", "yes"),
        watchdog_stale_run_minutes=int(os.environ.get("WATCHDOG_STALE_RUN_MINUTES", "60")),
        watchdog_post_verify_hours=int(os.environ.get("WATCHDOG_POST_VERIFY_HOURS", "6")),
        watchdog_credential_check=os.environ.get("WATCHDOG_CREDENTIAL_CHECK", "true").lower() in ("true", "1", "yes"),
        watchdog_max_verify_posts=int(os.environ.get("WATCHDOG_MAX_VERIFY_POSTS", "5")),
        auto_rollback_enabled=os.environ.get("AUTO_ROLLBACK_ENABLED", "true").lower() in ("true", "1", "yes"),
        auto_rollback_window_minutes=int(os.environ.get("AUTO_ROLLBACK_WINDOW_MINUTES", "30")),
        auto_rollback_health_failures=int(os.environ.get("AUTO_ROLLBACK_HEALTH_FAILURES", "3")),
    )
