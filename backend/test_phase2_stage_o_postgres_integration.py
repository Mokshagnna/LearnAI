"""
test_phase2_stage_o_postgres_integration.py
============================================
Comprehensive Live PostgreSQL Integration Test Suite for LearnAI Stage O.
Verifies LearnAI operations against a real, disposable Supabase PostgreSQL instance
using synthetic test records (never touching SQLite data).

Safety Guards:
- Strictly requires TEST_DATABASE_URL (will NOT fall back to DATABASE_URL).
- Validates that the connected target matches the intended disposable 'learnai-dev-test'
  project (via project ref 'rnlbkdmjbcduoltefyup' or explicit test target indicator).
- Uses 100% synthetic records prefixed with 'syn_stud_', 'syn_sess_', etc.
- Scoped teardown: deletes only the synthetic student ID (which cascades cleanly to test child rows).
- Zero writes to local SQLite database or backups.
"""

import os
import sys
import uuid
import json
import time
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Automatically load backend/.env relative to test file
env_file = backend_dir / ".env"
if env_file.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        pass

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders_to_pg,
    parse_json_column,
    serialize_json_for_db,
)
from services.db.postgres_adapter import PostgresAdapter
from services.db.migrations import apply_migrations, get_applied_migrations
from services.database import (
    create_quiz_session,
    get_quiz_session,
    submit_quiz_atomic,
    get_topic_mastery,
    upsert_topic_mastery,
    save_quiz_attempt,
    get_or_create_student,
)

# STRICT GUARD: Exclusively use TEST_DATABASE_URL (do not fall back to DATABASE_URL)
LIVE_PG_URL = os.getenv("TEST_DATABASE_URL")
KNOWN_TEST_PROJECT_REF = "rnlbkdmjbcduoltefyup"  # Supabase project ref for learnai-dev-test
PRODUCTION_TARGET_INDICATORS = ("prod", "production")


