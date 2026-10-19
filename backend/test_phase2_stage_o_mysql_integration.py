"""
test_phase2_stage_o_mysql_integration.py
========================================
Comprehensive Live MySQL Integration Test Suite for LearnAI.
Verifies LearnAI operations against a real, disposable MySQL instance
using synthetic test records (never touching SQLite data).

Safety Guards:
- Strictly requires TEST_MYSQL_DATABASE_URL (will NOT fall back to DATABASE_URL).
- Validates that the connected target is an approved disposable test database.
- Production rejection takes absolute precedence over all overrides.
- Uses 100% synthetic records prefixed with 'syn_stud_', 'syn_sess_', etc.
- Scoped teardown: deletes only synthetic test records.
- Zero writes to local SQLite database or backups.
"""

import os
import sys
import ssl
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
    translate_placeholders,
    parse_json_column,
    serialize_json_for_db,
)
from services.db.mysql_adapter import MySQLAdapter, MySQLConnectionWrapper
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

# STRICT GUARD: Exclusively use TEST_MYSQL_DATABASE_URL
LIVE_MYSQL_URL = os.getenv("TEST_MYSQL_DATABASE_URL")
PRODUCTION_TARGET_INDICATORS = ("prod", "production")


def is_verified_mysql_test_target(url: str) -> bool:
    """
    Ensure target URL points strictly to an approved disposable MySQL test database.
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

    # Scheme must be a valid MySQL scheme
    if parsed.scheme not in ("mysql", "mysql+pymysql"):
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

    # 2. Local disposable test databases (localhost / 127.0.0.1 / test-mysql)
    is_local = hostname in ("localhost", "127.0.0.1", "::1", "test-mysql")
    db_name = path.lstrip("/").lower()

    if is_local and ("test" in db_name or "dev" in db_name or os.getenv("CONFIRM_DISPOSABLE_TEST_TARGET") == "true"):
        return True

    # 3. Remote test databases: require explicit confirmation AND test/dev in database name
    if ("test" in db_name or "dev" in db_name) and os.getenv("CONFIRM_DISPOSABLE_TEST_TARGET") == "true":
        return True

    return False


# ── Safeguard Unit Tests (No DB connection required) ──────────────────────────

class TestMySQLTestTargetSafeguards:
    """Unit tests verifying MySQL test target safety guards (no DB connection required)."""

    def test_local_test_database_accepted(self):
        url = "mysql://root:secret_pass@127.0.0.1:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is True

    def test_local_dev_database_accepted(self):
        url = "mysql+pymysql://dev_user:secret_pass@localhost:3306/learnai_dev"
        assert is_verified_mysql_test_target(url) is True

    def test_empty_url_rejected(self):
        assert is_verified_mysql_test_target("") is False
        assert is_verified_mysql_test_target("   ") is False
        assert is_verified_mysql_test_target(None) is False

    def test_malformed_url_rejected(self):
        assert is_verified_mysql_test_target("not-a-valid-url") is False

    def test_non_mysql_scheme_rejected(self):
        assert is_verified_mysql_test_target("postgresql://root:pwd@127.0.0.1:5432/learnai_test") is False

    def test_production_database_name_rejected_even_with_confirmation(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = "mysql://app_user:pwd@127.0.0.1:3306/learnai_prod"
        assert is_verified_mysql_test_target(url) is False

    def test_production_hostname_rejected_even_with_confirmation(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = "mysql://app_user:pwd@mysql-production.cloud.internal:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is False

    def test_production_username_rejected_even_with_confirmation(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = "mysql://prod_admin:pwd@127.0.0.1:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is False

    def test_password_containing_prod_does_not_cause_false_rejection(self):
        url = "mysql://test_user:my_prod_secret_pass@127.0.0.1:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is True

    def test_remote_target_without_confirmation_rejected(self, monkeypatch):
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        url = "mysql://test_user:pwd@remote-mysql-server.net:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is False

    def test_remote_target_with_confirmation_and_test_db_accepted(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = "mysql://test_user:pwd@remote-mysql-server.net:3306/learnai_test"
        assert is_verified_mysql_test_target(url) is True

    def test_migration_script_url_parsing_and_adapter_method(self):
        """Verify that MySQLAdapter.parse_connection_params correctly parses target URL used by migrate_sqlite_to_mysql."""
        test_url = "mysql://app_user:strong_pwd@127.0.0.1:3306/learnai_test"
        connect_kwargs = MySQLAdapter.parse_connection_params(test_url)
        assert connect_kwargs["user"] == "app_user"
        assert connect_kwargs["password"] == "strong_pwd"
        assert connect_kwargs["host"] == "127.0.0.1"
        assert connect_kwargs["port"] == 3306
        assert connect_kwargs["database"] == "learnai_test"
        assert connect_kwargs["charset"] == "utf8mb4"
        assert connect_kwargs["autocommit"] is False

    def test_migration_script_mask_connection_url(self):
        """Verify mask_connection_url hides sensitive passwords without opening a database."""
        from scripts.migrate_sqlite_to_mysql import mask_connection_url
        url = "mysql://learnai_user:super_secret_password@mysql.example.com:3306/learnai_db"
        masked = mask_connection_url(url)
        assert "super_secret_password" not in masked
        assert "***" in masked
        assert "mysql://learnai_user:***@mysql.example.com:3306/learnai_db" in masked

    # ── Destructive Target Selection Safeguard Tests ─────────────────────────

    def test_clean_without_explicit_target_url_fails(self, monkeypatch):
        """Verify that --clean without --target-url raises ValueError and rejects fallback to DATABASE_URL."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        monkeypatch.setenv("DATABASE_URL", "mysql://dev_user:pass@127.0.0.1:3306/learnai_test")
        monkeypatch.setenv("TEST_MYSQL_DATABASE_URL", "mysql://dev_user:pass@127.0.0.1:3306/learnai_test")
        with pytest.raises(ValueError, match="Destructive operation .* requires an explicit --target-url"):
            validate_migration_arguments(target_url=None, clean=True, confirm_destructive=True)
        with pytest.raises(ValueError, match="Destructive operation .* requires an explicit --target-url"):
            validate_migration_arguments(target_url="", clean=True, confirm_destructive=True)

    def test_clean_without_confirm_destructive_fails(self):
        """Verify that --clean requires explicit --confirm-destructive."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        url = "mysql://dev_user:pass@127.0.0.1:3306/learnai_test"
        with pytest.raises(ValueError, match="requires explicit confirmation flag --confirm-destructive"):
            validate_migration_arguments(target_url=url, clean=True, confirm_destructive=False)

    def test_clean_against_production_target_fails(self):
        """Verify that --clean unconditionally rejects production targets even with confirmation."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        prod_urls = [
            "mysql://prod_user:pass@127.0.0.1:3306/learnai_test",
            "mysql://app_user:pass@mysql-production.cloud:3306/learnai_test",
            "mysql://app_user:pass@127.0.0.1:3306/learnai_production",
            "mysql://app_user:pass@127.0.0.1:3306/learnai_prod",
        ]
        for url in prod_urls:
            with pytest.raises(ValueError, match="cannot be executed against a production target"):
                validate_migration_arguments(target_url=url, clean=True, confirm_destructive=True)

    def test_clean_with_explicit_test_target_and_confirmation_succeeds(self):
        """Verify that --clean succeeds when both explicit non-production target and confirmation are provided."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        url = "mysql://dev_user:pass@127.0.0.1:3306/learnai_test"
        res = validate_migration_arguments(target_url=url, clean=True, confirm_destructive=True)
        assert res == url

    def test_non_destructive_with_explicit_target(self):
        """Verify non-destructive migration allows explicit target without confirmation flag."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        url = "mysql://dev_user:pass@127.0.0.1:3306/learnai_test"
        res = validate_migration_arguments(target_url=url, clean=False, confirm_destructive=False)
        assert res == url

    def test_non_destructive_with_env_test_mysql_database_url(self, monkeypatch):
        """Verify non-destructive migration falls back to safe TEST_MYSQL_DATABASE_URL."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        test_url = "mysql://dev_user:pass@127.0.0.1:3306/learnai_test"
        monkeypatch.setenv("TEST_MYSQL_DATABASE_URL", test_url)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        res = validate_migration_arguments(target_url=None, clean=False)
        assert res == test_url

    def test_non_destructive_with_production_test_url_fails(self, monkeypatch):
        """Verify fallback to TEST_MYSQL_DATABASE_URL fails if it points to production."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        prod_test_url = "mysql://prod_admin:pass@127.0.0.1:3306/learnai_test"
        monkeypatch.setenv("TEST_MYSQL_DATABASE_URL", prod_test_url)
        with pytest.raises(ValueError, match="contains production target indicators"):
            validate_migration_arguments(target_url=None, clean=False)

    def test_non_destructive_fallback_to_database_url(self, monkeypatch):
        """Verify non-destructive migration falls back to DATABASE_URL if TEST_MYSQL_DATABASE_URL is not set."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        db_url = "mysql://app_user:pass@127.0.0.1:3306/learnai_app"
        monkeypatch.delenv("TEST_MYSQL_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", db_url)
        res = validate_migration_arguments(target_url=None, clean=False)
        assert res == db_url

    def test_missing_all_targets_fails(self, monkeypatch):
        """Verify that lack of target URL and env variables raises ValueError."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        monkeypatch.delenv("TEST_MYSQL_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(ValueError, match="No target MySQL URL provided"):
            validate_migration_arguments(target_url=None, clean=False)

    def test_invalid_scheme_rejected(self):
        """Verify non-mysql schemes are rejected."""
        from scripts.migrate_sqlite_to_mysql import validate_migration_arguments
        with pytest.raises(ValueError, match="Unsupported target URL scheme 'postgresql'"):
            validate_migration_arguments(target_url="postgresql://user:pass@127.0.0.1:5432/learnai_test", clean=False)

    def test_migrate_data_defense_in_depth_rejects_clean_on_production(self):
        """Verify migrate_data step 0 defense-in-depth rejects clean on production before connecting or touching disk."""
        from scripts.migrate_sqlite_to_mysql import migrate_data
        prod_url = "mysql://app_user:pass@production-db.internal:3306/learnai_live"
        with pytest.raises(ValueError, match="Refusing to execute destructive migration"):
            migrate_data(
                sqlite_path=Path("nonexistent_dummy.db"),
                target_url=prod_url,
                clean_target=True,
            )

    def test_is_production_target_helper(self):
        """Verify is_production_target accurately flags indicators in user, host, or path."""
        from scripts.migrate_sqlite_to_mysql import is_production_target
        assert is_production_target("mysql://prod_user:pass@127.0.0.1/db") is True
        assert is_production_target("mysql://user:pass@production-host.com/db") is True
        assert is_production_target("mysql://user:pass@localhost/learnai_prod") is True
        assert is_production_target("mysql://user:pass@localhost/learnai_production") is True
        assert is_production_target("mysql://user:pass@127.0.0.1/learnai_test") is False
        assert is_production_target("mysql://user:prod_pass@127.0.0.1/learnai_test") is False
        assert is_production_target("") is False
        assert is_production_target(None) is False

    # ── TLS Parsing Safeguard Unit Tests ──────────────────────────────────────

    def test_tls_verify_identity_mode(self):
        """Verify ssl_mode=VERIFY_IDENTITY configures SSLContext with check_hostname=True and CERT_REQUIRED."""
        url = "mysql://app:pwd@tidb-serverless.com:4000/test?ssl_mode=VERIFY_IDENTITY"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], ssl.SSLContext)
        assert params["ssl"].check_hostname is True
        assert params["ssl"].verify_mode == ssl.CERT_REQUIRED

    def test_tls_verify_ca_mode(self):
        """Verify ssl_mode=VERIFY_CA configures SSLContext with check_hostname=False and CERT_REQUIRED."""
        url = "mysql://app:pwd@aiven-mysql.net:12345/test?ssl_mode=VERIFY_CA"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], ssl.SSLContext)
        assert params["ssl"].check_hostname is False
        assert params["ssl"].verify_mode == ssl.CERT_REQUIRED

    def test_tls_hyphenated_ssl_mode(self):
        """Verify ssl-mode (hyphenated) is handled identically to ssl_mode."""
        url = "mysql://app:pwd@tidb-serverless.com:4000/test?ssl-mode=verify-identity"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], ssl.SSLContext)
        assert params["ssl"].check_hostname is True
        assert params["ssl"].verify_mode == ssl.CERT_REQUIRED

    def test_tls_ssl_verify_cert_flag(self):
        """Verify ssl_verify_cert=true enables strict SSLContext."""
        url = "mysql://app:pwd@custom-mysql:3306/test?ssl_verify_cert=true"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], ssl.SSLContext)
        assert params["ssl"].check_hostname is True
        assert params["ssl"].verify_mode == ssl.CERT_REQUIRED

    def test_tls_ssl_true_encryption_only(self):
        """Verify ssl=true retains backward-compatible encryption-only mode (check_hostname=False dict)."""
        url = "mysql://app:pwd@localhost:3306/test?ssl=true"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], dict)
        assert params["ssl"] == {"check_hostname": False}

    def test_tls_ssl_mode_required_encryption_only(self):
        """Verify ssl_mode=REQUIRED retains backward-compatible encryption-only mode."""
        url = "mysql://app:pwd@localhost:3306/test?ssl_mode=REQUIRED"
        params = MySQLAdapter.parse_connection_params(url)
        assert isinstance(params["ssl"], dict)
        assert params["ssl"] == {"check_hostname": False}

    def test_tls_ssl_disabled(self):
        """Verify ssl=false and ssl_mode=DISABLED omit ssl configuration."""
        for q in ("ssl=false", "ssl=0", "ssl=disabled", "ssl_mode=DISABLED"):
            url = f"mysql://app:pwd@localhost:3306/test?{q}"
            params = MySQLAdapter.parse_connection_params(url)
            assert "ssl" not in params

    def test_tls_no_ssl_params_omits_ssl(self):
        """Verify standard connection without SSL parameters has no ssl key."""
        url = "mysql://app:pwd@localhost:3306/test"
        params = MySQLAdapter.parse_connection_params(url)
        assert "ssl" not in params



