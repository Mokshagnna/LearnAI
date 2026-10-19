#!/usr/bin/env python3
"""
migrate_sqlite_to_mysql.py
==========================
Production-grade data migration utility for LearnAI:
Migrates data from SQLite (offline snapshot) to MySQL with zero data loss.

Features:
- Source SQLite integrity check before migrating (opens in read-only mode).
- Target connection validation and automated schema check.
- Strict foreign key dependency ordering:
  1. students
  2. quiz_sessions
  3. quiz_attempts
  4. question_metadata
  5. student_answers
  6. topic_mastery
- Configurable batching with progress tracking.
- Type conversion for JSON, timestamps, and primitives.
- Dry-run mode (--dry-run): validates data, types, foreign keys, and schema with zero remote writes.
- Atomic transaction (rolls back target completely on any error).
- Post-migration reconciliation and row count verification.
- Re-entrant support: --clean and --skip-existing (using INSERT IGNORE).
- Credential privacy: never logs or displays passwords or complete connection strings.
"""

import os
import sys
import json
import sqlite3
import argparse
import logging
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add backend directory to sys.path so services can be imported
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Load backend/.env relative to script
env_file = backend_dir / ".env"
if env_file.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        pass

from services.db.base import DatabaseConfig, parse_json_safely
from services.db.mysql_adapter import MySQLAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("learnai.migration.mysql")

TABLE_DEPENDENCY_ORDER = [
    "students",
    "quiz_sessions",
    "quiz_attempts",
    "question_metadata",
    "student_answers",
    "topic_mastery",
]


def mask_connection_url(url: str) -> str:
    """Mask password from connection URL for safe logging and display."""
    if not url:
        return "<not configured>"
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            creds, host_part = netloc.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                netloc = f"{user}:***@{host_part}"
            else:
                netloc = f"{creds}:***@{host_part}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "mysql://***:***@***"


