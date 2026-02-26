"""Tests for the Content Repurposer."""

from __future__ import annotations

import pytest

from ortobahn.repurposer import Repurposer


@pytest.fixture()
def _seed_client(test_db):
    test_db.create_client(
        {
            "id": "repurpose-test",
            "name": "Repurpose Test Co",
            "industry": "tech",
            "target_audience": "developers",
            "brand_voice": "professional",
        }
    )


@pytest.fixture()
def _seed_published_post(test_db, _seed_client):
    pid = test_db.save_post(
        text="AI is transforming how small businesses do marketing. Here are 5 key trends we're tracking this quarter.",
        run_id="rep-run",
        status="published",
        confidence=0.85,
        client_id="repurpose-test",
        platform="bluesky",
    )
    test_db.execute(
        "UPDATE posts SET published_at=CURRENT_TIMESTAMP WHERE id=?",
        (pid,),
        commit=True,
    )
    return pid


@pytest.fixture()
def _seed_article(test_db, _seed_client):
    import uuid

    article_id = str(uuid.uuid4())
    test_db.execute(
        """INSERT INTO articles
            (id, client_id, run_id, title, subtitle, body_markdown, tags, status, word_count, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'published', 100, 0.8, CURRENT_TIMESTAMP)""",
        (
            article_id,
            "repurpose-test",
            "test-article-run",
            "5 AI Marketing Trends",
            "What to watch in 2025",
            "Paragraph one about trend A.\n\nParagraph two about trend B.\n\n"
            "Paragraph three about trend C.\n\nParagraph four about trend D.\n\n"
            "Paragraph five about trend E.\n\nConclusion paragraph.",
            "[]",
        ),
        commit=True,
    )
    return article_id


class TestPostToArticle:
    def test_creates_draft_article(self, test_db, _seed_published_post):
        pid = _seed_published_post
        repurposer = Repurposer(test_db)
        article_id = repurposer.post_to_article(pid, "repurpose-test")

        assert article_id is not None
        article = test_db.fetchone("SELECT * FROM articles WHERE id=?", (article_id,))
        assert article is not None
        assert article["client_id"] == "repurpose-test"
        assert article["source_post_id"] == pid
        assert article["status"] == "draft"
        assert "AI is transforming" in article["body_markdown"]

    def test_returns_none_for_nonexistent_post(self, test_db, _seed_client):
        repurposer = Repurposer(test_db)
        result = repurposer.post_to_article("nonexistent", "repurpose-test")
        assert result is None

    def test_returns_none_for_wrong_client(self, test_db, _seed_published_post):
        repurposer = Repurposer(test_db)
        result = repurposer.post_to_article(_seed_published_post, "wrong-client")
        assert result is None


class TestArticleToSeries:
    def test_creates_post_series(self, test_db, _seed_article):
        article_id = _seed_article
        repurposer = Repurposer(test_db)
        post_ids = repurposer.article_to_series(article_id, "repurpose-test", num_posts=3)

        assert len(post_ids) == 3

        for pid in post_ids:
            post = test_db.fetchone("SELECT * FROM posts WHERE id=?", (pid,))
            assert post is not None
            assert post["client_id"] == "repurpose-test"
            assert post["status"] == "draft"
            assert post["source_article_id"] == article_id
            assert post["repurpose_type"] == "article_to_series"
            assert post["series_id"] is not None

    def test_returns_empty_for_nonexistent_article(self, test_db, _seed_client):
        repurposer = Repurposer(test_db)
        result = repurposer.article_to_series("nonexistent", "repurpose-test")
        assert result == []

    def test_respects_platform_limit(self, test_db, _seed_article):
        repurposer = Repurposer(test_db)
        post_ids = repurposer.article_to_series(_seed_article, "repurpose-test", platform="bluesky", num_posts=2)

        for pid in post_ids:
            post = test_db.fetchone("SELECT text, platform FROM posts WHERE id=?", (pid,))
            assert len(post["text"]) <= 280
            assert post["platform"] == "bluesky"

    def test_creates_content_series_record(self, test_db, _seed_article):
        repurposer = Repurposer(test_db)
        post_ids = repurposer.article_to_series(_seed_article, "repurpose-test", num_posts=3)

        # Check that a content_series record was created
        first_post = test_db.fetchone("SELECT series_id FROM posts WHERE id=?", (post_ids[0],))
        series = test_db.fetchone(
            "SELECT * FROM content_series WHERE id=?",
            (first_post["series_id"],),
        )
        assert series is not None
        assert series["client_id"] == "repurpose-test"
        assert series["max_parts"] == 3


class TestGetRepurposeCandidates:
    def test_finds_high_performers(self, test_db, _seed_client):
        """Should find published posts with high engagement."""
        # Create posts with varying engagement
        for i in range(5):
            pid = test_db.save_post(
                text=f"Regular post {i}",
                run_id=f"cand-run-{i}",
                status="published",
                confidence=0.7,
                client_id="repurpose-test",
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
                (f"m-cand-{i}", pid, 3, 1, 1),
                commit=True,
            )

        # Add a high performer
        hp = test_db.save_post(
            text="This viral post should be repurposed",
            run_id="cand-hp",
            status="published",
            confidence=0.9,
            client_id="repurpose-test",
            platform="bluesky",
        )
        test_db.execute(
            "UPDATE posts SET published_at=CURRENT_TIMESTAMP WHERE id=?",
            (hp,),
            commit=True,
        )
        test_db.execute(
            "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at)"
            " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ("m-cand-hp", hp, 50, 20, 10),
            commit=True,
        )

        repurposer = Repurposer(test_db)
        candidates = repurposer.get_repurpose_candidates("repurpose-test")
        assert len(candidates) >= 1
        ids = [c["id"] for c in candidates]
        assert hp in ids

    def test_empty_with_no_data(self, test_db, _seed_client):
        repurposer = Repurposer(test_db)
        result = repurposer.get_repurpose_candidates("repurpose-test")
        assert result == []