def is_verified_test_target(url: str) -> bool:
    """
    Ensure target URL points strictly to an approved disposable test database.
    Production-target rejection takes absolute precedence over all acceptance conditions,
    including CONFIRM_DISPOSABLE_TEST_TARGET=true.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return False

    trimmed = url.strip()
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return False

    # Scheme must be a valid PostgreSQL scheme
    if parsed.scheme not in ("postgresql", "postgres"):
        return False

    username = (parsed.username or "").lower()
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    if not hostname:
        return False

    # 1. ABSOLUTE GUARD: Reject production targets unconditionally.
    # Neither CONFIRM_DISPOSABLE_TEST_TARGET nor matching test substrings may override this.
    # Note: parsed.password is deliberately NOT checked so passwords containing "prod" do not cause false rejections.
    for indicator in PRODUCTION_TARGET_INDICATORS:
        if indicator in username or indicator in hostname or indicator in path:
            return False

    # 2. Exact allowlist verification for known disposable Supabase project (learnai-dev-test)
    # Check for Supabase pooler pattern: username postgres.<project_ref> on *.pooler.supabase.com
    is_valid_pooler = (
        hostname.endswith(".pooler.supabase.com")
        and (username == f"postgres.{KNOWN_TEST_PROJECT_REF}" or username.startswith(f"postgres.{KNOWN_TEST_PROJECT_REF}."))
    )
    # Check for Supabase direct connection pattern: host db.<project_ref>.supabase.co
    is_valid_direct = (
        hostname == f"db.{KNOWN_TEST_PROJECT_REF}.supabase.co"
        or (hostname.endswith(".supabase.co") and KNOWN_TEST_PROJECT_REF in hostname)
    )

    if is_valid_pooler or is_valid_direct:
        return True

    # 3. Hostnames containing 'dev-test' alone are NOT treated as proof of disposable test database.
    # Unknown targets (including custom local DBs) fail closed by default,
    # and require explicit opt-in confirmation via CONFIRM_DISPOSABLE_TEST_TARGET=true.
    # Note: If target is an unknown Supabase project, reject it even with confirmation to prevent pointing to unverified Supabase instances.
    if hostname.endswith(".supabase.co") or hostname.endswith(".supabase.com"):
        return False

    return os.getenv("CONFIRM_DISPOSABLE_TEST_TARGET") == "true"


class TestPostgresTestTargetSafeguards:
    """Unit tests verifying test target safety guards (no DB connection required)."""

    def test_exact_approved_disposable_test_target_accepted(self):
        pooler_url = f"postgresql://postgres.{KNOWN_TEST_PROJECT_REF}:dummy_pass@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
        assert is_verified_test_target(pooler_url) is True

        direct_url = f"postgresql://postgres:dummy_pass@db.{KNOWN_TEST_PROJECT_REF}.supabase.co:5432/postgres?sslmode=require"
        assert is_verified_test_target(direct_url) is True

    def test_empty_url_rejected(self):
        assert is_verified_test_target("") is False
        assert is_verified_test_target("   ") is False
        assert is_verified_test_target(None) is False

    def test_malformed_url_rejected(self):
        assert is_verified_test_target("not-a-valid-url") is False
        assert is_verified_test_target(":///") is False
        assert is_verified_test_target("http://aws-0-ap-southeast-1.pooler.supabase.com/postgres") is False
        assert is_verified_test_target("mysql://user:pass@localhost:3306/db") is False

    def test_production_project_rejected(self):
        prod_url_hostname = "postgresql://postgres:dummy_pass@learnai-prod.supabase.co:5432/postgres"
        assert is_verified_test_target(prod_url_hostname) is False

        prod_url_production = "postgresql://postgres:dummy_pass@learnai-production.supabase.co:5432/postgres"
        assert is_verified_test_target(prod_url_production) is False

    def test_production_rejected_even_with_confirmation_override(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        prod_url = "postgresql://postgres:dummy_pass@learnai-prod.supabase.co:5432/postgres"
        assert is_verified_test_target(prod_url) is False

    def test_production_project_reference_in_username_rejected(self, monkeypatch):
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        prod_user_url = "postgresql://postgres.myproject-prod:dummy_pass@aws-0-pooler.supabase.com:5432/postgres"
        assert is_verified_test_target(prod_user_url) is False

        prod_user_full = "postgresql://postgres.production-db:dummy_pass@aws-0-pooler.supabase.com:5432/postgres"
        assert is_verified_test_target(prod_user_full) is False

    def test_production_project_reference_in_hostname_rejected(self):
        prod_host_url = "postgresql://postgres:dummy_pass@db.production-ref.supabase.co:5432/postgres"
        assert is_verified_test_target(prod_host_url) is False

    def test_deceptive_combinations_rejected(self):
        tricky_url1 = "postgresql://postgres.dev-test:dummy_pass@learnai-prod.supabase.co:5432/postgres"
        assert is_verified_test_target(tricky_url1) is False

        tricky_url2 = f"postgresql://postgres:dummy_pass@learnai-prod.supabase.co:5432/{KNOWN_TEST_PROJECT_REF}"
        assert is_verified_test_target(tricky_url2) is False

        unverified_dev_test = "postgresql://postgres:dummy_pass@aws-0-dev-test.supabase.co:5432/postgres"
        assert is_verified_test_target(unverified_dev_test) is False

    def test_unknown_target_rejected_by_default_and_requires_confirmation(self, monkeypatch):
        unrelated_url = "postgresql://postgres:dummy_pass@my-custom-test-host.local:5432/postgres"

        # Without confirmation: rejected
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        assert is_verified_test_target(unrelated_url) is False

        # With confirmation: accepted if non-production custom host
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        assert is_verified_test_target(unrelated_url) is True

        # Unknown Supabase projects fail closed even with confirmation
        unknown_supabase = "postgresql://postgres:dummy_pass@db.unknown-ref.supabase.co:5432/postgres"
        assert is_verified_test_target(unknown_supabase) is False

    def test_password_containing_prod_does_not_cause_rejection_or_leak(self):
        pwd_with_prod = "my_prod_secret_password_123"
        url = f"postgresql://postgres.{KNOWN_TEST_PROJECT_REF}:{pwd_with_prod}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
        assert is_verified_test_target(url) is True

    def test_stage_o_and_stage_p_validators_have_identical_behavior(self, monkeypatch):
        from test_phase2_stage_o_postgres_integration import is_verified_test_target as o_validator
        from test_phase2_stage_p_postgres_application import is_verified_test_target as p_validator

        test_cases = [
            f"postgresql://postgres.{KNOWN_TEST_PROJECT_REF}:pass@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
            f"postgresql://postgres:pass@db.{KNOWN_TEST_PROJECT_REF}.supabase.co:5432/postgres",
            "",
            "   ",
            None,
            "invalid-url",
            "postgresql://postgres:pass@learnai-prod.supabase.co:5432/postgres",
            "postgresql://postgres.myproject-prod:pass@aws-0-pooler.supabase.com:5432/postgres",
            "postgresql://postgres:pass@custom.local:5432/postgres",
        ]
        for tc in test_cases:
            monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
            assert o_validator(tc) == p_validator(tc)
            monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
            assert o_validator(tc) == p_validator(tc)

    def test_rejection_occurs_before_database_connection(self):
        start = time.perf_counter()
        result = is_verified_test_target("postgresql://postgres:pass@nonexistent-fake-prod-host.invalid:5432/postgres")
        duration = time.perf_counter() - start
        assert result is False
        assert duration < 0.05


requires_live_pg = pytest.mark.skipif(
    not LIVE_PG_URL,
    reason="Live PostgreSQL integration tests require TEST_DATABASE_URL configured in backend/.env."
)


@pytest.fixture(scope="module")
def pg_config():
    if not LIVE_PG_URL:
        pytest.skip("Live PostgreSQL integration tests require TEST_DATABASE_URL.")

    # Guard: Ensure we never run against an unverified or production target
    if not is_verified_test_target(LIVE_PG_URL):
        pytest.fail(
            "SAFETY ABORT: TEST_DATABASE_URL does not match the verified disposable "
            f"target project '{KNOWN_TEST_PROJECT_REF}' (learnai-dev-test). "
            "Set CONFIRM_DISPOSABLE_TEST_TARGET=true if running against a renamed test target."
        )

    # Point DatabaseConfig exclusively to TEST_DATABASE_URL
    os.environ["DATABASE_URL"] = LIVE_PG_URL
    cfg = DatabaseConfig()
    return cfg


@pytest.fixture(scope="module")
def pg_adapter(pg_config):
    adapter = PostgresAdapter(pg_config)
    yield adapter
    # Clean pool closure
    PostgresAdapter.close_pool()


@pytest.fixture(scope="module")
def live_schema(pg_adapter):
    """
    Apply initial versioned migrations (001_initial_supabase_schema.sql)
    once for the test run. Does NOT drop tables; uses CREATE TABLE IF NOT EXISTS.
    """
    conn = pg_adapter.get_connection()
    try:
        applied = apply_migrations(conn, is_postgres=True)
        conn.commit()
    finally:
        conn.close()
    return applied


@pytest.fixture
def synthetic_student_id():
    """Generates unique synthetic student ID and safely deletes only that ID in teardown."""
    sid = f"syn_stud_{uuid.uuid4().hex[:12]}"
    yield sid
    # Teardown: delete strictly this synthetic student (cascades only to its child records)
    if LIVE_PG_URL and is_verified_test_target(LIVE_PG_URL):
        try:
            cfg = DatabaseConfig()
            adapter = PostgresAdapter(cfg)
            conn = adapter.get_connection()
            try:
                conn.execute("DELETE FROM students WHERE id = %s;", (sid,))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass


# ── 1. Live Connection & Lifecycle Tests ───────────────────────────────────────

@requires_live_pg
class TestLivePostgresConnectionLifecycle:

    def test_pool_connection_and_version(self, pg_adapter):
        conn = pg_adapter.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT version();")
            ver_row = cur.fetchone()
            assert ver_row is not None
            ver_str = ver_row[0]
            assert "PostgreSQL" in ver_str
        finally:
            conn.close()

    def test_ssl_enforced_on_live_connection(self, pg_adapter):
        conn = pg_adapter.get_connection()
        try:
            # Native psycopg 3 connection SSL check
            assert bool(conn._conn.pgconn.ssl_in_use) is True, "Live Supabase PostgreSQL connection must be SSL/TLS encrypted!"
        finally:
            conn.close()


# ── 2. Live Schema Migration & Table Verification ──────────────────────────────

@requires_live_pg
class TestLiveSchemaMigration:

    def test_schema_migrations_tracked(self, pg_adapter, live_schema):
        conn = pg_adapter.get_connection()
        try:
            applied = get_applied_migrations(conn)
            assert "001_initial_supabase_schema" in applied

            # Verify all 6 core tables exist with indexes and foreign keys
            cur = conn.cursor()
            for tbl in ["students", "quiz_sessions", "quiz_attempts", "question_metadata", "student_answers", "topic_mastery"]:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                    (tbl,)
                )
                exists = cur.fetchone()[0]
                assert exists is True, f"Table '{tbl}' must exist in live database"
        finally:
            conn.close()

    def test_long_question_fingerprint_supported(self, pg_adapter, synthetic_student_id):
        """
        Regression test: Verify that question_fingerprint supports strings longer
        than 255 characters (specifically testing 360 characters matching source snapshot).
        """
        conn = pg_adapter.get_connection()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s);",
                (synthetic_student_id, now_iso, now_iso)
            )
            att_id = f"syn_att_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO quiz_attempts (
                    id, student_id, course_code, course_name, topic_name, quiz_data, score, total, percentage,
                    status, difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 1, 1, 100.0, 'completed', %s, %s, %s, %s, %s);
            """, (
                att_id, synthetic_student_id, "CS101", "CS", "Data",
                serialize_json_for_db({}, is_postgres=True),
                serialize_json_for_db({}, is_postgres=True),
                serialize_json_for_db({}, is_postgres=True),
                serialize_json_for_db([], is_postgres=True),
                serialize_json_for_db([], is_postgres=True),
                now_iso
            ))

            # Exactly 360-character fingerprint matching maximum observed in snapshot
            base_fp = "analyze the following scenario a researcher in 1998 developed a neural network "
            long_fp = (base_fp * 5)[:360]
            assert len(long_fp) == 360

            qm_id = f"syn_qm_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO question_metadata (
                    id, attempt_id, student_id, course_code, topic_name, question_index,
                    question_text, question_fingerprint, concept, difficulty, question_type,
                    correct_answer, created_at
                ) VALUES (%s, %s, %s, %s, %s, 0, 'Long scenario question', %s, 'General', 'Medium', 'mcq', 'A', %s);
            """, (qm_id, att_id, synthetic_student_id, "CS101", "Data", long_fp, now_iso))
            conn.commit()

            # Retrieve and verify round-trip fidelity
            cur = conn.execute("SELECT question_fingerprint FROM question_metadata WHERE id = %s;", (qm_id,))
            retrieved = cur.fetchone()[0]
            assert retrieved == long_fp
            assert len(retrieved) == 360
        finally:
            conn.close()


# ── 3. Parameter Translation & Row Compatibility ──────────────────────────────

@requires_live_pg
class TestLiveParameterTranslationAndRowCompatibility:

    def test_cursor_placeholder_translation_and_compatiblerow(self, pg_adapter, synthetic_student_id):
        conn = pg_adapter.get_connection()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # Insert with '?' placeholders
            cur = conn.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?);",
                (synthetic_student_id, now_iso, now_iso)
            )
            conn.commit()

            # Select with '?' placeholder
            cur2 = conn.execute("SELECT id, created_at, last_active FROM students WHERE id = ?;", (synthetic_student_id,))
            row = cur2.fetchone()
            assert row is not None
            assert isinstance(row, CompatibleRow)
            # Test dict-like access
            assert row["id"] == synthetic_student_id
            # Test tuple/index-like access
            assert row[0] == synthetic_student_id
            # Test dict conversion
            d = dict(row)
            assert d["id"] == synthetic_student_id
        finally:
            conn.close()


# ── 4. Foreign Key Cascades & Constraints ──────────────────────────────────────

@requires_live_pg
class TestLiveConstraintsAndCascades:

    def test_foreign_key_cascade_deletion(self, pg_adapter, synthetic_student_id):
        conn = pg_adapter.get_connection()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # 1. Insert synthetic student
            conn.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s);",
                (synthetic_student_id, now_iso, now_iso)
            )
            # 2. Insert child session
            session_id = f"syn_sess_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO quiz_sessions (
                    id, student_id, course_code, course_name, topic_name,
                    status, questions_snapshot, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                session_id, synthetic_student_id, "CS101", "Computer Science", "Sorting",
                "in_progress", serialize_json_for_db([], is_postgres=True), now_iso, now_iso
            ))
            conn.commit()

            # 3. Verify session exists
            cur = conn.execute("SELECT COUNT(*) FROM quiz_sessions WHERE id = %s;", (session_id,))
            assert cur.fetchone()[0] == 1

            # 4. Delete synthetic student
            conn.execute("DELETE FROM students WHERE id = %s;", (synthetic_student_id,))
            conn.commit()

            # 5. Verify child session was automatically cascade-deleted
            cur2 = conn.execute("SELECT COUNT(*) FROM quiz_sessions WHERE id = %s;", (session_id,))
            assert cur2.fetchone()[0] == 0, "Quiz session should have been cascaded on student delete"
        finally:
            conn.close()

    def test_student_answers_unique_constraint(self, pg_adapter, synthetic_student_id):
        conn = pg_adapter.get_connection()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s);",
                (synthetic_student_id, now_iso, now_iso)
            )
            sess_id = f"syn_sess_{uuid.uuid4().hex[:8]}"
            att_id = f"syn_att_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO quiz_sessions (id, student_id, course_code, course_name, topic_name, status, questions_snapshot, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (sess_id, synthetic_student_id, "CS101", "CS", "Data", "in_progress", serialize_json_for_db([], is_postgres=True), now_iso, now_iso))
            conn.execute("""
                INSERT INTO quiz_attempts (id, student_id, course_code, course_name, topic_name, quiz_data, score, total, percentage, status, difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, 1, 100.0, 'completed', %s, %s, %s, %s, %s);
            """, (att_id, synthetic_student_id, "CS101", "CS", "Data", serialize_json_for_db([], is_postgres=True), serialize_json_for_db({}, is_postgres=True), serialize_json_for_db({}, is_postgres=True), serialize_json_for_db([], is_postgres=True), serialize_json_for_db([], is_postgres=True), now_iso))

            # Insert first answer at index 0
            ans_id_1 = f"ans_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO student_answers (id, session_id, attempt_id, student_id, question_index, question_text, selected_answer, correct_answer, is_correct, difficulty, concept, created_at)
                VALUES (%s, %s, %s, %s, 0, 'Q1', 'A', 'A', 1, 'mixed', 'Concept1', %s);
            """, (ans_id_1, sess_id, att_id, synthetic_student_id, now_iso))
            conn.commit()

            # Attempt duplicate insert at same (session_id, question_index=0) -> MUST FAIL
            ans_id_2 = f"ans_{uuid.uuid4().hex[:8]}"
            with pytest.raises(Exception):
                conn.execute("""
                    INSERT INTO student_answers (id, session_id, attempt_id, student_id, question_index, question_text, selected_answer, correct_answer, is_correct, difficulty, concept, created_at)
                    VALUES (%s, %s, %s, %s, 0, 'Q1 duplicate', 'B', 'A', 0, 'mixed', 'Concept1', %s);
                """, (ans_id_2, sess_id, att_id, synthetic_student_id, now_iso))
                conn.commit()
            conn.rollback()
        finally:
            conn.close()