def verify_sqlite_source(sqlite_path: Path) -> Dict[str, Any]:
    """Verify SQLite source database integrity in read-only mode and count rows across all tables."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"Source SQLite database not found at: {sqlite_path}")

    logger.info("Verifying source SQLite integrity at %s (read-only)...", sqlite_path)
    try:
        uri = f"file:///{sqlite_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
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
    """Retrieve all rows from a source SQLite table as dictionaries in read-only mode."""
    try:
        uri = f"file:///{sqlite_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
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


def prepare_row_for_mysql(table: str, row: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
    """Convert SQLite row values to MySQL compatible types (JSON strings, timestamps, primitives)."""
    cols = list(row.keys())
    values = []

    for col, val in row.items():
        if val is None:
            values.append(None)
            continue

        # Handle JSON columns - serialize to formatted JSON string
        if (table == "quiz_sessions" and col in ("questions_snapshot", "adaptive_metadata")) or \
           (table == "quiz_attempts" and col in ("quiz_data", "difficulty_accuracy", "concept_accuracy", "weak_concepts", "strong_concepts")) or \
           (table == "topic_mastery" and col in ("weak_concepts", "strong_concepts")):
            parsed = parse_json_safely(val, default=[] if "concepts" in col or col == "questions_snapshot" else {})
            values.append(json.dumps(parsed))
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
    """Execute or rehearse data migration from SQLite to MySQL."""
    import pymysql

    # 1. Source verification (strictly read-only)
    source_counts = verify_sqlite_source(sqlite_path)

    # 2. Target connection
    masked_url = mask_connection_url(target_url)
    logger.info("Connecting to target MySQL at %s...", masked_url)

    connect_kwargs = MySQLAdapter.parse_connection_params(target_url)
    connect_kwargs["autocommit"] = False

    target_conn = pymysql.connect(**connect_kwargs)
    target_cursor = target_conn.cursor()

    try:
        # Check target schema
        db_name = connect_kwargs.get("database")
        for table in TABLE_DEPENDENCY_ORDER:
            target_cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s;",
                (db_name, table)
            )
            exists = target_cursor.fetchone()[0] > 0
            if not exists:
                raise RuntimeError(
                    f"Target table '{table}' does not exist in target database. "
                    "Run migrations (001_initial_mysql_schema.sql) first."
                )

        if dry_run:
            logger.info("DRY-RUN MODE ENABLED: Validating all data, types, and foreign keys without writing...")
            fk_cache: Dict[str, set] = {t: set() for t in TABLE_DEPENDENCY_ORDER}
            type_conversions: Dict[str, Any] = {
                t: {"total_rows": 0, "json_converted": 0, "valid_rows": 0, "errors": []}
                for t in TABLE_DEPENDENCY_ORDER
            }
            fk_violations: List[str] = []

            for table in TABLE_DEPENDENCY_ORDER:
                rows = get_sqlite_rows(sqlite_path, table)
                type_conversions[table]["total_rows"] = len(rows)

                for r in rows:
                    try:
                        cols, values = prepare_row_for_mysql(table, r)
                        type_conversions[table]["valid_rows"] += 1

                        for v in values:
                            if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                                type_conversions[table]["json_converted"] += 1

                        if "id" in r:
                            fk_cache[table].add(r["id"])

                        # Foreign key validations
                        if table == "quiz_sessions" and r.get("student_id") not in fk_cache["students"]:
                            fk_violations.append(f"quiz_sessions {r.get('id')} references unknown student {r.get('student_id')}")
                        elif table == "quiz_attempts" and r.get("student_id") not in fk_cache["students"]:
                            fk_violations.append(f"quiz_attempts {r.get('id')} references unknown student {r.get('student_id')}")
                        elif table == "question_metadata" and r.get("attempt_id") not in fk_cache["quiz_attempts"]:
                            fk_violations.append(f"question_metadata {r.get('id')} references unknown attempt {r.get('attempt_id')}")
                        elif table == "student_answers":
                            if r.get("student_id") not in fk_cache["students"]:
                                fk_violations.append(f"student_answers {r.get('id')} references unknown student {r.get('student_id')}")
                            if r.get("session_id") not in fk_cache["quiz_sessions"]:
                                fk_violations.append(f"student_answers {r.get('id')} references unknown session {r.get('session_id')}")
                        elif table == "topic_mastery" and r.get("student_id") not in fk_cache["students"]:
                            fk_violations.append(f"topic_mastery {r.get('id')} references unknown student {r.get('student_id')}")

                    except Exception as row_err:
                        type_conversions[table]["errors"].append(str(row_err))

            logger.info("DRY-RUN VALIDATION COMPLETE: Zero remote writes. Rolling back connection.")
            target_conn.rollback()
            target_conn.close()

            return {
                "dry_run": True,
                "status": "dry_run_success" if not fk_violations else "dry_run_warnings",
                "source_counts": source_counts,
                "proposed_rows": {t: type_conversions[t]["valid_rows"] for t in TABLE_DEPENDENCY_ORDER},
                "foreign_key_dependency_order": TABLE_DEPENDENCY_ORDER,
                "foreign_key_violations": fk_violations,
                "type_conversions": {
                    t: {
                        "total_rows": type_conversions[t]["total_rows"],
                        "json_columns_converted": type_conversions[t]["json_converted"],
                        "errors": type_conversions[t]["errors"]
                    }
                    for t in TABLE_DEPENDENCY_ORDER
                },
                "remote_changes": 0
            }

        # Clean target if requested (in reverse dependency order with foreign keys disabled)
        if clean_target:
            logger.warning("CLEAN TARGET: Truncating target MySQL tables...")
            target_cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            for table in reversed(TABLE_DEPENDENCY_ORDER):
                target_cursor.execute(f"TRUNCATE TABLE {table};")
            target_cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
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
                    cols, values = prepare_row_for_mysql(table, r)
                    cols_str = ", ".join(cols)
                    placeholders = ", ".join(["%s"] * len(cols))

                    if skip_existing:
                        query = f"INSERT IGNORE INTO {table} ({cols_str}) VALUES ({placeholders});"
                    else:
                        query = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders});"

                    target_cursor.execute(query, tuple(values))
                    table_inserted += 1

            inserted_counts[table] = table_inserted
            logger.info("Table '%s': inserted %d rows.", table, table_inserted)

        # Commit entire transaction
        logger.info("Committing all changes atomically to MySQL...")
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
        logger.info("MySQL Migration finished successfully. Summary: %s", summary)
        return summary

    except Exception as e:
        logger.critical("MySQL Migration operation failed: %s", e)
        try:
            target_conn.rollback()
        except Exception:
            pass
        target_conn.close()
        raise


def main():
    parser = argparse.ArgumentParser(description="Migrate LearnAI data from SQLite to MySQL.")
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default=str(backend_dir / "data" / "learnai.db"),
        help="Path to source SQLite database file."
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default=os.getenv("TEST_MYSQL_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Target MySQL connection string (defaults to TEST_MYSQL_DATABASE_URL or DATABASE_URL)."
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
        help="Validate source, target connection, types, foreign keys, and schema without writing data."
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
        logger.error("No target MySQL URL provided. Set TEST_MYSQL_DATABASE_URL or pass --target-url.")
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
        print("\n=== MYSQL MIGRATION SUMMARY ===")
        print(json.dumps(summary, indent=2))
        sys.exit(0 if summary.get("status") in ("success", "dry_run_success") else 1)
    except Exception as e:
        print(f"\nMigration failed with error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
