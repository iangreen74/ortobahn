"""Tests for Pydantic data models."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from ortobahn.models import (
    PLATFORM_CONSTRAINTS,
    AnalyticsReport,
    Client,
    ContentStatus,
    ContentType,
    DraftPost,
    Platform,
    PostIdea,
    PostType,
    Strategy,
)


class TestStrategy:
    def test_valid_strategy(self):
        s = Strategy(
            themes=["AI", "tech"],
            tone="bold",
            goals=["grow"],
            content_guidelines="be real",
            posting_frequency="3x/day",
            valid_until=datetime.utcnow() + timedelta(days=7),
        )
        assert len(s.themes) == 2

    def test_themes_min_length(self):
        with pytest.raises(ValidationError):
            Strategy(
                themes=[],
                tone="bold",
                goals=["grow"],
                content_guidelines="ok",
                posting_frequency="daily",
                valid_until=datetime.utcnow(),
            )

    def test_round_trip_json(self):
        s = Strategy(
            themes=["AI"],
            tone="bold",
            goals=["grow"],
            content_guidelines="ok",
            posting_frequency="daily",
            valid_until=datetime(2026, 3, 1),
        )
        data = s.model_dump_json()
        restored = Strategy.model_validate_json(data)
        assert restored.themes == s.themes


class TestDraftPost:
    def test_valid_draft(self):
        d = DraftPost(text="Hello world", source_idea="test", reasoning="test", confidence=0.9)
        assert d.confidence == 0.9

    def test_long_text_allowed(self):
        """DraftPost accepts long text - truncation happens in the Creator agent."""
        d = DraftPost(text="x" * 310, source_idea="test", reasoning="test", confidence=0.5)
        assert len(d.text) == 310

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            DraftPost(text="ok", source_idea="test", reasoning="test", confidence=1.5)
        with pytest.raises(ValidationError):
            DraftPost(text="ok", source_idea="test", reasoning="test", confidence=-0.1)


class TestPostIdea:
    def test_valid_idea(self):
        idea = PostIdea(
            topic="AI",
            angle="contrarian",
            hook="What if...",
            content_type=PostType.HOT_TAKE,
            priority=1,
        )
        assert idea.content_type == PostType.HOT_TAKE

    def test_priority_range(self):
        with pytest.raises(ValidationError):
            PostIdea(
                topic="AI",
                angle="x",
                hook="x",
                content_type=PostType.INSIGHT,
                priority=0,
            )
        with pytest.raises(ValidationError):
            PostIdea(
                topic="AI",
                angle="x",
                hook="x",
                content_type=PostType.INSIGHT,
                priority=6,
            )


class TestAnalyticsReport:
    def test_defaults(self):
        r = AnalyticsReport()
        assert r.total_posts == 0
        assert r.summary == "No data yet."
        assert r.recommendations == []

    def test_post_type_enum_values(self):
        assert PostType.HOT_TAKE == "hot_take"
        assert PostType.INSIGHT == "insight"
        assert PostType.QUESTION == "question"
        assert PostType.THREAD_STARTER == "thread_starter"
        assert PostType.COMMENTARY == "commentary"


class TestPlatform:
    def test_all_platforms(self):
        assert Platform.BLUESKY == "bluesky"
        assert Platform.TWITTER == "twitter"
        assert Platform.LINKEDIN == "linkedin"
        assert Platform.GOOGLE_ADS == "google_ads"
        assert Platform.INSTAGRAM == "instagram"
        assert Platform.GENERIC == "generic"

    def test_constraints_cover_all_platforms(self):
        for p in Platform:
            assert p in PLATFORM_CONSTRAINTS, f"Missing constraints for {p}"
            assert "max_chars" in PLATFORM_CONSTRAINTS[p]
            assert "hashtags" in PLATFORM_CONSTRAINTS[p]
            assert "tone" in PLATFORM_CONSTRAINTS[p]


class TestContentType:
    def test_values(self):
        assert ContentType.SOCIAL_POST == "social_post"
        assert ContentType.AD_HEADLINE == "ad_headline"
        assert ContentType.AD_DESCRIPTION == "ad_description"


class TestContentStatus:
    def test_values(self):
        assert ContentStatus.DRAFT == "draft"
        assert ContentStatus.APPROVED == "approved"
        assert ContentStatus.PUBLISHED == "published"
        assert ContentStatus.REJECTED == "rejected"
        assert ContentStatus.FAILED == "failed"
        assert ContentStatus.SKIPPED == "skipped"


class TestClient:
    def test_valid_client(self):
        c = Client(id="test", name="Test Corp")
        assert c.active is True
        assert c.description == ""

    def test_full_client(self):
        c = Client(
            id="vs",
            name="Vaultscaler",
            description="Autonomous engineering",
            industry="AI",
            target_audience="CTOs",
            brand_voice="authoritative",
            website="https://vaultscaler.com",
        )
        assert c.name == "Vaultscaler"


class TestModelDefaults:
    """Verify new fields on existing models have backward-compatible defaults."""

    def test_strategy_defaults(self):
        s = Strategy(
            themes=["AI"],
            tone="bold",
            goals=["grow"],
            content_guidelines="ok",
            posting_frequency="daily",
            valid_until=datetime.utcnow() + timedelta(days=7),
        )
        assert s.target_platforms == [Platform.GENERIC]
        assert s.client_id == "default"

    def test_draft_post_defaults(self):
        d = DraftPost(text="test", source_idea="test", reasoning="test", confidence=0.8)
        assert d.platform == Platform.GENERIC
        assert d.content_type == ContentType.SOCIAL_POST

    def test_post_idea_defaults(self):
        idea = PostIdea(topic="AI", angle="x", hook="x", content_type=PostType.INSIGHT, priority=1)
        assert idea.target_platforms == [Platform.GENERIC]
