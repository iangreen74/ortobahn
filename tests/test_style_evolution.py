"""Tests for Style Evolution module."""

from __future__ import annotations

import pytest

from ortobahn.style_evolution import EXPERIMENT_TEMPLATES, StyleEvolution


@pytest.fixture
def style_evo(test_db):
    return StyleEvolution(db=test_db)


class TestCreateExperiment:
    def test_inserts_into_ab_experiments(self, test_db, style_evo):
        result = style_evo.create_experiment(
            client_id="default",
            hypothesis="Short posts win",
            variable="post_length",
            variant_a="Short",
            variant_b="Long",
            min_pairs=5,
            run_id="run-1",
        )

        assert result["client_id"] == "default"
        assert result["hypothesis"] == "Short posts win"
        assert result["variable"] == "post_length"
        assert result["variant_a_description"] == "Short"
        assert result["variant_b_description"] == "Long"
        assert result["status"] == "active"
        assert result["pair_count"] == 0
        assert result["min_pairs_required"] == 5

        # Verify row exists in DB
        row = test_db.fetchone(
            "SELECT * FROM ab_experiments WHERE id = ?",
            (result["id"],),
        )
        assert row is not None
        assert row["client_id"] == "default"
        assert row["hypothesis"] == "Short posts win"
        assert row["variable"] == "post_length"
        assert row["variant_a_description"] == "Short"
        assert row["variant_b_description"] == "Long"
        assert row["status"] == "active"
        assert row["pair_count"] == 0
        assert row["min_pairs_required"] == 5
        assert row["created_by_run_id"] == "run-1"
        assert row["created_at"] is not None


class TestGetActiveExperiment:
    def test_returns_active_experiment(self, test_db, style_evo):
        created = style_evo.create_experiment(
            client_id="default",
            hypothesis="Hook style matters",
            variable="hook_style",
            variant_a="Question",
            variant_b="Statement",
        )

        active = style_evo.get_active_experiment("default")
        assert active is not None
        assert active["id"] == created["id"]
        assert active["variable"] == "hook_style"
        assert active["status"] == "active"

    def test_returns_none_when_no_active(self, test_db, style_evo):
        result = style_evo.get_active_experiment("default")
        assert result is None

    def test_returns_none_when_only_concluded(self, test_db, style_evo):
        created = style_evo.create_experiment(
            client_id="default",
            hypothesis="Test",
            variable="tone",
            variant_a="A",
            variant_b="B",
        )
        # Mark as concluded
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded' WHERE id = ?",
            (created["id"],),
            commit=True,
        )

        result = style_evo.get_active_experiment("default")
        assert result is None


class TestEnsureActiveExperiment:
    def test_creates_from_first_template_when_none_exists(self, test_db, style_evo):
        result = style_evo.ensure_active_experiment(client_id="default", run_id="run-1")

        assert result is not None
        first_template = EXPERIMENT_TEMPLATES[0]
        assert result["hypothesis"] == first_template["hypothesis"]
        assert result["variable"] == first_template["variable"]
        assert result["variant_a_description"] == first_template["variant_a"]
        assert result["variant_b_description"] == first_template["variant_b"]
        assert result["status"] == "active"

    def test_returns_existing_active_without_creating_new(self, test_db, style_evo):
        # Create an existing active experiment manually
        existing = style_evo.create_experiment(
            client_id="default",
            hypothesis="Custom hypothesis",
            variable="custom_var",
            variant_a="Custom A",
            variant_b="Custom B",
        )

        result = style_evo.ensure_active_experiment(client_id="default", run_id="run-2")

        assert result is not None
        assert result["id"] == existing["id"]
        assert result["hypothesis"] == "Custom hypothesis"

        # Verify no additional experiments were created
        rows = test_db.fetchall(
            "SELECT * FROM ab_experiments WHERE client_id = ?",
            ("default",),
        )
        assert len(rows) == 1

    def test_skips_already_used_templates(self, test_db, style_evo):
        # Create and conclude an experiment for the first template variable
        first = style_evo.create_experiment(
            client_id="default",
            hypothesis=EXPERIMENT_TEMPLATES[0]["hypothesis"],
            variable="post_length",
            variant_a="Short",
            variant_b="Long",
        )
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded', winner = 'A' WHERE id = ?",
            (first["id"],),
            commit=True,
        )

        result = style_evo.ensure_active_experiment(client_id="default", run_id="run-2")

        assert result is not None
        second_template = EXPERIMENT_TEMPLATES[1]
        assert result["variable"] == "hook_style"
        assert result["hypothesis"] == second_template["hypothesis"]

    def test_returns_none_when_all_templates_exhausted(self, test_db, style_evo):
        # Create concluded experiments for every template variable
        for tmpl in EXPERIMENT_TEMPLATES:
            exp = style_evo.create_experiment(
                client_id="default",
                hypothesis=tmpl["hypothesis"],
                variable=tmpl["variable"],
                variant_a=tmpl["variant_a"],
                variant_b=tmpl["variant_b"],
            )
            test_db.execute(
                "UPDATE ab_experiments SET status = 'concluded', winner = 'A' WHERE id = ?",
                (exp["id"],),
                commit=True,
            )

        result = style_evo.ensure_active_experiment(client_id="default", run_id="run-x")
        assert result is None


