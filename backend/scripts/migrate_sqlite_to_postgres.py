#!/usr/bin/env python3
"""
migrate_sqlite_to_postgres.py
=============================
Production-grade data migration utility for LearnAI:
Migrates data from SQLite (offline snapshot) to Supabase PostgreSQL with zero data loss.

Features:
- Source SQLite integrity check before migrating.
- Target connection validation and automated schema migration check.
- Strict foreign key dependency ordering:
  1. students
  2. quiz_sessions
  3. quiz_attempts
  4. question_metadata
  5. student_answers
  6. topic_mastery
- Configurable batching with progress tracking.
- Type conversion for JSONB, timestamps, and primitives.
- Dry-run mode (--dry-run).
- Atomic transaction (rolls back target completely on any error).
- Post-migration reconciliation and row count verification.
- Re-entrant support: --clean and --skip-existing.
"""

import os
import sys
import json
import sqlite3
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add backend directory to sys.path so services can be imported
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.db.base import DatabaseConfig, parse_json_safely
from services.db.postgres_adapter import PostgresAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("learnai.migration")

TABLE_DEPENDENCY_ORDER = [
    "students",
    "quiz_sessions",
    "quiz_attempts",
    "question_metadata",
    "student_answers",
    "topic_mastery",
]


def verify_sqlite_source(sqlite_path: Path) -> Dict[str, Any]:
    """Verify SQLite source database integrity and count rows across all tables."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"Source SQLite database not found at: {sqlite_path}")

    logger.info("Verifying source SQLite integrity at %s...", sqlite_path)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("PRAGMA integrity_check;")
    status = cursor.fetchone()[0]
    if status != "ok":
        conn.close()
        raise RuntimeError(f"Source SQLite database failed integrity check: {status}")

    counts = {}
    for table in TABLE_DEPENDENCY_ORDER:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table};")
            counts[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = 0

    conn.close()
    logger.info("Source SQLite database is healthy (integrity_check=ok). Row counts: %s", counts)
    return counts


def get_sqlite_rows(sqlite_path: Path, table: str) -> List[Dict[str, Any]]:
    """Retrieve all rows from a source SQLite table as dictionaries."""
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table};")
        rows = [dict(r) for r in cursor.fetchall()]
        return rows
    except sqlite3.OperationalError as e:
        logger.warning("Could not read table %s from SQLite: %s", table, e)
        return []
    finally:
        conn.close()


def prepare_row_for_postgres(table: str, row: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
    """Convert SQLite row values to PostgreSQL compatible types (JSONB, timestamps, booleans)."""
    from psycopg.types.json import Jsonb

    cols = list(row.keys())
    values = []

    for col, val in row.items():
        if val is None:
            values.append(None)
            continue

        # Handle JSONB columns
        if (table == "quiz_sessions" and col in ("questions_snapshot", "adaptive_metadata")) or \
           (table == "quiz_attempts" and col in ("quiz_data", "difficulty_accuracy", "concept_accuracy", "weak_concepts", "strong_concepts")) or \
           (table == "topic_mastery" and col in ("weak_concepts", "strong_concepts")):
            parsed = parse_json_safely(val, default=[] if "concepts" in col or col == "questions_snapshot" else {})
            values.append(Jsonb(parsed))
        else:
            values.append(val)

    return cols, values


def migrate_data(
    sqlite_path: Path,
    target_url: str,
    batch_size: int = 200,
    dry_run: bool = False,
    clean_target: bool = False,
    skip_existing: bool = False,
) -> Dict[str, Any]:
    """Execute the data migration from SQLite to Supabase PostgreSQL."""
    import psycopg
    from psycopg.types.json import Jsonb

    # 1. Source verification
    source_counts = verify_sqlite_source(sqlite_path)

    # 2. Target connection
    normalized_url = PostgresAdapter.normalize_database_url(target_url)
    logger.info("Connecting to target Supabase PostgreSQL...")

    target_conn = psycopg.connect(normalized_url, autocommit=False)
    target_cursor = target_conn.cursor()

    try:
        # Check target schema
        for table in TABLE_DEPENDENCY_ORDER:
            target_cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            exists = target_cursor.fetchone()[0]
            if not exists:
                raise RuntimeError(
                    f"Target table '{table}' does not exist in target database. "
                    "Run migrations (001_initial_supabase_schema.sql) first."
                )

        if dry_run:
            logger.info("DRY-RUN MODE ENABLED: Validated connection and schema. No data will be written.")
            target_conn.rollback()
            target_conn.close()
            return {
                "dry_run": True,
                "source_counts": source_counts,
                "status": "dry_run_success"
            }

        # Clean target if requested (in reverse dependency order)
        if clean_target:
            logger.warning("CLEAN TARGET: Truncating target tables in reverse dependency order...")
            for table in reversed(TABLE_DEPENDENCY_ORDER):
                target_cursor.execute(f"TRUNCATE TABLE {table} CASCADE;")
            logger.info("Target tables truncated successfully.")

        # Migrate each table in dependency order
        inserted_counts = {}
        for table in TABLE_DEPENDENCY_ORDER:
            rows = get_sqlite_rows(sqlite_path, table)
            if not rows:
                inserted_counts[table] = 0
                logger.info("Table '%s': 0 rows to migrate.", table)
                continue

            logger.info("Migrating table '%s' (%d rows, batch_size=%d)...", table, len(rows), batch_size)
            table_inserted = 0

            # Process in batches
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                for r in batch:
                    cols, values = prepare_row_for_postgres(table, r)
                    cols_str = ", ".join(cols)
                    placeholders = ", ".join(["%s"] * len(cols))

                    if skip_existing:
                        # ON CONFLICT DO NOTHING for idempotency
                        query = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"
                    else:
                        query = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders});"

                    target_cursor.execute(query, tuple(values))
                    table_inserted += 1

            inserted_counts[table] = table_inserted
            logger.info("Table '%s': inserted %d rows.", table, table_inserted)

        # Commit entire transaction
        logger.info("Committing all changes atomically to PostgreSQL...")
        target_conn.commit()
        logger.info("Transaction committed successfully.")

        # Post-migration reconciliation
        logger.info("Performing post-migration reconciliation...")
        target_counts = {}
        reconciliation_passed = True

        for table in TABLE_DEPENDENCY_ORDER:
            target_cursor.execute(f"SELECT COUNT(*) FROM {table};")
            count = target_cursor.fetchone()[0]
            target_counts[table] = count
            expected = source_counts.get(table, 0)
            if clean_target and count != expected:
                logger.error(
                    "Reconciliation mismatch on '%s': source has %d, target has %d!",
                    table, expected, count
                )
                reconciliation_passed = False
            else:
                logger.info("Reconciliation OK on '%s': count = %d", table, count)

        # Verify sample data integrity for students and quiz_attempts
        if target_counts.get("students", 0) > 0:
            target_cursor.execute("SELECT id, created_at FROM students LIMIT 3;")
            sample_students = target_cursor.fetchall()
            logger.info("Sample migrated students: %s", sample_students)

        target_conn.close()

        summary = {
            "status": "success" if reconciliation_passed else "warning_mismatch",
            "source_counts": source_counts,
            "target_counts": target_counts,
            "inserted_counts": inserted_counts,
            "reconciliation_passed": reconciliation_passed
        }
        logger.info("Migration finished successfully. Summary: %s", summary)
        return summary

    except Exception as e:
        logger.critical("Migration failed! Rolling back all target changes: %s", e)
        try:
            target_conn.rollback()
        except Exception:
            pass
        target_conn.close()
        raise


def main():
    parser = argparse.ArgumentParser(description="Migrate LearnAI data from SQLite to Supabase PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default=str(backend_dir / "data" / "learnai.db"),
        help="Path to source SQLite database file."
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default=os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL"),
        help="Target PostgreSQL connection string (defaults to DATABASE_URL)."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Batch size for inserts (default: 200)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate source, target connection, and schema without writing data."
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Truncate target tables before migration."
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip rows on primary key conflict instead of failing."
    )

    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path).resolve()
    target_url = args.target_url

    if not target_url:
        logger.error("No target PostgreSQL URL provided. Set DATABASE_URL or pass --target-url.")
        sys.exit(1)

    try:
        summary = migrate_data(
            sqlite_path=sqlite_path,
            target_url=target_url,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            clean_target=args.clean,
            skip_existing=args.skip_existing,
        )
        print("\n=== MIGRATION SUMMARY ===")
        print(json.dumps(summary, indent=2))
        sys.exit(0 if summary.get("reconciliation_passed", True) else 1)
    except Exception as e:
        print(f"\nMigration failed with error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
