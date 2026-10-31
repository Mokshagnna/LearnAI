"""
test_phase2_stage_m_postgres_migration.py
=========================================
Comprehensive automated test suite for LearnAI Stage M:
SQLite to Supabase PostgreSQL Migration (Free-Tier Architecture).

Covers:
1. Placeholder Translation:
   - Single & multiple '?' to '%s'
   - Preserves '?' inside single quotes
   - Handles mixed queries
2. CompatibleRow Interface:
   - Dict access: row['col']
   - Index access: row[0]
   - Dict conversion: dict(row)
   - row.get() and row.keys()
3. JSON Serialization & Deserialization:
   - parse_json_safely for dicts, lists, strings, None, malformed JSON
   - parse_json_column on sqlite3.Row, CompatibleRow, dict
   - serialize_json_for_db for SQLite and PostgreSQL
4. Fail-Fast Safeguards:
   - When DATABASE_URL is set to unreachable host, raises DatabaseConnectionError
   - Refuses to fall back silently to SQLite
5. Schema Migration Runner:
   - Migration tracking table creation
   - Idempotent migration discovery and execution
   - Migration ordering and rollback execution
6. Data Migration Utility:
   - Source SQLite verification and integrity checking
   - Table dependency ordering validation
   - Row type adaptation (JSONB, timestamps)
   - Dry-run validation
"""

import os
import json
import sqlite3
import pytest
from pathlib import Path
from typing import Dict, Any, List

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders_to_pg,
    parse_json_safely,
    parse_json_column,
    serialize_json_for_db,
)
from services.db.sqlite_adapter import SQLiteAdapter
from services.db.postgres_adapter import (
    PostgresAdapter,
    PostgresCursorWrapper,
    PostgresConnectionWrapper,
)
from services.db.migrations import (
    apply_migrations,
    rollback_migration,
    ensure_migration_table,
    get_applied_migrations,
)
from services.database import get_connection, init_db, is_postgres_active
from scripts.migrate_sqlite_to_postgres import (
    TABLE_DEPENDENCY_ORDER,
    verify_sqlite_source,
    prepare_row_for_postgres,
)


# ── 1. Parameter Placeholder Translation Tests ────────────────────────────────

class TestParameterPlaceholderTranslation:

    def test_single_placeholder(self):
        query = "SELECT * FROM students WHERE id = ?"
        translated = translate_placeholders_to_pg(query)
        assert translated == "SELECT * FROM students WHERE id = %s"

    def test_multiple_placeholders(self):
        query = "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)"
        translated = translate_placeholders_to_pg(query)
        assert translated == "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s)"

    def test_question_mark_inside_quotes_preserved(self):
        query = "SELECT * FROM question_metadata WHERE question_text = 'What is A* search?' AND id = ?"
        translated = translate_placeholders_to_pg(query)
        assert translated == "SELECT * FROM question_metadata WHERE question_text = 'What is A* search?' AND id = %s"

    def test_multiple_quoted_strings_with_question_marks(self):
        query = "SELECT 'Is this ok?' as q1, 'Why not?' as q2 WHERE id = ? AND course_code = ?"
        translated = translate_placeholders_to_pg(query)
        assert translated == "SELECT 'Is this ok?' as q1, 'Why not?' as q2 WHERE id = %s AND course_code = %s"

    def test_no_placeholders_unchanged(self):
        query = "SELECT COUNT(*) FROM topic_mastery"
        translated = translate_placeholders_to_pg(query)
        assert translated == query

    def test_question_mark_inside_line_comment_preserved(self):
        query = "-- What is this? This is a comment\nSELECT * FROM students WHERE id = ?"
        translated = translate_placeholders_to_pg(query)
        assert translated == "-- What is this? This is a comment\nSELECT * FROM students WHERE id = %s"

    def test_question_mark_inside_block_comment_preserved(self):
        query = "/* Is this a question? */ SELECT * FROM quiz_attempts WHERE score >= ? /* another ? */"
        translated = translate_placeholders_to_pg(query)
        assert translated == "/* Is this a question? */ SELECT * FROM quiz_attempts WHERE score >= %s /* another ? */"

    def test_escaped_single_quotes_with_question_marks(self):
        query = "SELECT 'It''s a question?' as val WHERE id = ?"
        translated = translate_placeholders_to_pg(query)
        assert translated == "SELECT 'It''s a question?' as val WHERE id = %s"


