"""
migrations.py
=============
Schema migration runner for Supabase PostgreSQL and SQLite.
Discovers versioned SQL files, manages schema_migrations table, and provides idempotent upgrades.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("learnai.db.migrations")


def get_migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "migrations"


def ensure_migration_table(conn: Any, is_postgres: bool = True):
    """Ensure the schema_migrations tracking table exists."""
    cursor = conn.cursor()
    if is_postgres:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            );
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)
    conn.commit()


def get_applied_migrations(conn: Any) -> List[str]:
    """Retrieve list of applied migration versions."""
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    rows = cursor.fetchall()
    return [r[0] if isinstance(r, (tuple, list)) else r["version"] for r in rows]


def apply_migrations(conn: Any, is_postgres: bool = True, migrations_dir: Optional[Path] = None) -> List[str]:
    """
    Discover and apply unapplied SQL migration files in ascending order.
    Returns list of newly applied migration versions.
    """
    mdir = migrations_dir or get_migrations_dir()
    if not mdir.is_dir():
        logger.warning("Migrations directory not found at %s", mdir)
        return []

    ensure_migration_table(conn, is_postgres=is_postgres)
    applied = set(get_applied_migrations(conn))
    newly_applied = []

    sql_files = sorted([f for f in mdir.glob("*.sql") if not f.name.endswith(".rollback.sql")])

    for sql_file in sql_files:
        version = sql_file.stem
        if version in applied:
            continue

        logger.info("Applying migration %s (%s)...", version, sql_file.name)
        with open(sql_file, "r", encoding="utf-8") as f:
            sql_content = f.read()

        cursor = conn.cursor()
        try:
            # Execute full script
            cursor.execute(sql_content)
            now_str = datetime.now(timezone.utc).isoformat()
            if is_postgres:
                cursor.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s);",
                    (version, now_str)
                )
            else:
                cursor.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                    (version, now_str)
                )
            conn.commit()
            newly_applied.append(version)
            logger.info("Successfully applied migration %s.", version)
        except Exception as e:
            conn.rollback()
            logger.error("Failed to apply migration %s: %s", version, e)
            raise RuntimeError(f"Migration {version} failed: {e}") from e

    return newly_applied


def rollback_migration(conn: Any, version: str, is_postgres: bool = True, migrations_dir: Optional[Path] = None):
    """
    Execute rollback script for a specific migration version.
    """
    mdir = migrations_dir or get_migrations_dir()
    rollback_file = mdir / f"{version}.rollback.sql"
    if not rollback_file.is_file():
        raise FileNotFoundError(f"Rollback file not found: {rollback_file}")

    with open(rollback_file, "r", encoding="utf-8") as f:
        sql_content = f.read()

    cursor = conn.cursor()
    try:
        cursor.execute(sql_content)
        if is_postgres:
            cursor.execute("DELETE FROM schema_migrations WHERE version = %s;", (version,))
        else:
            cursor.execute("DELETE FROM schema_migrations WHERE version = ?;", (version,))
        conn.commit()
        logger.info("Successfully rolled back migration %s.", version)
    except Exception as e:
        conn.rollback()
        logger.error("Failed to rollback migration %s: %s", version, e)
        raise RuntimeError(f"Rollback of {version} failed: {e}") from e