class TestGetExperimentContext:
    def test_returns_formatted_string_with_experiment_details(self, test_db, style_evo):
        style_evo.create_experiment(
            client_id="default",
            hypothesis="Short posts win",
            variable="post_length",
            variant_a="Short and punchy",
            variant_b="Detailed and informative",
            min_pairs=5,
        )

        ctx = style_evo.get_experiment_context("default")

        assert "## A/B Style Experiment (ACTIVE)" in ctx
        assert "Hypothesis: Short posts win" in ctx
        assert "Variable being tested: post_length" in ctx
        assert "Variant A: Short and punchy" in ctx
        assert "Variant B: Detailed and informative" in ctx
        assert "Pairs completed: 0/5" in ctx
        assert "IMPORTANT: For ONE of the post ideas, generate TWO versions:" in ctx
        assert "ab_group" in ctx

    def test_returns_empty_string_when_no_active_experiment(self, test_db, style_evo):
        ctx = style_evo.get_experiment_context("default")
        assert ctx == ""

    def test_includes_past_experiment_results(self, test_db, style_evo):
        # Create and conclude a past experiment
        past_exp = style_evo.create_experiment(
            client_id="default",
            hypothesis="Short posts win",
            variable="post_length",
            variant_a="Short and punchy",
            variant_b="Detailed and informative",
        )
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded', winner = 'A', "
            "result_summary = 'A won by 60%', concluded_at = '2025-01-01T00:00:00' "
            "WHERE id = ?",
            (past_exp["id"],),
            commit=True,
        )

        # Create a new active experiment
        style_evo.create_experiment(
            client_id="default",
            hypothesis="Questions hook better",
            variable="hook_style",
            variant_a="Question hook",
            variant_b="Statement hook",
        )

        ctx = style_evo.get_experiment_context("default")

        assert "## Past Experiment Results (apply these learnings):" in ctx
        assert "Short posts win" in ctx
        assert "Short and punchy" in ctx  # Winner was A