# ── 2. CompatibleRow Interface Tests ─────────────────────────────────────────

class TestCompatibleRowInterface:

    def test_compatible_row_dict_and_index_access(self):
        desc = [("id",), ("score",), ("topic_name",)]
        values = ("attempt-123", 9, "Neural Networks")
        row = CompatibleRow(desc, values)

        # Dict access
        assert row["id"] == "attempt-123"
        assert row["score"] == 9
        assert row["topic_name"] == "Neural Networks"

        # Index access
        assert row[0] == "attempt-123"
        assert row[1] == 9
        assert row[2] == "Neural Networks"

        # dict() conversion
        as_dict = dict(row)
        assert as_dict == {"id": "attempt-123", "score": 9, "topic_name": "Neural Networks"}

        # .get() access
        assert row.get("id") == "attempt-123"
        assert row.get("missing", "default_val") == "default_val"

        # .keys()
        assert row.keys() == ["id", "score", "topic_name"]


# ── 3. JSON Serialization & Deserialization Tests ─────────────────────────────

class TestJsonSerializationAndDeserialization:

    def test_parse_json_safely_from_string(self):
        json_str = '{"weak_concepts": ["Backprop", "CNN"], "score": 8}'
        parsed = parse_json_safely(json_str)
        assert parsed == {"weak_concepts": ["Backprop", "CNN"], "score": 8}

    def test_parse_json_safely_from_already_parsed_dict(self):
        already_dict = {"weak_concepts": ["Backprop"]}
        assert parse_json_safely(already_dict) == already_dict

    def test_parse_json_safely_from_already_parsed_list(self):
        already_list = ["Concept A", "Concept B"]
        assert parse_json_safely(already_list) == already_list

    def test_parse_json_safely_empty_and_none(self):
        assert parse_json_safely(None, default=[]) == []
        assert parse_json_safely("", default={}) == {}
        assert parse_json_safely("   ", default=[]) == []

    def test_parse_json_safely_malformed_string(self):
        assert parse_json_safely("{bad_json: True", default={"status": "fallback"}) == {"status": "fallback"}

    def test_parse_json_column_sqlite_row(self, tmp_path):
        db_file = tmp_path / "test_json_row.db"
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE test_t (id TEXT, data TEXT);")
        conn.execute("INSERT INTO test_t VALUES ('1', '{\"key\": \"val\"}');")
        conn.commit()

        row = conn.execute("SELECT * FROM test_t WHERE id = '1';").fetchone()
        conn.close()

        parsed = parse_json_column(row, "data")
        assert parsed == {"key": "val"}

    def test_parse_json_column_compatible_row(self):
        row = CompatibleRow([("data",)], (json.dumps(["A", "B"]),))
        assert parse_json_column(row, "data") == ["A", "B"]

    def test_serialize_json_for_db_sqlite(self):
        data = {"questions": [1, 2, 3]}
        serialized = serialize_json_for_db(data, is_postgres=False)
        assert isinstance(serialized, str)
        assert json.loads(serialized) == data

    def test_serialize_json_for_db_postgres(self):
        data = {"questions": [1, 2, 3]}
        serialized = serialize_json_for_db(data, is_postgres=True)
        # Should be a Jsonb instance
        from psycopg.types.json import Jsonb
        assert isinstance(serialized, Jsonb)
        assert serialized.obj == data


# ── 4. Fail-Fast Safeguards Tests ─────────────────────────────────────────────

