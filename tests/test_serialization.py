"""Tests for content serialization (multi-part narrative arcs)."""

from __future__ import annotations

import pytest

from ortobahn.serialization import SeriesManager


@pytest.fixture
def series_mgr(test_db):
    return SeriesManager(db=test_db)


class TestCreateSeries:
    def test_create_series_returns_id(self, series_mgr):
        series_id = series_mgr.create_series("default", "Day N of AI", max_parts=30)
        assert isinstance(series_id, str)
        assert len(series_id) == 8

    def test_create_series_inserts_row(self, series_mgr, test_db):
        series_id = series_mgr.create_series(
            "default", "Building in Public", description="Weekly updates", max_parts=12
        )
        row = test_db.fetchone("SELECT * FROM content_series WHERE id=?", (series_id,))
        assert row is not None
        assert row["client_id"] == "default"
        assert row["series_title"] == "Building in Public"
        assert row["series_description"] == "Weekly updates"
        assert row["current_part"] == 0
        assert row["max_parts"] == 12
        assert row["status"] == "active"
        assert row["created_at"] is not None
        assert row["updated_at"] is not None


class TestGetActiveSeries:
    def test_returns_active_series_for_client(self, series_mgr):
        series_mgr.create_series("default", "Series A")
        series_mgr.create_series("default", "Series B")

        active = series_mgr.get_active_series("default")
        assert len(active) == 2
        titles = {s["series_title"] for s in active}
        assert "Series A" in titles
        assert "Series B" in titles

    def test_excludes_completed_series(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Done Series", max_parts=1)
        post_id = test_db.save_post(text="Final part", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, post_id)  # completes at max_parts=1

        active = series_mgr.get_active_series("default")
        assert len(active) == 0

    def test_excludes_paused_series(self, series_mgr):
        sid = series_mgr.create_series("default", "Paused Series")
        series_mgr.pause_series(sid)

        active = series_mgr.get_active_series("default")
        assert len(active) == 0

    def test_scoped_by_client(self, series_mgr, test_db):
        test_db.create_client({"id": "other", "name": "Other Corp"})
        series_mgr.create_series("default", "Default Series")
        series_mgr.create_series("other", "Other Series")

        default_active = series_mgr.get_active_series("default")
        other_active = series_mgr.get_active_series("other")
        assert len(default_active) == 1
        assert default_active[0]["series_title"] == "Default Series"
        assert len(other_active) == 1
        assert other_active[0]["series_title"] == "Other Series"


class TestGetSeries:
    def test_returns_series_by_id(self, series_mgr):
        sid = series_mgr.create_series("default", "Lookup Series", description="desc", max_parts=5)
        result = series_mgr.get_series(sid)
        assert result is not None
        assert result["id"] == sid
        assert result["series_title"] == "Lookup Series"
        assert result["series_description"] == "desc"
        assert result["max_parts"] == 5

    def test_returns_none_when_not_found(self, series_mgr):
        result = series_mgr.get_series("nonexistent")
        assert result is None


class TestGetSeriesPosts:
    def test_returns_posts_linked_to_series(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Post Series", max_parts=5)
        p1 = test_db.save_post(text="Part 1 content", run_id="run-1", client_id="default")
        p2 = test_db.save_post(text="Part 2 content", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, p1)
        series_mgr.advance_series(sid, p2)

        posts = series_mgr.get_series_posts(sid)
        assert len(posts) == 2
        assert posts[0]["series_part"] == 1
        assert posts[0]["text"] == "Part 1 content"
        assert posts[1]["series_part"] == 2
        assert posts[1]["text"] == "Part 2 content"

    def test_returns_empty_list_when_no_posts(self, series_mgr):
        sid = series_mgr.create_series("default", "Empty Series")
        posts = series_mgr.get_series_posts(sid)
        assert posts == []


class TestGetSeriesContext:
    def test_returns_formatted_context_string(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "AI Journey", description="Documenting AI progress", max_parts=10)
        p1 = test_db.save_post(text="Day 1: Started the journey", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, p1)

        context = series_mgr.get_series_context("default")
        assert "## Active Content Series" in context
        assert "### Series: AI Journey" in context
        assert "Description: Documenting AI progress" in context
        assert "Current part: 1" in context
        assert "Max parts: 10" in context
        assert "Recent installments:" in context
        assert "Part 1:" in context
        assert "Day 1: Started the journey" in context

    def test_returns_empty_string_when_no_active_series(self, series_mgr):
        context = series_mgr.get_series_context("default")
        assert context == ""

    def test_returns_empty_string_when_all_series_completed(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Short Series", max_parts=1)
        p1 = test_db.save_post(text="Only part", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, p1)

        context = series_mgr.get_series_context("default")
        assert context == ""


class TestAdvanceSeries:
    def test_increments_current_part(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Test Series", max_parts=5)
        p1 = test_db.save_post(text="Part 1 content", run_id="run-1", client_id="default")
        part_num = series_mgr.advance_series(sid, p1)

        assert part_num == 1

        series = series_mgr.get_series(sid)
        assert series["current_part"] == 1

    def test_updates_post_with_series_info(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Test Series", max_parts=5)
        p1 = test_db.save_post(text="Part 1 content", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, p1)

        post = test_db.get_post(p1)
        assert post["series_id"] == sid
        assert post["series_part"] == 1

    def test_sequential_advances(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Test Series", max_parts=5)
        p1 = test_db.save_post(text="Part 1", run_id="run-1", client_id="default")
        p2 = test_db.save_post(text="Part 2", run_id="run-1", client_id="default")
        p3 = test_db.save_post(text="Part 3", run_id="run-1", client_id="default")

        assert series_mgr.advance_series(sid, p1) == 1
        assert series_mgr.advance_series(sid, p2) == 2
        assert series_mgr.advance_series(sid, p3) == 3

    def test_auto_completes_when_max_parts_reached(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Short Series", max_parts=3)
        for i in range(3):
            pid = test_db.save_post(text=f"Part {i + 1}", run_id="run-1", client_id="default")
            series_mgr.advance_series(sid, pid)

        series = series_mgr.get_series(sid)
        assert series["status"] == "completed"
        assert series["current_part"] == 3

    def test_raises_for_nonexistent_series(self, series_mgr, test_db):
        pid = test_db.save_post(text="Orphan post", run_id="run-1", client_id="default")
        with pytest.raises(ValueError, match="not found"):
            series_mgr.advance_series("nonexistent", pid)

    def test_raises_for_non_active_series(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Paused Series", max_parts=5)
        series_mgr.pause_series(sid)

        pid = test_db.save_post(text="Late post", run_id="run-1", client_id="default")
        with pytest.raises(ValueError, match="not active"):
            series_mgr.advance_series(sid, pid)


class TestPauseAndResume:
    def test_pause_updates_status(self, series_mgr):
        sid = series_mgr.create_series("default", "Pausable Series")
        series_mgr.pause_series(sid)

        series = series_mgr.get_series(sid)
        assert series["status"] == "paused"

    def test_resume_updates_status(self, series_mgr):
        sid = series_mgr.create_series("default", "Resumable Series")
        series_mgr.pause_series(sid)
        series_mgr.resume_series(sid)

        series = series_mgr.get_series(sid)
        assert series["status"] == "active"

    def test_resume_only_affects_paused_series(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Completed Series", max_parts=1)
        pid = test_db.save_post(text="Final", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, pid)

        # Series is now 'completed'; resume should not change it back to 'active'
        series_mgr.resume_series(sid)
        series = series_mgr.get_series(sid)
        assert series["status"] == "completed"


class TestSuggestNewSeries:
    def test_returns_true_when_no_active_series(self, series_mgr):
        assert series_mgr.suggest_new_series("default") is True

    def test_returns_true_when_all_series_have_10_or_more_parts(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Mature Series", max_parts=0)
        # Manually set current_part to 10 to avoid creating 10 posts
        test_db.execute(
            "UPDATE content_series SET current_part = 10 WHERE id = ?",
            (sid,),
            commit=True,
        )

        assert series_mgr.suggest_new_series("default") is True

    def test_returns_false_when_series_has_fewer_than_10_parts(self, series_mgr, test_db):
        sid = series_mgr.create_series("default", "Young Series", max_parts=0)
        pid = test_db.save_post(text="Part 1", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid, pid)

        assert series_mgr.suggest_new_series("default") is False

    def test_returns_false_when_any_series_under_10_parts(self, series_mgr, test_db):
        sid1 = series_mgr.create_series("default", "Mature Series", max_parts=0)
        test_db.execute(
            "UPDATE content_series SET current_part = 15 WHERE id = ?",
            (sid1,),
            commit=True,
        )

        sid2 = series_mgr.create_series("default", "Young Series", max_parts=0)
        pid = test_db.save_post(text="Part 1", run_id="run-1", client_id="default")
        series_mgr.advance_series(sid2, pid)

        assert series_mgr.suggest_new_series("default") is False
