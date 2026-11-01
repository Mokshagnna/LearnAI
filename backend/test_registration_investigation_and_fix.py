"""
test_registration_investigation_and_fix.py
==========================================
Comprehensive verification tests for LearnAI account registration flow:
1. Successful registration with a new email.
2. Registration with an already registered email (409 Conflict).
3. Invalid email, weak password, and mismatched password rejection (400 / 422).
4. Password hashing (Argon2id) and login using the newly created account.
5. Database persistence in students, student_accounts, and student_sessions.
6. Verification of MySQL migration discovery for Migration 003 and 004.
"""

import sys
import uuid
import secrets
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure backend is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from main import app
from services.database import (
    get_connection,
    get_student_account_by_email,
    get_student_account_by_id,
    init_db,
)
from services.account_service import (
    verify_password,
    normalize_email,
)
from services.db.migrations import (
    get_migrations_dir,
    split_sql_statements,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def ensure_clean_db():
    init_db()


class TestRegistrationInvestigationAndFix:
    """Verifies all six required registration scenarios."""

    def test_1_successful_registration_with_new_email(self):
        """1. Successful registration with a new email returns 201 and valid token."""
        unique_suffix = secrets.token_hex(6)
        email = f"student_{unique_suffix}@gmail.com"
        password = "SecurePassword123!"
        full_name = "Mokshagnna Tester"

        response = client.post(
            "/api/auth/register",
            json={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )

        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
        data = response.json()
        assert "token" in data
        assert "student_id" in data
        assert data["student_id"].startswith("stu_")
        assert data["email"] == email.lower()
        assert data["full_name"] == full_name
        assert data["message"] == "Account created successfully."

    def test_2_duplicate_email_rejection(self):
        """2. Registration with an already registered email returns 409 Conflict."""
        unique_suffix = secrets.token_hex(6)
        email = f"duplicate_{unique_suffix}@gmail.com"
        password = "Password999!"

        # First registration
        res1 = client.post(
            "/api/auth/register",
            json={
                "full_name": "First User",
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )
        assert res1.status_code == 201

        # Second registration with identical email (different case)
        res2 = client.post(
            "/api/auth/register",
            json={
                "full_name": "Second User",
                "email": email.upper(),
                "password": password,
                "confirm_password": password,
            },
        )
        assert res2.status_code == 409
        err = res2.json()
        assert "already exists" in err["detail"].lower()

    def test_3_invalid_email_and_invalid_password(self):
        """3. Invalid email and invalid password return proper client error status codes."""
        # 3a. Invalid email format
        res_bad_email = client.post(
            "/api/auth/register",
            json={
                "full_name": "Valid Name",
                "email": "not-an-email",
                "password": "ValidPassword123",
                "confirm_password": "ValidPassword123",
            },
        )
        assert res_bad_email.status_code in (400, 422)
        assert "valid email" in res_bad_email.text.lower() or "value_error" in res_bad_email.text.lower()

        # 3b. Short password (< 8 chars)
        res_short_pwd = client.post(
            "/api/auth/register",
            json={
                "full_name": "Valid Name",
                "email": f"short_{secrets.token_hex(4)}@gmail.com",
                "password": "Pass1",
                "confirm_password": "Pass1",
            },
        )
        assert res_short_pwd.status_code in (400, 422)

        # 3c. Password without number
        res_no_num = client.post(
            "/api/auth/register",
            json={
                "full_name": "Valid Name",
                "email": f"nonum_{secrets.token_hex(4)}@gmail.com",
                "password": "OnlyLettersPassword",
                "confirm_password": "OnlyLettersPassword",
            },
        )
        assert res_no_num.status_code == 400
        assert "number" in res_no_num.json()["detail"].lower()

        # 3d. Password mismatch
        res_mismatch = client.post(
            "/api/auth/register",
            json={
                "full_name": "Valid Name",
                "email": f"mismatch_{secrets.token_hex(4)}@gmail.com",
                "password": "Password123",
                "confirm_password": "DifferentPassword123",
            },
        )
        assert res_mismatch.status_code == 400
        assert "match" in res_mismatch.json()["detail"].lower()

    def test_4_password_hashing_and_login_with_new_account(self):
        """4. Password is stored as Argon2id hash and login succeeds with new account."""
        unique_suffix = secrets.token_hex(6)
        email = f"login_test_{unique_suffix}@gmail.com"
        password = "LoginPass789!"
        full_name = "Login Tester"

        # Register
        reg_res = client.post(
            "/api/auth/register",
            json={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )
        assert reg_res.status_code == 201
        student_id = reg_res.json()["student_id"]

        # Verify password in DB is Argon2id hash
        account = get_student_account_by_id(student_id)
        assert account is not None
        stored_hash = account["password_hash"]
        assert stored_hash != password
        assert stored_hash.startswith("$argon2id$")
        assert verify_password(stored_hash, password) is True
        assert verify_password(stored_hash, "WrongPassword123") is False

        # Login with correct credentials
        login_res = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login_res.status_code == 200
        login_data = login_res.json()
        assert login_data["student_id"] == student_id
        assert login_data["email"] == email.lower()
        assert "token" in login_data

        # Login with incorrect password
        bad_login = client.post(
            "/api/auth/login",
            json={"email": email, "password": "WrongPassword999"},
        )
        assert bad_login.status_code == 401
        assert "invalid" in bad_login.json()["detail"].lower()

    def test_5_database_persistence_after_registration(self):
        """5. Verifies database persistence in students, student_accounts, and student_sessions."""
        unique_suffix = secrets.token_hex(6)
        email = f"persist_{unique_suffix}@gmail.com"
        password = "PersistPass123!"
        full_name = "Persistence Test"

        reg_res = client.post(
            "/api/auth/register",
            json={
                "full_name": full_name,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )
        assert reg_res.status_code == 201
        student_id = reg_res.json()["student_id"]

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Verify students record exists
            cursor.execute("SELECT id, created_at, last_active FROM students WHERE id = ?", (student_id,))
            student_row = cursor.fetchone()
            assert student_row is not None
            assert student_row[0] == student_id

            # Verify student_accounts record exists
            cursor.execute("SELECT id, student_id, email, full_name, password_hash FROM student_accounts WHERE student_id = ?", (student_id,))
            account_row = cursor.fetchone()
            assert account_row is not None
            assert account_row[1] == student_id
            assert account_row[2] == email.lower()
            assert account_row[3] == full_name

            # Verify student_sessions record exists
            cursor.execute("SELECT id, student_id, token_hash, is_revoked FROM student_sessions WHERE student_id = ?", (student_id,))
            session_row = cursor.fetchone()
            assert session_row is not None
            assert session_row[1] == student_id
            assert session_row[3] == 0
        finally:
            conn.close()

    def test_6_mysql_migration_discovery_and_schema_validation(self):
        """6. Verifies MySQL migrations 001, 002, 003, and 004 are discovered for MySQL."""
        mdir = get_migrations_dir()
        eff_dialect = "mysql"
        sql_files = sorted([
            f for f in mdir.glob("*.sql")
            if not f.name.endswith(".rollback.sql")
            and "postgres" not in f.name.lower()
            and "supabase" not in f.name.lower()
            and "sqlite" not in f.name.lower()
        ])
        discovered_names = [f.name for f in sql_files]

        assert "001_initial_mysql_schema.sql" in discovered_names
        assert "002_study_materials_mysql_schema.sql" in discovered_names
        assert "003_student_accounts_and_auth.sql" in discovered_names
        assert "004_topic_evidence_and_coverage.sql" in discovered_names

        # Parse statements of migration 003 to ensure clean syntax
        m003_content = (mdir / "003_student_accounts_and_auth.sql").read_text(encoding="utf-8")
        stmts = split_sql_statements(m003_content)
        assert len(stmts) == 3
        assert "CREATE TABLE IF NOT EXISTS student_accounts" in stmts[0]
        assert "CREATE TABLE IF NOT EXISTS student_sessions" in stmts[1]
        assert "CREATE TABLE IF NOT EXISTS password_reset_tokens" in stmts[2]

    def test_7_legacy_student_and_account_coexistence(self):
        """7. Verifies legacy anonymous students in `students` do not block new Gmail registrations."""
        legacy_uuid = str(uuid.uuid4())
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)",
                (legacy_uuid, "2026-01-01T00:00:00", "2026-01-01T00:00:00")
            )
            conn.commit()
        finally:
            conn.close()

        # Registration with a new Gmail succeeds cleanly
        unique_suffix = secrets.token_hex(6)
        email = f"student_{unique_suffix}@gmail.com"
        password = "Password789!"
        reg_res = client.post(
            "/api/auth/register",
            json={
                "full_name": "Legacy Coexistence",
                "email": email,
                "password": password,
                "confirm_password": password,
            },
        )
        assert reg_res.status_code == 201
        new_stu_id = reg_res.json()["student_id"]
        assert new_stu_id != legacy_uuid
        assert new_stu_id.startswith("stu_")

        # Verify legacy student remains in students table unmodified
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM students WHERE id = ?", (legacy_uuid,))
            assert cursor.fetchone() is not None
            # Verify new student also exists in students
            cursor.execute("SELECT id FROM students WHERE id = ?", (new_stu_id,))
            assert cursor.fetchone() is not None
            # Verify account exists in student_accounts
            cursor.execute("SELECT student_id, email FROM student_accounts WHERE student_id = ?", (new_stu_id,))
            acc_row = cursor.fetchone()
            assert acc_row is not None
            assert acc_row[1] == email.lower()
        finally:
            conn.close()

    def test_8_migration_004_mysql_tidb_compatibility(self):
        """8. Verifies migration 004 has no BLOB/TEXT/JSON defaults and is TiDB/MySQL compliant."""
        import re
        mdir = get_migrations_dir()
        m004_file = mdir / "004_topic_evidence_and_coverage.sql"
        assert m004_file.is_file()
        content = m004_file.read_text(encoding="utf-8")
        stmts = split_sql_statements(content)
        assert len(stmts) == 1
        stmt = stmts[0].upper()

        # Guard against MySQL Error 1101: BLOB/TEXT/JSON column can't have a default value
        bad_defaults = re.findall(r"\b(TEXT|BLOB|JSON|MEDIUMTEXT|LONGTEXT)\b.*?\bDEFAULT\b", stmt)
        assert len(bad_defaults) == 0, f"Found incompatible TEXT/BLOB/JSON default: {bad_defaults}"

        assert "ADD COLUMN TOPIC_ALIASES TEXT" in stmt
        assert "DEFAULT '[]'" not in stmt
        assert "COVERAGE_STATUS VARCHAR(50) NOT NULL DEFAULT 'NEEDS_REVIEW'" in stmt
        assert "ADD COLUMN EVIDENCE TEXT" in stmt

        # Check rollback script syntax
        rb_file = mdir / "004_topic_evidence_and_coverage.rollback.sql"
        assert rb_file.is_file()
        rb_content = rb_file.read_text(encoding="utf-8")
        rb_stmts = split_sql_statements(rb_content)
        assert len(rb_stmts) == 1
        assert "DROP COLUMN TOPIC_ALIASES" in rb_stmts[0].upper()