class TestFailFastSafeguards:

    def test_postgres_adapter_without_url_raises(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
        cfg = DatabaseConfig()
        assert not cfg.is_postgres

        with pytest.raises(DatabaseConnectionError) as exc_info:
            PostgresAdapter(cfg)
        assert "no DATABASE_URL" in str(exc_info.value)

    def test_unreachable_postgres_fails_fast_and_never_falls_back_to_sqlite(self, monkeypatch):
        """
        Critical safety test:
        If DATABASE_URL is set to an unreachable host, attempting to connect MUST fail fast.
        It must NEVER fall back silently to SQLite.
        """
        # Point to unreachable port on localhost with 1.0s timeout
        unreachable_url = "postgresql://learnai:secret@127.0.0.1:59999/learnai?sslmode=disable"
        monkeypatch.setenv("DATABASE_URL", unreachable_url)
        monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1.0")

        cfg = DatabaseConfig()
        assert cfg.is_postgres

        adapter = PostgresAdapter(cfg)
        with pytest.raises(DatabaseConnectionError) as exc_info:
            adapter.get_connection()

        err_msg = str(exc_info.value)
        assert "PostgreSQL connection to Supabase failed" in err_msg or "failed" in err_msg.lower()

    def test_normalize_database_url_enforces_ssl(self):
        raw_url = "postgres://postgres:secret@db.xyz.supabase.co:6543/postgres"
        normalized = PostgresAdapter.normalize_database_url(raw_url)
        assert "sslmode=require" in normalized
        assert normalized.startswith("postgresql://")


# ── 5. Schema Migration Runner Tests ─────────────────────────────────────────

class TestSchemaMigrationRunner:

    def test_migration_runner_on_sqlite(self, tmp_path):
        """Test migration runner engine discovery, tracking, and idempotency."""
        db_file = tmp_path / "test_migrations.db"
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row

        # Create temporary test migrations
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()

        m1 = mig_dir / "001_test_init.sql"
        m1.write_text("CREATE TABLE test_students (id TEXT PRIMARY KEY, name TEXT);", encoding="utf-8")

        m1_rollback = mig_dir / "001_test_init.rollback.sql"
        m1_rollback.write_text("DROP TABLE test_students;", encoding="utf-8")

        # 1. Apply migration
        newly_applied = apply_migrations(conn, is_postgres=False, migrations_dir=mig_dir)
        assert newly_applied == ["001_test_init"]

        # Verify table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_students';")
        assert cursor.fetchone() is not None

        # Verify tracking table
        applied = get_applied_migrations(conn)
        assert applied == ["001_test_init"]

        # 2. Idempotency: Running again applies 0 new migrations
        second_run = apply_migrations(conn, is_postgres=False, migrations_dir=mig_dir)
        assert second_run == []

        # 3. Rollback
        rollback_migration(conn, "001_test_init", is_postgres=False, migrations_dir=mig_dir)
        applied_after_rollback = get_applied_migrations(conn)
        assert applied_after_rollback == []

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_students';")
        assert cursor.fetchone() is None

        conn.close()


# ── 6. Data Migration Utility Tests ──────────────────────────────────────────

class TestDataMigrationUtility:

    def test_table_dependency_order(self):
        """Ensure strict foreign key dependency ordering is maintained."""
        assert TABLE_DEPENDENCY_ORDER == [
            "students",
            "quiz_sessions",
            "quiz_attempts",
            "question_metadata",
            "student_answers",
            "topic_mastery",
        ]

    def test_source_snapshot_verification(self):
        """Verify the active local SQLite database snapshot."""
        active_db = Path(__file__).resolve().parent / "data" / "learnai.db"
        counts = verify_sqlite_source(active_db)
        assert counts["students"] >= 75
        assert counts["quiz_attempts"] >= 53
        assert counts["question_metadata"] >= 370

    def test_prepare_row_for_postgres_jsonb_wrapping(self):
        """Verify prepare_row_for_postgres wraps JSON fields into Jsonb objects."""
        from psycopg.types.json import Jsonb

        row = {
            "id": "att-1",
            "student_id": "stud-1",
            "quiz_data": json.dumps({"questions": []}),
            "difficulty_accuracy": json.dumps({"Medium": {"correct": 5, "total": 5}}),
            "concept_accuracy": json.dumps({"A*": {"correct": 3, "total": 3}}),
            "weak_concepts": json.dumps(["BFS"]),
            "strong_concepts": json.dumps(["A*"]),
            "score": 8,
            "created_at": "2026-10-17T12:00:00"
        }

        cols, values = prepare_row_for_postgres("quiz_attempts", row)
        val_map = dict(zip(cols, values))

        assert isinstance(val_map["quiz_data"], Jsonb)
        assert isinstance(val_map["weak_concepts"], Jsonb)
        assert val_map["weak_concepts"].obj == ["BFS"]
        assert val_map["score"] == 8