class TestTagPostPair:
    def test_updates_posts_with_ab_group_and_pair_id(self, test_db, style_evo):
        # Create an experiment
        exp = style_evo.create_experiment(
            client_id="default",
            hypothesis="Test",
            variable="test_var",
            variant_a="A style",
            variant_b="B style",
        )

        # Insert two test posts
        post_a_id = test_db.save_post(text="A variant", run_id="run-1", client_id="default")
        post_b_id = test_db.save_post(text="B variant", run_id="run-1", client_id="default")

        pair_id = style_evo.tag_post_pair(post_a_id, post_b_id, exp["id"])

        assert pair_id is not None
        assert len(pair_id) > 0

        # Verify post A was tagged
        post_a = test_db.fetchone("SELECT * FROM posts WHERE id = ?", (post_a_id,))
        assert post_a["ab_group"] == "A"
        assert post_a["ab_pair_id"] == pair_id

        # Verify post B was tagged
        post_b = test_db.fetchone("SELECT * FROM posts WHERE id = ?", (post_b_id,))
        assert post_b["ab_group"] == "B"
        assert post_b["ab_pair_id"] == pair_id

        # Verify experiment pair_count was incremented
        row = test_db.fetchone("SELECT pair_count FROM ab_experiments WHERE id = ?", (exp["id"],))
        assert row["pair_count"] == 1

    def test_increments_pair_count_multiple_times(self, test_db, style_evo):
        exp = style_evo.create_experiment(
            client_id="default",
            hypothesis="Test",
            variable="test_var",
            variant_a="A",
            variant_b="B",
        )

        # Tag two pairs
        post_a1 = test_db.save_post(text="A1", run_id="run-1", client_id="default")
        post_b1 = test_db.save_post(text="B1", run_id="run-1", client_id="default")
        style_evo.tag_post_pair(post_a1, post_b1, exp["id"])

        post_a2 = test_db.save_post(text="A2", run_id="run-1", client_id="default")
        post_b2 = test_db.save_post(text="B2", run_id="run-1", client_id="default")
        style_evo.tag_post_pair(post_a2, post_b2, exp["id"])

        row = test_db.fetchone("SELECT pair_count FROM ab_experiments WHERE id = ?", (exp["id"],))
        assert row["pair_count"] == 2


class TestGetStyleLearnings:
    def test_returns_concluded_experiments_with_winners(self, test_db, style_evo):
        # Create two experiments, conclude both with winners
        exp1 = style_evo.create_experiment(
            client_id="default",
            hypothesis="Short posts win",
            variable="post_length",
            variant_a="Short",
            variant_b="Long",
        )
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded', winner = 'A', "
            "result_summary = 'A won by 60%', concluded_at = '2025-01-01T00:00:00' "
            "WHERE id = ?",
            (exp1["id"],),
            commit=True,
        )

        exp2 = style_evo.create_experiment(
            client_id="default",
            hypothesis="Questions hook better",
            variable="hook_style",
            variant_a="Question",
            variant_b="Statement",
        )
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded', winner = 'B', "
            "result_summary = 'B won by 55%', concluded_at = '2025-01-02T00:00:00' "
            "WHERE id = ?",
            (exp2["id"],),
            commit=True,
        )

        learnings = style_evo.get_style_learnings("default")

        assert len(learnings) == 2
        # Ordered by concluded_at DESC so exp2 is first
        assert learnings[0]["variable"] == "hook_style"
        assert learnings[0]["winner"] == "B"
        assert learnings[0]["result_summary"] == "B won by 55%"
        assert learnings[1]["variable"] == "post_length"
        assert learnings[1]["winner"] == "A"
        assert learnings[1]["result_summary"] == "A won by 60%"

    def test_excludes_experiments_without_winner(self, test_db, style_evo):
        exp = style_evo.create_experiment(
            client_id="default",
            hypothesis="Test",
            variable="tone",
            variant_a="A",
            variant_b="B",
        )
        # Concluded but no winner (inconclusive)
        test_db.execute(
            "UPDATE ab_experiments SET status = 'concluded', concluded_at = '2025-01-01T00:00:00' WHERE id = ?",
            (exp["id"],),
            commit=True,
        )

        learnings = style_evo.get_style_learnings("default")
        assert len(learnings) == 0

    def test_excludes_active_experiments(self, test_db, style_evo):
        style_evo.create_experiment(
            client_id="default",
            hypothesis="Test",
            variable="tone",
            variant_a="A",
            variant_b="B",
        )

        learnings = style_evo.get_style_learnings("default")
        assert len(learnings) == 0

    def test_returns_empty_list_when_no_experiments(self, test_db, style_evo):
        learnings = style_evo.get_style_learnings("default")
        assert learnings == []
