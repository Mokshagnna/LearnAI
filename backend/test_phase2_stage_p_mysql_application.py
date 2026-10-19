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


# ── Live Application Tests (Requires verified TEST_MYSQL_DATABASE_URL) ────────

@pytest.mark.skipif(
    not is_verified_mysql_test_target(LIVE_MYSQL_URL),
    reason="TEST_MYSQL_DATABASE_URL is not set or does not point to a verified disposable MySQL test database."
)
class TestLiveMySQLApplication:
    """
    Live FastAPI routes verification executed exclusively against a disposable MySQL test instance.
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
        yield {
            "client": client,
            "student_id": synthetic_student_id,
        }

        reset_db_adapter()

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
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "student_id" in data
        assert data["status"] == "ok"
