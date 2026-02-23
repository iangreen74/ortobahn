"""Tests for prompt tuner performance insights."""

from __future__ import annotations

from ortobahn.prompt_tuner import get_performance_insights


class TestGetPerformanceInsights:
    def test_empty_db_returns_empty_string(self, test_db):
        """Should return empty string when no posts exist."""
        result = get_performance_insights(test_db)
        assert result == ""

    def test_posts_with_metrics_returns_formatted_string(self, test_db):
        """Should return a formatted markdown string with top performers."""
        # Create published posts with metrics
        for i in range(5):
            pid = test_db.save_post(
                text=f"Post about AI topic {i}",
                run_id="r1",
                status="published",
                platform="bluesky",
            )
            test_db.update_post_published(pid, f"at://post/{i}", f"bafy{i}")
            test_db.save_metrics(pid, like_count=(i + 1) * 5, repost_count=i, reply_count=1)

        result = get_performance_insights(test_db)

        assert result != ""
        assert "Performance Insights" in result
        assert "Top Performing Posts" in result
        assert "Analyzed" in result
        assert "5" in result  # Number of analyzed posts

    def test_top_posts_section_present(self, test_db):
        """Top performing posts section should list posts with engagement counts."""
        for i in range(3):
            pid = test_db.save_post(
                text=f"Content piece {i} with some details",
                run_id="r1",
                status="published",
            )
            test_db.update_post_published(pid, f"at://p/{i}", f"cid{i}")
            test_db.save_metrics(pid, like_count=10 * (i + 1), repost_count=i)

        result = get_performance_insights(test_db)

        assert "Top Performing Posts" in result
        assert "engagements" in result.lower()

    def test_fewer_than_10_posts_skips_bottom_section(self, test_db):
        """When fewer than 10 posts exist, bottom performers section should be absent."""
        for i in range(5):
            pid = test_db.save_post(
                text=f"Short list post {i}",
                run_id="r1",
                status="published",
            )
            test_db.update_post_published(pid, f"at://p/{i}", f"cid{i}")
            test_db.save_metrics(pid, like_count=i + 1)

        result = get_performance_insights(test_db)

        assert "Top Performing Posts" in result
        assert "Lowest Performing Posts" not in result

    def test_10_or_more_posts_includes_bottom_section(self, test_db):
        """When 10 or more posts exist, both top and bottom sections should appear."""
        for i in range(12):
            pid = test_db.save_post(
                text=f"Post number {i} with enough detail to be meaningful",
                run_id="r1",
                status="published",
            )
            test_db.update_post_published(pid, f"at://p/{i}", f"cid{i}")
            test_db.save_metrics(pid, like_count=i * 3, repost_count=i, reply_count=1)

        result = get_performance_insights(test_db)

        assert "Top Performing Posts" in result
        assert "Lowest Performing Posts" in result

    def test_includes_recommendation_footer(self, test_db):
        """Output should include an actionable recommendation footer."""
        pid = test_db.save_post(
            text="Solo post for footer test",
            run_id="r1",
            status="published",
        )
        test_db.update_post_published(pid, "at://solo", "cidsolo")
        test_db.save_metrics(pid, like_count=5)

        result = get_performance_insights(test_db)

        assert "double down" in result.lower()
        assert "low performers" in result.lower()

    def test_client_scoped_insights(self, test_db):
        """Insights should filter by client_id when provided."""
        # Create posts for two different clients
        test_db.create_client({"id": "clientA", "name": "Client A"})
        test_db.create_client({"id": "clientB", "name": "Client B"})

        pid_a = test_db.save_post(
            text="Client A post about AI",
            run_id="r1",
            status="published",
            client_id="clientA",
        )
        test_db.update_post_published(pid_a, "at://a/1", "cida1")
        test_db.save_metrics(pid_a, like_count=20)

        pid_b = test_db.save_post(
            text="Client B post about finance",
            run_id="r2",
            status="published",
            client_id="clientB",
        )
        test_db.update_post_published(pid_b, "at://b/1", "cidb1")
        test_db.save_metrics(pid_b, like_count=5)

        result_a = get_performance_insights(test_db, client_id="clientA")

        assert "Client A post" in result_a
        assert "Client B post" not in result_a

    def test_posts_sorted_by_engagement(self, test_db):
        """Top section should list posts in descending engagement order."""
        texts_and_likes = [("Low post", 1), ("Medium post", 10), ("High post", 50)]
        for text, likes in texts_and_likes:
            pid = test_db.save_post(text=text, run_id="r1", status="published")
            test_db.update_post_published(pid, f"at://p/{text}", f"cid-{text}")
            test_db.save_metrics(pid, like_count=likes)

        result = get_performance_insights(test_db)

        # High post should appear before Low post in the output
        high_pos = result.index("High post")
        low_pos = result.index("Low post")
        assert high_pos < low_pos
