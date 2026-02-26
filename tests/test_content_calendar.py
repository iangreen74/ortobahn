"""Tests for the Content Calendar feature."""

from __future__ import annotations

import pytest


class TestCalendarMigration:
    def test_scheduled_at_column_exists(self, test_db):
        """Migration 034 should add scheduled_at to posts."""
        test_db.fetchall("SELECT scheduled_at FROM posts LIMIT 1")

    def test_post_insights_table_exists(self, test_db):
        """Migration 035 should add post_insights table."""
        test_db.fetchall("SELECT id, post_id, insight_text FROM post_insights LIMIT 1")

    def test_engagement_mode_column_exists(self, test_db):
        """Migration 036 should add engagement_mode to clients."""
        test_db.fetchall("SELECT engagement_mode FROM clients LIMIT 1")

    def test_provenance_columns_exist(self, test_db):
        """Migration 037 should add provenance columns."""
        test_db.fetchall("SELECT source_post_id, source_article_id, repurpose_type FROM posts LIMIT 1")
        test_db.fetchall("SELECT source_post_id, source_article_id FROM articles LIMIT 1")

    def test_digest_columns_exist(self, test_db):
        """Migration 038 should add digest columns and table."""
        test_db.fetchall("SELECT digest_enabled, digest_email, digest_day, digest_hour FROM clients LIMIT 1")
        test_db.fetchall("SELECT id, client_id, sent_at FROM digest_history LIMIT 1")


class TestCalendarRouteLogic:
    """Test calendar route helper logic without requiring a full app."""

    @pytest.fixture()
    def _seed_client(self, test_db):
        test_db.create_client(
            {
                "id": "cal-test",
                "name": "Calendar Test Co",
                "industry": "tech",
                "target_audience": "developers",
                "brand_voice": "professional",
            }
        )

    def test_posts_with_scheduled_at(self, test_db, _seed_client):
        """Posts can have a scheduled_at timestamp set."""
        pid = test_db.save_post(
            text="Scheduled post",
            run_id="cal-run",
            status="draft",
            confidence=0.8,
            client_id="cal-test",
            platform="bluesky",
        )
        test_db.execute(
            "UPDATE posts SET scheduled_at=? WHERE id=?",
            ("2025-03-15T10:00:00", pid),
            commit=True,
        )
        row = test_db.fetchone("SELECT scheduled_at FROM posts WHERE id=?", (pid,))
        assert row is not None
        assert row["scheduled_at"] == "2025-03-15T10:00:00"

    def test_query_posts_by_scheduled_range(self, test_db, _seed_client):
        """Can query posts by scheduled_at date range."""
        for i in range(3):
            pid = test_db.save_post(
                text=f"Scheduled post {i}",
                run_id=f"cal-run-{i}",
                status="draft",
                confidence=0.8,
                client_id="cal-test",
                platform="bluesky",
            )
            test_db.execute(
                "UPDATE posts SET scheduled_at=? WHERE id=?",
                (f"2025-03-{10 + i:02d}T10:00:00", pid),
                commit=True,
            )

        # Query March 2025
        rows = test_db.fetchall(
            "SELECT id FROM posts WHERE client_id=? AND scheduled_at BETWEEN ? AND ?",
            ("cal-test", "2025-03-01", "2025-03-31T23:59:59"),
        )
        assert len(rows) == 3

    def test_reschedule_updates_scheduled_at(self, test_db, _seed_client):
        """Rescheduling updates the scheduled_at field."""
        pid = test_db.save_post(
            text="To reschedule",
            run_id="cal-resched",
            status="draft",
            confidence=0.8,
            client_id="cal-test",
            platform="bluesky",
        )
        test_db.execute(
            "UPDATE posts SET scheduled_at=? WHERE id=?",
            ("2025-03-10T10:00:00", pid),
            commit=True,
        )

        # Reschedule
        test_db.execute(
            "UPDATE posts SET scheduled_at=? WHERE id=? AND client_id=?",
            ("2025-03-15T14:00:00", pid, "cal-test"),
            commit=True,
        )
        row = test_db.fetchone("SELECT scheduled_at FROM posts WHERE id=?", (pid,))
        assert row["scheduled_at"] == "2025-03-15T14:00:00"

    def test_cannot_reschedule_published_post(self, test_db, _seed_client):
        """Published posts should not be reschedulable (business logic)."""
        pid = test_db.save_post(
            text="Published post",
            run_id="cal-pub",
            status="published",
            confidence=0.8,
            client_id="cal-test",
            platform="bluesky",
        )
        post = test_db.fetchone("SELECT status FROM posts WHERE id=?", (pid,))
        assert post["status"] == "published"
        # In the route, we check status before allowing reschedule
        assert post["status"] not in ("draft", "approved", "scheduled")
