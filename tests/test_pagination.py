"""Tests for pagination support."""

from __future__ import annotations

from ortobahn.db import Database


class TestPostsPagination:
    def test_offset_skips_rows(self, tmp_path):
        db = Database(tmp_path / "page.db")
        # Create 5 posts
        for i in range(5):
            db.save_post(text=f"Post {i}", run_id="r1", status="published", client_id="default")

        all_posts = db.get_recent_posts_with_metrics(limit=10, client_id="default")
        page1 = db.get_recent_posts_with_metrics(limit=2, offset=0, client_id="default")
        page2 = db.get_recent_posts_with_metrics(limit=2, offset=2, client_id="default")

        assert len(all_posts) == 5
        assert len(page1) == 2
        assert len(page2) == 2
        # Pages should have different posts
        page1_ids = {p["id"] for p in page1}
        page2_ids = {p["id"] for p in page2}
        assert page1_ids.isdisjoint(page2_ids)
        db.close()

    def test_offset_beyond_results_returns_empty(self, tmp_path):
        db = Database(tmp_path / "page2.db")
        db.save_post(text="Only post", run_id="r1", status="published")
        result = db.get_recent_posts_with_metrics(limit=10, offset=100)
        assert result == []
        db.close()

    def test_count_posts(self, tmp_path):
        db = Database(tmp_path / "count.db")
        db.save_post(text="Draft", run_id="r1", status="draft", client_id="c1")
        db.save_post(text="Published", run_id="r1", status="published", client_id="c1")
        db.save_post(text="Other client", run_id="r1", status="published", client_id="c2")

        assert db.count_posts() == 3
        assert db.count_posts(client_id="c1") == 2
        assert db.count_posts(client_id="c1", status="published") == 1
        assert db.count_posts(client_id="c2") == 1
        db.close()

    def test_get_all_posts_offset(self, tmp_path):
        db = Database(tmp_path / "all.db")
        for i in range(5):
            db.save_post(text=f"Post {i}", run_id="r1", status="draft")
        page = db.get_all_posts(limit=2, offset=2)
        assert len(page) == 2
        db.close()


class TestArticlesPagination:
    def test_get_recent_articles_offset(self, tmp_path):
        db = Database(tmp_path / "articles.db")
        for i in range(5):
            db.save_article(
                {
                    "title": f"Article {i}",
                    "body_markdown": f"Body {i}",
                    "client_id": "c1",
                }
            )

        all_articles = db.get_recent_articles("c1", limit=10)
        page1 = db.get_recent_articles("c1", limit=2, offset=0)
        page2 = db.get_recent_articles("c1", limit=2, offset=2)

        assert len(all_articles) == 5
        assert len(page1) == 2
        assert len(page2) == 2
        page1_ids = {a["id"] for a in page1}
        page2_ids = {a["id"] for a in page2}
        assert page1_ids.isdisjoint(page2_ids)
        db.close()

    def test_count_articles(self, tmp_path):
        db = Database(tmp_path / "count_articles.db")
        db.save_article({"title": "A1", "body_markdown": "B1", "client_id": "c1"})
        db.save_article({"title": "A2", "body_markdown": "B2", "client_id": "c1"})
        db.save_article({"title": "A3", "body_markdown": "B3", "client_id": "c2"})

        assert db.count_articles("c1") == 2
        assert db.count_articles("c2") == 1
        assert db.count_articles("c3") == 0
        db.close()

    def test_offset_beyond_results_returns_empty(self, tmp_path):
        db = Database(tmp_path / "articles_empty.db")
        db.save_article({"title": "Only", "body_markdown": "one", "client_id": "c1"})
        result = db.get_recent_articles("c1", limit=10, offset=100)
        assert result == []
        db.close()
