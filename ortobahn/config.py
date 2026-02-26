"""Configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from ortobahn.constants import DEFAULT_CLIENT_ID


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

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""

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
    default_client_id: str = DEFAULT_CLIENT_ID

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
    rate_limit_window_seconds: int = 60

    # Slack alerting
    slack_webhook_url: str = ""
    slack_signing_secret: str = ""

    # Backups
    backup_enabled: bool = True
    backup_dir: Path = Path("data/backups")
    backup_max_count: int = 10

    # S3 Backups
    s3_backup_enabled: bool = False
    s3_backup_bucket: str = ""
    s3_backup_prefix: str = "backups/"
    s3_backup_schedule_hours: int = 24
    s3_backup_retention_days: int = 30
    s3_region: str = "us-west-2"

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

    # CTO Agent (autonomous engineering)
    cto_enabled: bool = True
    cto_max_tasks_per_cycle: int = 1
    thinking_budget_cto: int = 16_000

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

    # Engagement agent
    engagement_enabled: bool = True
    engagement_max_replies: int = 3
    engagement_confidence_threshold: float = 0.75

    # Style evolution (A/B testing)
    style_evolution_enabled: bool = True

    # Predictive timing
    predictive_timing_enabled: bool = True

    # Content serialization
    serialization_enabled: bool = True

    # Post feedback loop (real-time learning)
    post_feedback_enabled: bool = True
    post_feedback_delay_seconds: int = 600

    # Cross-client meta-learning
    meta_learning_enabled: bool = True

    # Publisher error recovery
    publish_retry_enabled: bool = True
    publish_max_retries: int = 2

    # Dynamic posting cadence
    dynamic_cadence_enabled: bool = True

    # Article generation
    thinking_budget_article_writer: int = 16_000
    article_confidence_threshold: float = 0.8

    # Email digest (AWS SES)
    ses_region: str = "us-west-2"
    ses_sender_email: str = ""
    digest_enabled_global: bool = True

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

        # Thinking budgets
        for name in (
            "thinking_budget_reflection",
            "thinking_budget_ceo",
            "thinking_budget_strategist",
            "thinking_budget_creator",
            "thinking_budget_legal",
            "thinking_budget_security",
            "thinking_budget_cto",
            "thinking_budget_article_writer",
        ):
            val = getattr(self, name)
            if not (1024 <= val <= 128_000):
                errors.append(f"{name} must be 1024-128000, got {val}")

        # Pool sizes
        if self.db_pool_min < 1:
            errors.append(f"db_pool_min must be >= 1, got {self.db_pool_min}")
        if self.db_pool_max < self.db_pool_min:
            errors.append(f"db_pool_max ({self.db_pool_max}) must be >= db_pool_min ({self.db_pool_min})")

        # Retry counts
        if not (0 <= self.publish_max_retries <= 10):
            errors.append(f"publish_max_retries must be 0-10, got {self.publish_max_retries}")
        if not (0 <= self.cifix_max_llm_attempts <= 10):
            errors.append(f"cifix_max_llm_attempts must be 0-10, got {self.cifix_max_llm_attempts}")

        # Token limits
        if self.claude_max_tokens < 1024:
            errors.append(f"claude_max_tokens must be >= 1024, got {self.claude_max_tokens}")

        # Rate limits
        if self.rate_limit_default < 1:
            errors.append(f"rate_limit_default must be >= 1, got {self.rate_limit_default}")
        if self.rate_limit_window_seconds < 1:
            errors.append(f"rate_limit_window_seconds must be >= 1, got {self.rate_limit_window_seconds}")

        # S3 backup validation
        if self.s3_backup_enabled:
            if not self.s3_backup_bucket:
                errors.append("S3_BACKUP_BUCKET must be set when s3_backup_enabled is True")
            if self.s3_backup_schedule_hours < 1:
                errors.append(f"s3_backup_schedule_hours must be >= 1, got {self.s3_backup_schedule_hours}")
            if self.s3_backup_retention_days < 0:
                errors.append(f"s3_backup_retention_days must be >= 0, got {self.s3_backup_retention_days}")

        return errors


_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment, caching the result."""
    global _settings
    if _settings is None:
        load_dotenv()
        _settings = Settings(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            bluesky_handle=os.getenv("BLUESKY_HANDLE", ""),
            bluesky_app_password=os.getenv("BLUESKY_APP_PASSWORD", ""),
            newsapi_key=os.getenv("NEWSAPI_KEY"),
            twitter_api_key=os.getenv("TWITTER_API_KEY", ""),
            twitter_api_secret=os.getenv("TWITTER_API_SECRET", ""),
            twitter_access_token=os.getenv("TWITTER_ACCESS_TOKEN", ""),
            twitter_access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET", ""),
            linkedin_access_token=os.getenv("LINKEDIN_ACCESS_TOKEN", ""),
            linkedin_person_urn=os.getenv("LINKEDIN_PERSON_URN", ""),
            reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
            reddit_username=os.getenv("REDDIT_USERNAME", ""),
            reddit_password=os.getenv("REDDIT_PASSWORD", ""),
            autonomous_mode=os.getenv("AUTONOMOUS_MODE", "true").lower() == "true",
            claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            claude_max_tokens=int(os.getenv("CLAUDE_MAX_TOKENS", "4096")),
            thinking_budget_ceo=int(os.getenv("THINKING_BUDGET_CEO", "10000")),
            thinking_budget_strategist=int(os.getenv("THINKING_BUDGET_STRATEGIST", "8000")),
            thinking_budget_creator=int(os.getenv("THINKING_BUDGET_CREATOR", "6000")),
            thinking_budget_legal=int(os.getenv("THINKING_BUDGET_LEGAL", "10000")),
            thinking_budget_security=int(os.getenv("THINKING_BUDGET_SECURITY", "8000")),
            use_bedrock=os.getenv("USE_BEDROCK", "false").lower() == "true",
            bedrock_region=os.getenv("BEDROCK_REGION", "us-west-2"),
            database_url=os.getenv("DATABASE_URL", ""),
            db_path=Path(os.getenv("DB_PATH", "data/ortobahn.db")),
            db_pool_min=int(os.getenv("DB_POOL_MIN", "2")),
            db_pool_max=int(os.getenv("DB_POOL_MAX", "10")),
            post_confidence_threshold=float(os.getenv("POST_CONFIDENCE_THRESHOLD", "0.7")),
            pipeline_interval_hours=int(os.getenv("PIPELINE_INTERVAL_HOURS", "8")),
            max_posts_per_cycle=int(os.getenv("MAX_POSTS_PER_CYCLE", "4")),
            default_client_id=os.getenv("DEFAULT_CLIENT_ID", DEFAULT_CLIENT_ID),
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("WEB_PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            default_monthly_budget=float(os.getenv("DEFAULT_MONTHLY_BUDGET", "0.0")),
            post_delay_seconds=int(os.getenv("POST_DELAY_SECONDS", "30")),
            rate_limit_enabled=os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true",
            rate_limit_default=int(os.getenv("RATE_LIMIT_DEFAULT", "60")),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
            slack_signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
            backup_enabled=os.getenv("BACKUP_ENABLED", "true").lower() == "true",
            backup_dir=Path(os.getenv("BACKUP_DIR", "data/backups")),
            backup_max_count=int(os.getenv("BACKUP_MAX_COUNT", "10")),
            s3_backup_enabled=os.getenv("S3_BACKUP_ENABLED", "false").lower() == "true",
            s3_backup_bucket=os.getenv("S3_BACKUP_BUCKET", ""),
            s3_backup_prefix=os.getenv("S3_BACKUP_PREFIX", "backups/"),
            s3_backup_schedule_hours=int(os.getenv("S3_BACKUP_SCHEDULE_HOURS", "24")),
            s3_backup_retention_days=int(os.getenv("S3_BACKUP_RETENTION_DAYS", "30")),
            s3_region=os.getenv("S3_REGION", "us-west-2"),
            secret_key=os.getenv("SECRET_KEY", ""),
            admin_api_key=os.getenv("ADMIN_API_KEY", ""),
            cognito_user_pool_id=os.getenv("COGNITO_USER_POOL_ID", ""),
            cognito_client_id=os.getenv("COGNITO_CLIENT_ID", ""),
            cognito_region=os.getenv("COGNITO_REGION", "us-west-2"),
            thinking_budget_reflection=int(os.getenv("THINKING_BUDGET_REFLECTION", "8000")),
            enable_self_critique=os.getenv("ENABLE_SELF_CRITIQUE", "true").lower() == "true",
            memory_max_per_agent=int(os.getenv("MEMORY_MAX_PER_AGENT", "100")),
            memory_prune_days=int(os.getenv("MEMORY_PRUNE_DAYS", "90")),
            ab_testing_enabled=os.getenv("AB_TESTING_ENABLED", "true").lower() == "true",
            min_ab_pairs=int(os.getenv("MIN_AB_PAIRS", "5")),
            creator_critique_threshold=float(os.getenv("CREATOR_CRITIQUE_THRESHOLD", "0.8")),
            preflight_enabled=os.getenv("PREFLIGHT_ENABLED", "true").lower() == "true",
            cifix_enabled=os.getenv("CIFIX_ENABLED", "true").lower() == "true",
            cifix_auto_pr=os.getenv("CIFIX_AUTO_PR", "true").lower() == "true",
            cifix_max_llm_attempts=int(os.getenv("CIFIX_MAX_LLM_ATTEMPTS", "2")),
            cto_enabled=os.getenv("CTO_ENABLED", "true").lower() == "true",
            cto_max_tasks_per_cycle=int(os.getenv("CTO_MAX_TASKS_PER_CYCLE", "1")),
            thinking_budget_cto=int(os.getenv("THINKING_BUDGET_CTO", "16000")),
            ortobahn_bluesky_handle=os.getenv("ORTOBAHN_BLUESKY_HANDLE", ""),
            ortobahn_bluesky_app_password=os.getenv("ORTOBAHN_BLUESKY_APP_PASSWORD", ""),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
            stripe_publishable_key=os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            stripe_price_id=os.getenv("STRIPE_PRICE_ID", ""),
            watchdog_enabled=os.getenv("WATCHDOG_ENABLED", "true").lower() == "true",
            watchdog_stale_run_minutes=int(os.getenv("WATCHDOG_STALE_RUN_MINUTES", "60")),
            watchdog_post_verify_hours=int(os.getenv("WATCHDOG_POST_VERIFY_HOURS", "6")),
            watchdog_credential_check=os.getenv("WATCHDOG_CREDENTIAL_CHECK", "true").lower() == "true",
            watchdog_max_verify_posts=int(os.getenv("WATCHDOG_MAX_VERIFY_POSTS", "5")),
            auto_rollback_enabled=os.getenv("AUTO_ROLLBACK_ENABLED", "true").lower() == "true",
            auto_rollback_window_minutes=int(os.getenv("AUTO_ROLLBACK_WINDOW_MINUTES", "30")),
            auto_rollback_health_failures=int(os.getenv("AUTO_ROLLBACK_HEALTH_FAILURES", "3")),
            engagement_enabled=os.getenv("ENGAGEMENT_ENABLED", "true").lower() == "true",
            engagement_max_replies=int(os.getenv("ENGAGEMENT_MAX_REPLIES", "3")),
            engagement_confidence_threshold=float(os.getenv("ENGAGEMENT_CONFIDENCE_THRESHOLD", "0.75")),
            style_evolution_enabled=os.getenv("STYLE_EVOLUTION_ENABLED", "true").lower() == "true",
            predictive_timing_enabled=os.getenv("PREDICTIVE_TIMING_ENABLED", "true").lower() == "true",
            serialization_enabled=os.getenv("SERIALIZATION_ENABLED", "true").lower() == "true",
            post_feedback_enabled=os.getenv("POST_FEEDBACK_ENABLED", "true").lower() == "true",
            post_feedback_delay_seconds=int(os.getenv("POST_FEEDBACK_DELAY_SECONDS", "600")),
            meta_learning_enabled=os.getenv("META_LEARNING_ENABLED", "true").lower() == "true",
            publish_retry_enabled=os.getenv("PUBLISH_RETRY_ENABLED", "true").lower() == "true",
            publish_max_retries=int(os.getenv("PUBLISH_MAX_RETRIES", "2")),
            dynamic_cadence_enabled=os.getenv("DYNAMIC_CADENCE_ENABLED", "true").lower() == "true",
            thinking_budget_article_writer=int(os.getenv("THINKING_BUDGET_ARTICLE_WRITER", "16000")),
            article_confidence_threshold=float(os.getenv("ARTICLE_CONFIDENCE_THRESHOLD", "0.8")),
            ses_region=os.getenv("SES_REGION", "us-west-2"),
            ses_sender_email=os.getenv("SES_SENDER_EMAIL", ""),
            digest_enabled_global=os.getenv("DIGEST_ENABLED_GLOBAL", "true").lower() == "true",
        )
    return _settings


def get_settings() -> Settings:
    """Get cached settings."""
    if _settings is None:
        return load_settings()
    return _settings


def reset_settings() -> None:
    """Reset cached settings (used in tests)."""
    global _settings
    _settings = None
