"""Database backup utility."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ortobahn.backup")


def backup_database(db_path: Path, backup_dir: Path, max_backups: int = 10) -> Path | None:
    """Copy the SQLite database to a timestamped backup file.

    Removes oldest backups if count exceeds max_backups.
    Returns the backup path, or None if db_path doesn't exist.
    """
    if not db_path.exists():
        logger.warning(f"Database not found at {db_path}, skipping backup")
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"ortobahn_{timestamp}.db"

    shutil.copy2(db_path, backup_path)
    logger.info(f"Database backed up to {backup_path}")

    # Prune old backups
    backups = sorted(backup_dir.glob("ortobahn_*.db"), key=lambda p: p.stat().st_mtime)
    while len(backups) > max_backups:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.info(f"Removed old backup: {oldest}")

    return backup_path
