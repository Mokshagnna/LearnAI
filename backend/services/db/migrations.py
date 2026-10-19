"""
migrations.py
=============
Schema migration runner for MySQL, Supabase PostgreSQL, and SQLite.
Discovers versioned SQL files, manages schema_migrations table, and provides idempotent upgrades.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("learnai.db.migrations")


def get_migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "migrations"


def split_sql_statements(sql: str) -> List[str]:
    """
    Split a SQL script into individual executable statements by semicolon,
    correctly handling single/double-quoted strings and line/block comments.
    """
    statements = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]
        next_c = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if c == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if c == "*" and next_c == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if not in_single_quote and not in_double_quote:
            if c == "-" and next_c == "-":
                in_line_comment = True
                i += 2
                continue
            if c == "/" and next_c == "*":
                in_block_comment = True
                i += 2
                continue

        if c == "'" and not in_double_quote:
            if in_single_quote and next_c == "'":
                current.append("''")
                i += 2
                continue
            in_single_quote = not in_single_quote
            current.append(c)
            i += 1
            continue

        if c == '"' and not in_single_quote:
            if in_double_quote and next_c == '"':
                current.append('""')
                i += 2
                continue
            in_double_quote = not in_double_quote
            current.append(c)
            i += 1
            continue

        if c == ";" and not in_single_quote and not in_double_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(c)
        i += 1

    rem = "".join(current).strip()
    if rem:
        statements.append(rem)

    return statements


def _detect_dialect(conn: Any, is_postgres: bool = True, dialect: Optional[str] = None) -> str:
    """Infer dialect string ('mysql', 'postgres', or 'sqlite')."""
    if dialect:
        return dialect.lower()
    if type(conn).__name__ == "MySQLConnectionWrapper" or getattr(conn, "dialect", None) == "mysql":
        return "mysql"
    if is_postgres:
        return "postgres"
    return "sqlite"


def ensure_migration_table(conn: Any, is_postgres: bool = True, dialect: Optional[str] = None):
    """Ensure the schema_migrations tracking table exists."""
    eff_dialect = _detect_dialect(conn, is_postgres=is_postgres, dialect=dialect)
    cursor = conn.cursor()
    if eff_dialect == "mysql":
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                name VARCHAR(255) DEFAULT '',
                applied_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
    elif eff_dialect == "postgres":
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                applied_at TIMESTAMPTZ NOT NULL
            );
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
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


def apply_migrations(
    conn: Any,
    is_postgres: bool = True,
    dialect: Optional[str] = None,
    migrations_dir: Optional[Path] = None
) -> List[str]:
    """
    Discover and apply unapplied SQL migration files in ascending order.
    Returns list of newly applied migration versions.
    """
    mdir = migrations_dir or get_migrations_dir()
    if not mdir.is_dir():
        logger.warning("Migrations directory not found at %s", mdir)
        return []

    eff_dialect = _detect_dialect(conn, is_postgres=is_postgres, dialect=dialect)
    ensure_migration_table(conn, is_postgres=(eff_dialect == "postgres"), dialect=eff_dialect)
    applied = set(get_applied_migrations(conn))
    newly_applied = []

    if eff_dialect == "mysql":
        sql_files = sorted([f for f in mdir.glob("*.sql") if not f.name.endswith(".rollback.sql") and "mysql" in f.name.lower()])
    elif eff_dialect == "postgres":
        sql_files = sorted([f for f in mdir.glob("*.sql") if not f.name.endswith(".rollback.sql") and "mysql" not in f.name.lower()])
    else:
        sql_files = sorted([f for f in mdir.glob("*.sql") if not f.name.endswith(".rollback.sql") and "mysql" not in f.name.lower() and "supabase" not in f.name.lower()])

    for sql_file in sql_files:
        version = sql_file.stem
        if version in applied:
            continue

        logger.info("Applying migration %s (%s) for %s...", version, sql_file.name, eff_dialect)
        with open(sql_file, "r", encoding="utf-8") as f:
            sql_content = f.read()

        cursor = conn.cursor()
        try:
            # Execute statement by statement for driver compatibility
            statements = split_sql_statements(sql_content)
            for stmt in statements:
                cursor.execute(stmt)

            if eff_dialect == "mysql":
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s);",
                    (version, sql_file.name, now_str)
                )
            elif eff_dialect == "postgres":
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s);",
                    (version, sql_file.name, now_str)
                )
            else:
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                    (version, sql_file.name, now_str)
                )
            conn.commit()
            newly_applied.append(version)
            logger.info("Successfully applied migration %s.", version)
        except Exception as e:
            conn.rollback()
            logger.error("Failed to apply migration %s: %s", version, e)
            raise RuntimeError(f"Migration {version} failed: {e}") from e

    return newly_applied


def rollback_migration(
    conn: Any,
    version: str,
    is_postgres: bool = True,
    dialect: Optional[str] = None,
    migrations_dir: Optional[Path] = None
):
    """
    Execute rollback script for a specific migration version.
    """
    mdir = migrations_dir or get_migrations_dir()
    rollback_file = mdir / f"{version}.rollback.sql"
    if not rollback_file.is_file():
        raise FileNotFoundError(f"Rollback file not found: {rollback_file}")

    eff_dialect = _detect_dialect(conn, is_postgres=is_postgres, dialect=dialect)

    with open(rollback_file, "r", encoding="utf-8") as f:
        sql_content = f.read()

    cursor = conn.cursor()
    try:
        statements = split_sql_statements(sql_content)
        for stmt in statements:
            cursor.execute(stmt)

        if eff_dialect in ("postgres", "mysql"):
            cursor.execute("DELETE FROM schema_migrations WHERE version = %s;", (version,))
        else:
            cursor.execute("DELETE FROM schema_migrations WHERE version = ?;", (version,))
        conn.commit()
        logger.info("Successfully rolled back migration %s.", version)
    except Exception as e:
        conn.rollback()
        logger.error("Failed to rollback migration %s: %s", version, e)
        raise RuntimeError(f"Rollback of {version} failed: {e}") from e