# ── Live Integration Tests (Requires verified TEST_MYSQL_DATABASE_URL) ─────────

@pytest.mark.skipif(
    not is_verified_mysql_test_target(LIVE_MYSQL_URL),
    reason="TEST_MYSQL_DATABASE_URL is not set or does not point to a verified disposable MySQL test database."
)
class TestLiveMySQLIntegration:
    """
    Live integration tests executed exclusively against a disposable MySQL test instance.
    All records use synthetic prefixes and are cleaned up after each test.
    """

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        config = DatabaseConfig(database_url=LIVE_MYSQL_URL)
        adapter = MySQLAdapter(config)
        conn = adapter.get_connection()

        # Apply schema migrations
        applied = apply_migrations(conn, dialect="mysql")

        synthetic_student_id = f"syn_stud_{uuid.uuid4().hex[:12]}"
        yield {
            "conn": conn,
            "adapter": adapter,
            "student_id": synthetic_student_id,
            "applied": applied
        }

        # Scoped teardown: delete synthetic records
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM student_answers WHERE student_id = %s;", (synthetic_student_id,))
            cur.execute("DELETE FROM question_metadata WHERE student_id = %s;", (synthetic_student_id,))
            cur.execute("DELETE FROM quiz_attempts WHERE student_id = %s;", (synthetic_student_id,))
            cur.execute("DELETE FROM quiz_sessions WHERE student_id = %s;", (synthetic_student_id,))
            cur.execute("DELETE FROM topic_mastery WHERE student_id = %s;", (synthetic_student_id,))
            cur.execute("DELETE FROM students WHERE id = %s;", (synthetic_student_id,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def test_live_mysql_connectivity_and_version(self, setup_and_teardown):
        conn = setup_and_teardown["conn"]
        cur = conn.cursor()
        cur.execute("SELECT VERSION();")
        row = cur.fetchone()
        assert row is not None
        version_str = row[0] if isinstance(row, (tuple, list)) else row["VERSION()"]
        assert len(version_str) > 0

    def test_live_mysql_schema_migrations_applied(self, setup_and_teardown):
        conn = setup_and_teardown["conn"]
        applied = get_applied_migrations(conn)
        assert len(applied) >= 1
        assert any("mysql" in v for v in applied)

    def test_live_mysql_crud_lifecycle(self, setup_and_teardown):
        conn = setup_and_teardown["conn"]
        student_id = setup_and_teardown["student_id"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        cur = conn.cursor()
        # 1. Insert synthetic student
        cur.execute(
            "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s);",
            (student_id, now, now)
        )
        conn.commit()

        # 2. Query student
        cur.execute("SELECT id, created_at FROM students WHERE id = %s;", (student_id,))
        row = cur.fetchone()
        assert row is not None
        assert row["id"] == student_id

        # 3. Create quiz session
        sess_id = f"syn_sess_{uuid.uuid4().hex[:12]}"
        questions = [{"question": "What is AI?", "correct_answer": "Artificial Intelligence"}]
        cur.execute(
            """INSERT INTO quiz_sessions (
                id, student_id, course_code, course_name, topic_name,
                status, questions_snapshot, recommended_difficulty, created_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            (sess_id, student_id, "CS101", "AI intro", "Basics",
             "in_progress", json.dumps(questions), "mixed", now, now)
        )
        conn.commit()

        cur.execute("SELECT * FROM quiz_sessions WHERE id = %s;", (sess_id,))
        sess_row = cur.fetchone()
        assert sess_row is not None
        assert sess_row["status"] == "in_progress"

    def test_live_mysql_upsert_topic_mastery(self, setup_and_teardown):
        conn = setup_and_teardown["conn"]
        student_id = setup_and_teardown["student_id"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO students (id, created_at, last_active) VALUES (%s, %s, %s);",
            (student_id, now, now)
        )
        conn.commit()

        # Execute upsert via upsert_topic_mastery
        res = upsert_topic_mastery(
            student_id=student_id,
            course_code="CS101",
            topic_name="Search Algorithms",
            score=5,
            total=5,
            weak_concepts=[],
            strong_concepts=["BFS", "DFS"],
            current_level="mixed",
            conn=conn
        )
        assert res["attempt_count"] == 1
        assert res["total_correct"] == 5

        # Re-upsert (second attempt) to verify ON DUPLICATE KEY UPDATE
        res2 = upsert_topic_mastery(
            student_id=student_id,
            course_code="CS101",
            topic_name="Search Algorithms",
            score=4,
            total=5,
            weak_concepts=["A*"],
            strong_concepts=["BFS"],
            current_level=res["current_level"],
            conn=conn
        )
        assert res2["attempt_count"] == 2
        assert res2["total_questions"] == 10
        assert res2["total_correct"] == 9
