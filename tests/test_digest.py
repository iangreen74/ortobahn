"""Tests for Weekly Digest Email."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ortobahn.digest import WeeklyDigest, _esc


@pytest.fixture()
def _seed_client(test_db):
    test_db.create_client(
        {
            "id": "digest-test",
            "name": "Digest Test Co",
            "industry": "tech",
            "target_audience": "developers",
            "brand_voice": "professional",
        }
    )
    test_db.execute(
        "UPDATE clients SET digest_enabled=1, digest_email='team@example.com',"
        " digest_day=0, digest_hour=9 WHERE id='digest-test'",
        commit=True,
    )


@pytest.fixture()
def _seed_posts(test_db, _seed_client):
    """Create some published posts with metrics for digest testing."""
    for i in range(5):
        pid = test_db.save_post(
            text=f"Digest test post {i}",
            run_id=f"digest-run-{i}",
            status="published",
            confidence=0.8,
            client_id="digest-test",
            platform="bluesky",
        )
        test_db.execute(
            "UPDATE posts SET published_at=CURRENT_TIMESTAMP WHERE id=?",
            (pid,),
            commit=True,
        )
        test_db.execute(
            "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at)"
            " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (f"m-digest-{i}", pid, 10 + i * 5, 3 + i, 2),
            commit=True,
        )
    return pid  # return last post id


class TestGenerateDigest:
    def test_generates_with_data(self, test_db, _seed_posts):
        digest = WeeklyDigest(test_db)
        data = digest.generate_digest("digest-test")
        assert data["posts_published"] == 5
        assert data["total_engagement"] > 0
        assert data["avg_engagement"] > 0
        assert data["top_post"] is not None
        assert data["period_start"] is not None
        assert data["period_end"] is not None

    def test_generates_empty_for_no_posts(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        data = digest.generate_digest("digest-test")
        assert data["posts_published"] == 0
        assert data["total_engagement"] == 0
        assert data["top_post"] is None

    def test_platform_breakdown(self, test_db, _seed_posts):
        digest = WeeklyDigest(test_db)
        data = digest.generate_digest("digest-test")
        platforms = data["platform_breakdown"]
        assert len(platforms) >= 1
        assert platforms[0]["platform"] == "bluesky"
        assert platforms[0]["count"] == 5


class TestRenderEmail:
    def test_renders_html(self, test_db, _seed_posts):
        digest = WeeklyDigest(test_db)
        data = digest.generate_digest("digest-test")
        html = digest.render_email("Digest Test Co", data)
        assert "Weekly Performance Digest" in html
        assert "Digest Test Co" in html
        assert "Posts Published" in html
        assert "Total Engagement" in html

    def test_renders_empty_state(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        data = digest.generate_digest("digest-test")
        html = digest.render_email("Digest Test Co", data)
        assert "0" in html  # 0 posts
        assert "Weekly Performance Digest" in html

    def test_escapes_html_in_content(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        data = {
            "posts_published": 1,
            "total_engagement": 10,
            "avg_engagement": 10.0,
            "top_post": {"id": "x", "text": "<script>xss</script>", "platform": "bluesky",
                         "like_count": 5, "repost_count": 3, "reply_count": 2},
            "engagement_change_pct": 50,
            "platform_breakdown": [],
            "period_start": "2025-01-01",
            "period_end": "2025-01-07",
        }
        html = digest.render_email("Test", data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestSendDigest:
    def test_send_success(self, test_db, _seed_posts):
        digest = WeeklyDigest(test_db)
        mock_ses = MagicMock()
        mock_ses.send_html_email.return_value = "msg-123"

        result = digest.send_digest("digest-test", "Digest Test Co", "team@example.com", mock_ses)
        assert result is True
        mock_ses.send_html_email.assert_called_once()

        # Check digest_history record
        row = test_db.fetchone(
            "SELECT status, posts_published FROM digest_history WHERE client_id='digest-test'"
        )
        assert row is not None
        assert row["status"] == "sent"
        assert row["posts_published"] == 5

    def test_send_failure(self, test_db, _seed_posts):
        digest = WeeklyDigest(test_db)
        mock_ses = MagicMock()
        mock_ses.send_html_email.return_value = None  # SES failure

        result = digest.send_digest("digest-test", "Digest Test Co", "team@example.com", mock_ses)
        assert result is False

        row = test_db.fetchone(
            "SELECT status, error FROM digest_history WHERE client_id='digest-test'"
        )
        assert row["status"] == "failed"
        assert row["error"] == "SES send failed"


class TestGetClientsDueForDigest:
    def test_finds_due_client(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        # Use a Monday at 9 AM UTC
        now = datetime(2025, 1, 6, 9, 0, 0, tzinfo=timezone.utc)  # Monday
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 1
        assert clients[0]["id"] == "digest-test"

    def test_skips_wrong_day(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        # Tuesday at 9 AM (client is set to day=0 = Monday)
        now = datetime(2025, 1, 7, 9, 0, 0, tzinfo=timezone.utc)  # Tuesday
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 0

    def test_skips_wrong_hour(self, test_db, _seed_client):
        digest = WeeklyDigest(test_db)
        # Monday at 3 PM (client is set to hour=9)
        now = datetime(2025, 1, 6, 15, 0, 0, tzinfo=timezone.utc)
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 0

    def test_skips_disabled_digest(self, test_db, _seed_client):
        test_db.execute("UPDATE clients SET digest_enabled=0 WHERE id='digest-test'", commit=True)
        digest = WeeklyDigest(test_db)
        now = datetime(2025, 1, 6, 9, 0, 0, tzinfo=timezone.utc)
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 0

    def test_skips_no_email(self, test_db, _seed_client):
        test_db.execute("UPDATE clients SET digest_email='' WHERE id='digest-test'", commit=True)
        digest = WeeklyDigest(test_db)
        now = datetime(2025, 1, 6, 9, 0, 0, tzinfo=timezone.utc)
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 0

    def test_skips_recently_sent(self, test_db, _seed_client):
        """Don't send if a digest was sent in the last 23 hours."""
        import uuid

        test_db.execute(
            "INSERT INTO digest_history (id, client_id, sent_at, period_start, period_end,"
            " posts_published, total_engagement, status)"
            " VALUES (?, 'digest-test', CURRENT_TIMESTAMP, '2025-01-01', '2025-01-07', 5, 100, 'sent')",
            (str(uuid.uuid4()),),
            commit=True,
        )
        digest = WeeklyDigest(test_db)
        now = datetime(2025, 1, 6, 9, 0, 0, tzinfo=timezone.utc)
        clients = digest.get_clients_due_for_digest(now)
        assert len(clients) == 0


class TestEscape:
    def test_escapes_special_chars(self):
        assert _esc("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"
        assert _esc('"quotes"') == "&quot;quotes&quot;"
        assert _esc("a & b") == "a &amp; b"
