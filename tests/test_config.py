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
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
        monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
        # Prevent .env file from being loaded by changing cwd
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
