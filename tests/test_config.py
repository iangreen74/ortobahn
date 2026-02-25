"""Tests for configuration loading and validation."""

from ortobahn.config import Settings, load_settings


class TestSettingsValidate:
    def test_valid_settings(self, test_settings):
        errors = test_settings.validate()
        assert errors == []

    def test_missing_anthropic_key(self):
        s = Settings(anthropic_api_key="", bluesky_handle="x.bsky.social", bluesky_app_password="pass")
        errors = s.validate()
        assert any("ANTHROPIC_API_KEY" in e for e in errors)

    def test_bad_anthropic_key_format(self):
        s = Settings(anthropic_api_key="bad-key", bluesky_handle="x.bsky.social", bluesky_app_password="pass")
        errors = s.validate()
        assert any("sk-ant-" in e for e in errors)

    def test_missing_bluesky_handle(self):
        s = Settings(anthropic_api_key="sk-ant-test", bluesky_handle="", bluesky_app_password="pass")
        errors = s.validate(require_bluesky=True)
        assert any("BLUESKY_HANDLE" in e for e in errors)

    def test_skip_bluesky_validation(self):
        s = Settings(anthropic_api_key="sk-ant-test", bluesky_handle="", bluesky_app_password="")
        errors = s.validate(require_bluesky=False)
        assert errors == []

    def test_bad_confidence_threshold(self):
        s = Settings(
            anthropic_api_key="sk-ant-test",
            bluesky_handle="x.bsky.social",
            bluesky_app_password="pass",
            post_confidence_threshold=1.5,
        )
        errors = s.validate()
        assert any("POST_CONFIDENCE_THRESHOLD" in e for e in errors)

    def test_bad_interval(self):
        s = Settings(
            anthropic_api_key="sk-ant-test",
            bluesky_handle="x.bsky.social",
            bluesky_app_password="pass",
            pipeline_interval_hours=0,
        )
        errors = s.validate()
        assert any("PIPELINE_INTERVAL_HOURS" in e for e in errors)


class TestLoadSettings:
    def test_load_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
        monkeypatch.setenv("BLUESKY_HANDLE", "env.bsky.social")
        monkeypatch.setenv("BLUESKY_APP_PASSWORD", "env-pass")
        s = load_settings()
        assert s.anthropic_api_key == "sk-ant-from-env"
        assert s.bluesky_handle == "env.bsky.social"

    def test_defaults_without_env(self, monkeypatch, tmp_path):
        # Set to empty string rather than deleting — prevents load_dotenv()
        # from re-populating the key from the project's .env file.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("BLUESKY_HANDLE", "")
        monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
        monkeypatch.chdir(tmp_path)
        s = load_settings()
        assert s.anthropic_api_key == ""
        assert s.claude_model == "claude-sonnet-4-5-20250929"


class TestPlatformHelpers:
    def test_has_twitter_false_by_default(self):
        s = Settings(anthropic_api_key="sk-ant-test")
        assert s.has_twitter() is False

    def test_has_twitter_true_when_all_set(self):
        s = Settings(
            anthropic_api_key="sk-ant-test",
            twitter_api_key="key",
            twitter_api_secret="secret",
            twitter_access_token="token",
            twitter_access_token_secret="token_secret",
        )
        assert s.has_twitter() is True

    def test_has_twitter_false_when_partial(self):
        s = Settings(
            anthropic_api_key="sk-ant-test",
            twitter_api_key="key",
            twitter_api_secret="",
        )
        assert s.has_twitter() is False

    def test_has_linkedin_false_by_default(self):
        s = Settings(anthropic_api_key="sk-ant-test")
        assert s.has_linkedin() is False

    def test_has_linkedin_true_when_set(self):
        s = Settings(
            anthropic_api_key="sk-ant-test",
            linkedin_access_token="token",
            linkedin_person_urn="urn:li:person:abc",
        )
        assert s.has_linkedin() is True

    def test_autonomous_mode_default_true(self):
        s = Settings(anthropic_api_key="sk-ant-test")
        assert s.autonomous_mode is True

    def test_autonomous_mode_env_override(self, monkeypatch):
        monkeypatch.setenv("AUTONOMOUS_MODE", "false")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        s = load_settings()
        assert s.autonomous_mode is False


class TestValidationBounds:
    def test_thinking_budget_too_low(self):
        s = Settings(anthropic_api_key="sk-ant-test", thinking_budget_ceo=500)
        errors = s.validate()
        assert any("thinking_budget_ceo" in e for e in errors)

    def test_thinking_budget_too_high(self):
        s = Settings(anthropic_api_key="sk-ant-test", thinking_budget_ceo=200_000)
        errors = s.validate()
        assert any("thinking_budget_ceo" in e for e in errors)

    def test_thinking_budget_valid(self):
        s = Settings(anthropic_api_key="sk-ant-test", thinking_budget_ceo=10_000)
        errors = s.validate()
        assert not any("thinking_budget_ceo" in e for e in errors)

    def test_pool_min_too_low(self):
        s = Settings(anthropic_api_key="sk-ant-test", db_pool_min=0)
        errors = s.validate()
        assert any("db_pool_min" in e for e in errors)

    def test_pool_max_less_than_min(self):
        s = Settings(anthropic_api_key="sk-ant-test", db_pool_min=5, db_pool_max=3)
        errors = s.validate()
        assert any("db_pool_max" in e for e in errors)

    def test_retry_count_out_of_range(self):
        s = Settings(anthropic_api_key="sk-ant-test", publish_max_retries=15)
        errors = s.validate()
        assert any("publish_max_retries" in e for e in errors)

    def test_token_limit_too_low(self):
        s = Settings(anthropic_api_key="sk-ant-test", claude_max_tokens=100)
        errors = s.validate()
        assert any("claude_max_tokens" in e for e in errors)

    def test_rate_limit_too_low(self):
        s = Settings(anthropic_api_key="sk-ant-test", rate_limit_default=0)
        errors = s.validate()
        assert any("rate_limit_default" in e for e in errors)

    def test_negative_budget(self):
        s = Settings(anthropic_api_key="sk-ant-test", default_monthly_budget=-10)
        errors = s.validate()
        assert any("default_monthly_budget" in e for e in errors)

    def test_threshold_out_of_range(self):
        s = Settings(anthropic_api_key="sk-ant-test", engagement_confidence_threshold=1.5)
        errors = s.validate()
        assert any("engagement_confidence_threshold" in e for e in errors)

    def test_all_defaults_valid(self):
        s = Settings(anthropic_api_key="sk-ant-test")
        errors = s.validate()
        assert errors == []
