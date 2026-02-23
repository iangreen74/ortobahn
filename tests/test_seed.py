"""Tests for seed data functions."""

from __future__ import annotations

from unittest.mock import patch

from ortobahn.seed import (
    CTO_BACKLOG_TASKS,
    ORTOBAHN_CLIENT,
    VAULTSCALER_CLIENT,
    seed_all,
    seed_cto_backlog,
    seed_ortobahn,
    seed_vaultscaler,
)


class TestSeedVaultscaler:
    def test_creates_client(self, test_db):
        """seed_vaultscaler should create the vaultscaler client when it does not exist."""
        cid = seed_vaultscaler(test_db)

        assert cid == "vaultscaler"
        client = test_db.get_client("vaultscaler")
        assert client is not None
        assert client["name"] == "Vaultscaler"
        assert client["internal"] == 1

    def test_idempotent_on_existing(self, test_db):
        """Calling seed_vaultscaler twice should not create a duplicate."""
        cid1 = seed_vaultscaler(test_db)
        cid2 = seed_vaultscaler(test_db)

        assert cid1 == cid2 == "vaultscaler"
        # Should only have one vaultscaler client
        client = test_db.get_client("vaultscaler")
        assert client is not None

    def test_sets_auto_publish(self, test_db):
        """seed_vaultscaler should mark the client as auto_publish."""
        seed_vaultscaler(test_db)
        client = test_db.get_client("vaultscaler")
        assert client["auto_publish"] == 1

    def test_uses_vaultscaler_client_data(self, test_db):
        """Client should have the full Vaultscaler profile data."""
        seed_vaultscaler(test_db)
        client = test_db.get_client("vaultscaler")
        assert client["industry"] == VAULTSCALER_CLIENT["industry"]
        assert "Lev" in client["products"]


class TestSeedOrtobahn:
    def test_creates_client_when_fresh(self, test_db):
        """seed_ortobahn should create the ortobahn client when neither 'ortobahn' nor 'default' named Ortobahn exist."""
        # The default client from migrations is named "Ortobahn", so seed_ortobahn
        # will update the default client rather than creating a new one
        cid = seed_ortobahn(test_db)

        # Should return "default" since migration 001 creates default named Ortobahn
        assert cid in ("ortobahn", "default")
        client = test_db.get_client(cid)
        assert client is not None

    def test_updates_default_client_if_named_ortobahn(self, test_db):
        """If 'default' client is named Ortobahn, seed should update it in-place."""
        # Migration already creates 'default' named 'Ortobahn'
        default = test_db.get_client("default")
        assert default is not None
        assert default["name"] == "Ortobahn"

        cid = seed_ortobahn(test_db)

        assert cid == "default"
        updated = test_db.get_client("default")
        assert updated["description"] == ORTOBAHN_CLIENT["description"]
        assert updated["internal"] == 1
        assert updated["auto_publish"] == 1

    def test_idempotent(self, test_db):
        """Calling seed_ortobahn twice should not create duplicates."""
        cid1 = seed_ortobahn(test_db)
        cid2 = seed_ortobahn(test_db)

        assert cid1 == cid2

    def test_returns_ortobahn_if_exists(self, test_db):
        """If an 'ortobahn' client already exists, return its id without creating."""
        # Use a different name to avoid UNIQUE constraint with the migration-seeded 'default' client
        test_db.create_client({"id": "ortobahn", "name": "Ortobahn Self-Marketing"}, start_trial=False)
        test_db.execute("UPDATE clients SET internal=0, auto_publish=0 WHERE id='ortobahn'", commit=True)

        cid = seed_ortobahn(test_db)

        assert cid == "ortobahn"
        client = test_db.get_client("ortobahn")
        assert client["internal"] == 1
        assert client["auto_publish"] == 1


class TestSeedCtoBacklog:
    def test_creates_tasks(self, test_db):
        """seed_cto_backlog should create engineering tasks from the backlog."""
        task_ids = seed_cto_backlog(test_db)

        assert len(task_ids) == len(CTO_BACKLOG_TASKS)
        tasks = test_db.get_engineering_tasks(limit=100)
        task_titles = {t["title"] for t in tasks}
        for task_data in CTO_BACKLOG_TASKS:
            assert task_data["title"] in task_titles

    def test_idempotent(self, test_db):
        """Calling seed_cto_backlog twice should not create duplicate tasks."""
        ids1 = seed_cto_backlog(test_db)
        ids2 = seed_cto_backlog(test_db)

        assert len(ids1) == len(CTO_BACKLOG_TASKS)
        assert len(ids2) == 0  # No new tasks created on second call

        tasks = test_db.get_engineering_tasks(limit=100)
        assert len(tasks) == len(CTO_BACKLOG_TASKS)

    def test_returns_task_ids(self, test_db):
        """Returned task IDs should match tasks in the database."""
        task_ids = seed_cto_backlog(test_db)

        for tid in task_ids:
            # Verify each task can be found
            tasks = test_db.get_engineering_tasks(limit=100)
            found = any(t["id"] == tid for t in tasks)
            assert found, f"Task {tid} not found in database"

    def test_task_priorities_set(self, test_db):
        """Tasks should have the correct priority values from seed data."""
        seed_cto_backlog(test_db)
        tasks = test_db.get_engineering_tasks(limit=100)
        task_by_title = {t["title"]: t for t in tasks}

        for task_data in CTO_BACKLOG_TASKS:
            stored = task_by_title[task_data["title"]]
            assert stored["priority"] == task_data["priority"]
            assert stored["category"] == task_data["category"]


class TestSeedAll:
    def test_orchestrates_all_seeds(self, test_db):
        """seed_all should create both clients and seed the backlog."""
        ids = seed_all(test_db)

        assert len(ids) == 2
        assert "vaultscaler" in ids
        # ortobahn or default, depending on migration state
        assert any(cid in ("ortobahn", "default") for cid in ids)

        # Verify backlog was seeded
        tasks = test_db.get_engineering_tasks(limit=100)
        assert len(tasks) == len(CTO_BACKLOG_TASKS)

    @patch("ortobahn.seed.seed_ortobahn_credentials")
    @patch("ortobahn.seed.seed_vaultscaler_credentials")
    def test_migrates_credentials_when_settings_provided(self, mock_vs_creds, mock_ob_creds, test_db, test_settings):
        """seed_all should call credential migration when settings are provided."""
        seed_all(test_db, settings=test_settings)

        mock_vs_creds.assert_called_once_with(test_db, test_settings)
        mock_ob_creds.assert_called_once_with(test_db, test_settings)

    def test_skips_credentials_without_settings(self, test_db):
        """seed_all without settings should skip credential migration."""
        # Should not raise even without settings
        ids = seed_all(test_db)
        assert len(ids) == 2

    def test_returns_client_ids(self, test_db):
        """seed_all should return the IDs of the seeded clients."""
        ids = seed_all(test_db)

        for cid in ids:
            client = test_db.get_client(cid)
            assert client is not None
