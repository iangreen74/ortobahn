"""Tests for database backup utility."""

from __future__ import annotations

import time
from pathlib import Path

from ortobahn.backup import backup_database


class TestBackupDatabase:
    def test_backup_creates_file(self, tmp_path):
        """Backup should copy the DB file and return a path to the new backup."""
        db_file = tmp_path / "ortobahn.db"
        db_file.write_text("sqlite data")
        backup_dir = tmp_path / "backups"

        result = backup_database(db_file, backup_dir)

        assert result is not None
        assert result.exists()
        assert result.parent == backup_dir
        assert result.name.startswith("ortobahn_")
        assert result.name.endswith(".db")
        assert result.read_text() == "sqlite data"

    def test_backup_returns_path(self, tmp_path):
        """Return value should be a Path pointing to the new backup."""
        db_file = tmp_path / "ortobahn.db"
        db_file.write_text("data")
        backup_dir = tmp_path / "backups"

        result = backup_database(db_file, backup_dir)

        assert isinstance(result, Path)
        assert result.is_file()

    def test_backup_creates_dir_if_missing(self, tmp_path):
        """Backup dir should be created automatically if it does not exist."""
        db_file = tmp_path / "ortobahn.db"
        db_file.write_text("data")
        backup_dir = tmp_path / "nested" / "backups"

        assert not backup_dir.exists()
        result = backup_database(db_file, backup_dir)

        assert result is not None
        assert backup_dir.exists()
        assert backup_dir.is_dir()

    def test_backup_returns_none_for_missing_db(self, tmp_path):
        """Should return None without error if the source DB does not exist."""
        db_file = tmp_path / "nonexistent.db"
        backup_dir = tmp_path / "backups"

        result = backup_database(db_file, backup_dir)

        assert result is None
        assert not backup_dir.exists()

    def test_backup_prunes_oldest_when_exceeding_max(self, tmp_path):
        """When backup count exceeds max_backups, the oldest should be removed."""
        db_file = tmp_path / "ortobahn.db"
        db_file.write_text("data")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create pre-existing backups with staggered mtimes
        import os

        for i in range(5):
            f = backup_dir / f"ortobahn_2025010{i}_000000.db"
            f.write_text(f"old-{i}")
            # Set mtime to well in the past so the new backup is always newest
            os.utime(f, (1_000_000 + i * 100, 1_000_000 + i * 100))

        # Backup with max_backups=3 -- existing 5 + 1 new = 6, prune to 3
        result = backup_database(db_file, backup_dir, max_backups=3)

        assert result is not None
        remaining = list(backup_dir.glob("ortobahn_*.db"))
        assert len(remaining) == 3

        # The newest backup (just created) should be among the remaining
        assert result in remaining

    def test_backup_no_pruning_under_limit(self, tmp_path):
        """When backup count is under max_backups, nothing should be pruned."""
        db_file = tmp_path / "ortobahn.db"
        db_file.write_text("data")
        backup_dir = tmp_path / "backups"

        # Use time.sleep to ensure distinct timestamps in the filename
        backup_database(db_file, backup_dir, max_backups=10)
        time.sleep(1.1)  # Ensure different second for timestamp-based name
        backup_database(db_file, backup_dir, max_backups=10)

        remaining = list(backup_dir.glob("ortobahn_*.db"))
        assert len(remaining) == 2

    def test_backup_file_content_matches_source(self, tmp_path):
        """Backup file should contain the exact same bytes as the source."""
        db_file = tmp_path / "ortobahn.db"
        content = b"\x00SQLite format 3\x00" + b"\xff" * 100
        db_file.write_bytes(content)
        backup_dir = tmp_path / "backups"

        result = backup_database(db_file, backup_dir)

        assert result is not None
        assert result.read_bytes() == content
