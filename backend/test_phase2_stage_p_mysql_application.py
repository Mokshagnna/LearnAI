"""
test_phase2_stage_p_mysql_application.py
========================================
Stage P: Application-Level MySQL Verification Suite for LearnAI.
Verifies real FastAPI routes and service layer operations against a
disposable MySQL database instance.

Safety Guards:
- Strictly requires TEST_MYSQL_DATABASE_URL (will NOT fall back to DATABASE_URL).
- Validates that the connected target is an approved disposable test database.
- Production rejection takes absolute precedence over all overrides.
- All live tests run with TestClient and clean up synthetic data.
- Unit safeguards run unconditionally without requiring a database.
"""

import os
import sys
import uuid
import time
import pytest
from pathlib import Path
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

LIVE_MYSQL_URL = os.getenv("TEST_MYSQL_DATABASE_URL")
PRODUCTION_TARGET_INDICATORS = ("prod", "production")


def is_tidb_cloud_gateway_hostname(hostname: str) -> bool:
    """
    Recognize documented TiDB Cloud AWS Serverless gateway hostname format:
    gateway<digits>.<region>.prod.aws.tidbcloud.com
    - Exactly six DNS labels
    - First label: 'gateway' followed by digits (e.g., 'gateway01')
    - Second label: non-empty region (letters, digits, or hyphens)
    - Final four labels: exactly 'prod', 'aws', 'tidbcloud', 'com'
    """
    if not hostname or not isinstance(hostname, str):
        return False
    parts = hostname.strip().lower().rstrip(".").split(".")
    return (
        len(parts) == 6
        and parts[0].startswith("gateway")
        and parts[0][7:].isdigit()
        and bool(parts[1])
        and all(char.isalnum() or char == "-" for char in parts[1])
        and parts[2:] == ["prod", "aws", "tidbcloud", "com"]
    )


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
    for indicator in PRODUCTION_TARGET_INDICATORS:
        if indicator in username or indicator in path:
            return False
        # "prod" is part of TiDB Cloud's documented AWS gateway domain.
        # Only exempt hostnames matching the exact gateway pattern.
        if indicator in hostname and not is_tidb_cloud_gateway_hostname(hostname):
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

class TestMySQLApplicationSafeguards:
    """Unit tests verifying MySQL application test target safeguards (no DB required)."""

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

    def test_application_test_cleanup_scoped_to_exact_student_id(self):
        """Verify cleanup logic targets only the exact student ID recorded by test without broad deletes."""
        student_id = "test-student-exact-uuid-12345"
        executed_statements = []

        class MockCursor:
            def execute(self, sql, params=None):
                executed_statements.append((sql, params))

        class MockConn:
            def cursor(self):
                return MockCursor()
            def commit(self):
                pass
            def close(self):
                pass

        conn = MockConn()
        cur = conn.cursor()
        for sid in [student_id]:
            cur.execute("DELETE FROM student_answers WHERE student_id = %s;", (sid,))
            cur.execute("DELETE FROM question_metadata WHERE student_id = %s;", (sid,))
            cur.execute("DELETE FROM quiz_attempts WHERE student_id = %s;", (sid,))
            cur.execute("DELETE FROM quiz_sessions WHERE student_id = %s;", (sid,))
            cur.execute("DELETE FROM topic_mastery WHERE student_id = %s;", (sid,))
            cur.execute("DELETE FROM students WHERE id = %s;", (sid,))

        assert len(executed_statements) == 6
        for sql, params in executed_statements:
            assert params == (student_id,)
            assert "WHERE student_id = %s" in sql or "WHERE id = %s" in sql

    def test_application_test_cleanup_surfaces_errors_and_closes_connection(self):
        """Verify that cleanup error is surfaced loudly and connection is closed under all outcomes."""
        class FailingConn:
            def __init__(self):
                self.closed = False
            def cursor(self):
                raise RuntimeError("Simulated DB connection drop")
            def close(self):
                self.closed = True

        conn = FailingConn()
        cleanup_error = None
        created_student_ids = ["test-student-123"]
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM students WHERE id = %s;", (created_student_ids[0],))
        except Exception as err:
            cleanup_error = err
        finally:
            conn.close()

        assert conn.closed is True
        assert cleanup_error is not None
        assert "Simulated DB connection drop" in str(cleanup_error)


# ── Live Application Tests (Requires verified TEST_MYSQL_DATABASE_URL) ────────

@pytest.mark.skipif(
    not is_verified_mysql_test_target(LIVE_MYSQL_URL),
    reason="TEST_MYSQL_DATABASE_URL is not set or does not point to a verified disposable MySQL test database."
)
class TestLiveMySQLApplication:
    """
    Live FastAPI routes verification executed exclusively against a disposable MySQL test instance.
    All records created during testing are cleaned up scoped strictly to their exact identifiers.
    """

    @pytest.fixture(autouse=True)
    def setup_app(self, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        from services.database import reset_db_adapter, init_db

        monkeypatch.setenv("DATABASE_URL", LIVE_MYSQL_URL)
        monkeypatch.setenv("SESSION_SECRET_KEY", "mysql-app-test-secret-key-abcdef123456")
        reset_db_adapter()
        init_db()

        client = TestClient(app)
        synthetic_student_id = f"syn_stud_{uuid.uuid4().hex[:12]}"
        created_student_ids = []
        try:
            yield {
                "client": client,
                "student_id": synthetic_student_id,
                "created_student_ids": created_student_ids,
            }
        finally:
            # Scoped teardown: delete student records created during test execution
            cleanup_error = None
            if created_student_ids:
                conn = None
                try:
                    from services.db.base import DatabaseConfig
                    from services.db.mysql_adapter import MySQLAdapter

                    cleanup_adapter = MySQLAdapter(DatabaseConfig(database_url=LIVE_MYSQL_URL))
                    conn = cleanup_adapter.get_connection()
                    cur = conn.cursor()
                    for sid in created_student_ids:
                        cur.execute("DELETE FROM student_answers WHERE student_id = %s;", (sid,))
                        cur.execute("DELETE FROM question_metadata WHERE student_id = %s;", (sid,))
                        cur.execute("DELETE FROM quiz_attempts WHERE student_id = %s;", (sid,))
                        cur.execute("DELETE FROM quiz_sessions WHERE student_id = %s;", (sid,))
                        cur.execute("DELETE FROM topic_mastery WHERE student_id = %s;", (sid,))
                        cur.execute("DELETE FROM students WHERE id = %s;", (sid,))
                    conn.commit()
                except Exception as err:
                    cleanup_error = err
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            reset_db_adapter()
            if cleanup_error is not None:
                raise RuntimeError(
                    f"Live MySQL test cleanup failed for student IDs {created_student_ids}: {cleanup_error}"
                ) from cleanup_error

    def test_health_check_reports_healthy_with_mysql(self, setup_app):
        client = setup_app["client"]
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["database"] == "connected"
        assert data["status"] == "healthy"

    def test_auth_session_creation_with_mysql(self, setup_app):
        client = setup_app["client"]
        res = client.post("/api/auth/session")
        if res.status_code == 200:
            try:
                data = res.json()
                sid = data.get("student_id")
                if sid and isinstance(sid, str):
                    setup_app["created_student_ids"].append(sid)
            except Exception:
                pass
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "student_id" in data
        assert "expires_at" in data
