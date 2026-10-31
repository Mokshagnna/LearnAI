"""
backup.py
=========
Transactionally consistent SQLite Online Backup and Safe Restore Utilities for LearnAI.
Uses Python's native sqlite3 Connection.backup API with integrity verification and retention management.
"""

import os
import sqlite3
import json
import logging
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from services.database import get_db_path, get_connection

logger = logging.getLogger("learnai.backup")

_BACKUP_LOCK = threading.Lock()
DEFAULT_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))


def get_backup_dir() -> Path:
    """Resolve backup directory from environment or active DB parent directory."""
    custom_dir = os.getenv("LEARNAI_BACKUP_DIR")
    if custom_dir:
        p = Path(custom_dir).resolve()
    else:
        p = get_db_path().parent / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def verify_database_integrity(db_path: Path) -> bool:
    """
    Execute PRAGMA integrity_check on the specified SQLite database file.
    Returns True if healthy ('ok'), False otherwise.
    """
    if not db_path.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        row = cursor.fetchone()
        conn.close()
        return bool(row and row[0] == "ok")
    except Exception as e:
        logger.error("Integrity check failed for %s: %s", db_path, e)
        return False


def prune_old_backups(
    backup_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    active_db_path: Optional[Path] = None
) -> List[Path]:
    """
    Prune backups older than retention_days.
    Safety Guarantees:
    - Never deletes the active database file.
    - Never deletes the last remaining valid backup file, even if older than retention_days.
    Returns list of deleted backup file paths.
    """
    active_path = (active_db_path or get_db_path()).resolve()
    backups = sorted(
        backup_dir.glob("learnai_backup_*.db"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    if len(backups) <= 1:
        # Retain at least one valid backup
        return []

    cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = []

    # Keep the newest backup unconditionally; examine the rest
    for b in backups[1:]:
        if b.resolve() == active_path:
            continue
        mtime = datetime.fromtimestamp(b.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff_time:
            try:
                b.unlink()
                deleted.append(b)
                logger.info("Pruned expired backup: %s (created %s)", b.name, mtime.isoformat())
            except Exception as e:
                logger.warning("Failed to delete expired backup %s: %s", b, e)

    return deleted


def backup_database(
    db_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    dest_path: Optional[Path] = None,
    retention_days: int = DEFAULT_RETENTION_DAYS
) -> Dict[str, Any]:
    """
    Create an online, transactionally consistent backup using SQLite Connection.backup API.
    Guarantees:
    - Non-blocking snapshot while application reads and writes occur.
    - Thread-safe via global backup lock.
    - Never overwrites the active database.
    - Applies retention policy, keeping at least the latest backup.
    """
    with _BACKUP_LOCK:
        source_path = (db_path or get_db_path()).resolve()
        target_dir = (backup_dir or get_backup_dir()).resolve()

        if not source_path.is_file():
            raise FileNotFoundError(f"Source database file not found at {source_path}")

        if target_dir == source_path:
            raise ValueError("Backup target directory cannot be the active database file!")

        now = datetime.now(timezone.utc)
        if dest_path is not None:
            final_dest = dest_path.resolve()
            final_dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            timestamp_str = now.strftime("%Y%m%d_%H%M%S")
            unique_suffix = uuid.uuid4().hex[:6]
            dest_filename = f"learnai_backup_{timestamp_str}_{unique_suffix}.db"
            final_dest = target_dir / dest_filename

        # Safeguard: Never overwrite active database
        if final_dest.resolve() == source_path:
            raise ValueError("Backup target path cannot be the same as the active database path!")

        logger.info("Starting online database backup from %s to %s", source_path, final_dest)

        source_conn = None
        dest_conn = None
        try:
            source_conn = get_connection(source_path)
            dest_conn = sqlite3.connect(str(final_dest))

            # Perform consistent online backup in 100-page increments
            source_conn.backup(dest_conn, pages=100)
            dest_conn.close()
            dest_conn = None

            # Verify integrity of newly generated backup
            if not verify_database_integrity(final_dest):
                if final_dest.is_file():
                    final_dest.unlink()
                raise RuntimeError(f"Backup failed PRAGMA integrity_check: {final_dest}")

            size_bytes = final_dest.stat().st_size

            # Prune older backups according to retention policy
            pruned = prune_old_backups(final_dest.parent, retention_days=retention_days, active_db_path=source_path)

            logger.info("Backup completed successfully: %s (%d bytes)", final_dest.name, size_bytes)
            return {
                "status": "success",
                "backup_file": str(final_dest),
                "filename": final_dest.name,
                "size_bytes": size_bytes,
                "created_at": now.isoformat(),
                "pruned_count": len(pruned)
            }

        except Exception as e:
            logger.error("Database backup failed: %s", e, exc_info=True)
            if dest_conn is not None:
                try:
                    dest_conn.close()
                except Exception:
                    pass
            if final_dest.is_file():
                try:
                    final_dest.unlink()
                except Exception:
                    pass
            raise
        finally:
            if source_conn is not None:
                try:
                    source_conn.close()
                except Exception:
                    pass


def restore_database(
    backup_file: Path,
    target_db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Safely restore database from a verified backup snapshot.
    Guarantees:
    - Pre-verifies backup file with PRAGMA integrity_check before altering target.
    - Creates a .pre_restore.bak safety copy of the existing target database.
    - Verifies integrity of the restored database.
    - If restore fails, automatically rolls back to the safety copy.
    """
    with _BACKUP_LOCK:
        backup_path = Path(backup_file).resolve()
        if not backup_path.is_file():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        target_path = (target_db_path or get_db_path()).resolve()

        # 1. Pre-verification: Verify backup snapshot integrity
        if not verify_database_integrity(backup_path):
            raise ValueError(f"Cannot restore: backup file failed integrity check at {backup_path}")

        safety_copy = target_path.with_suffix(".pre_restore.bak")
        target_existed = target_path.is_file()

        try:
            # 2. Safety copy: Move existing target to .pre_restore.bak
            if target_existed:
                if safety_copy.is_file():
                    safety_copy.unlink()
                target_path.rename(safety_copy)
                logger.info("Created safety pre-restore copy at %s", safety_copy)

            # 3. Perform restoration via SQLite backup API for clean page writing
            source_conn = sqlite3.connect(str(backup_path))
            dest_conn = sqlite3.connect(str(target_path))
            source_conn.backup(dest_conn, pages=100)
            source_conn.close()
            dest_conn.close()

            # 4. Post-verification
            if not verify_database_integrity(target_path):
                raise RuntimeError("Restored database failed integrity check!")

            logger.info("Successfully restored database from %s to %s", backup_path.name, target_path)
            return {
                "status": "restored",
                "target_path": str(target_path),
                "source_backup": str(backup_path),
                "safety_copy": str(safety_copy) if target_existed else None
            }

        except Exception as e:
            logger.error("Restore failed: %s; rolling back to safety copy if available", e, exc_info=True)
            # Rollback to safety copy
            if target_existed and safety_copy.is_file():
                if target_path.is_file():
                    target_path.unlink()
                safety_copy.rename(target_path)
                logger.info("Successfully rolled back to safety copy at %s", target_path)
            raise