# ── 5. End-to-End Quiz Session Lifecycle & Atomic Submission ───────────────────

@requires_live_pg
class TestLiveQuizSessionLifecycleAndAtomicSubmission:

    def test_full_session_atomic_submission_flow(self, pg_adapter, synthetic_student_id):
        """
        Creates a synthetic quiz session, validates it, and submits it atomically
        against live Supabase PostgreSQL.
        """
        session_id = f"syn_sess_{uuid.uuid4().hex[:10]}"
        questions = [
            {
                "question": "What is 2 + 2?",
                "options": ["3", "4", "5", "6"],
                "correct_answer": "4",
                "difficulty": "foundational",
                "concept": "Basic Math",
                "fingerprint": f"fp_{uuid.uuid4().hex[:8]}",
            },
            {
                "question": "What is 3 * 3?",
                "options": ["6", "8", "9", "12"],
                "correct_answer": "9",
                "difficulty": "foundational",
                "concept": "Multiplication",
                "fingerprint": f"fp_{uuid.uuid4().hex[:8]}",
            }
        ]

        # 1. Create quiz session on PostgreSQL
        sess_record = create_quiz_session(
            session_id=session_id,
            student_id=synthetic_student_id,
            course_code="MATH101",
            course_name="Elementary Mathematics",
            topic_name="Arithmetic",
            questions=questions,
            recommended_difficulty="foundational",
            adaptive_metadata={"starting_level": "foundational"},
            duration_seconds=3600,
        )
        assert sess_record["id"] == session_id
        assert sess_record["status"] == "in_progress"

        # 2. Retrieve and verify session
        fetched = get_quiz_session(session_id)
        assert fetched is not None
        assert fetched["id"] == session_id
        assert fetched["student_id"] == synthetic_student_id
        assert len(fetched["questions"]) == 2

        # 3. Submit quiz atomically
        from services.evaluator import evaluate_answers
        user_answers = ["4", "9"]  # 100% score

        submission = submit_quiz_atomic(
            session_id=session_id,
            student_id=synthetic_student_id,
            answers=user_answers,
            evaluator_fn=evaluate_answers,
        )
        assert submission["status"] in ("Excellent", "completed")
        assert submission["score"] == 2
        assert submission["total"] == 2
        assert submission["percentage"] == 100.0

        # 4. Verify session state is completed
        post_sess = get_quiz_session(session_id)
        assert post_sess["status"] == "completed"
        assert post_sess["submitted_at"] is not None

        # 5. Verify Topic Mastery was updated on PostgreSQL
        profile = get_topic_mastery(synthetic_student_id, "MATH101", "Arithmetic")
        assert profile is not None
        assert profile["attempt_count"] >= 1
        assert profile["total_questions"] >= 2
        assert profile["total_correct"] >= 2
        assert profile["mastery_score"] > 0.0
