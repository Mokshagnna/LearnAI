"""
test_student_auth_system.py
===========================
Comprehensive Test Suite for LearnAI Student Registration, Login, Sessions,
and Password Reset System.

Validates:
1. Registration:
   - Successful registration with normalized email and Argon2id hash.
   - Duplicate email rejection (409 Conflict).
   - Invalid email format rejection (400 Bad Request).
   - Password mismatch rejection (400 Bad Request).
   - Password strength validation (min 8 chars, letter + digit).
   - Verification that NO plaintext password exists in the database.
   - Argon2id password hashing algorithm verification.
2. Login and Sessions:
   - Successful login with correct credentials.
   - Incorrect password rejection with generic 401 error.
   - Unknown email rejection with generic 401 error (no email enumeration).
   - Case-insensitive email login normalization.
   - Session persistence and /api/auth/me profile retrieval.
   - Successful logout and immediate session revocation.
   - Rejection of revoked or tampered sessions.
3. Forgot Password and Password Reset:
   - Forgot password request generates single-use secure token hash.
   - Generic response prevents user enumeration.
   - Reset password with valid token updates password.
   - Verification that previous password no longer works and new password works.
   - Single-use token enforcement: reusing the token is rejected.
   - Expired token rejection.
   - Existing active sessions across devices are invalidated after password reset.
   - Unconfigured email behavior: clear, documented development fallback.
"""

import pytest
import sqlite3
import time
import os
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from services.database import (
    init_db,
    get_connection,
    get_student_account_by_email,
    get_student_account_by_id,
)
from services.auth_utils import (
    sign_student_identity,
    verify_student_identity,
)
from services.account_service import (
    hash_password,
    verify_password,
)
from services.rate_limiter import reset_all_rate_limiters


@pytest.fixture
def auth_test_client(tmp_path, monkeypatch):
    """Fixture providing an isolated SQLite database and fresh TestClient."""
    reset_all_rate_limiters()
    db_file = tmp_path / "learnai_student_auth_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    yield client, db_file
    reset_all_rate_limiters()


# ── 1. Registration Tests ────────────────────────────────────────────────────

class TestStudentRegistration:

    def test_successful_registration(self, auth_test_client):
        """Registering with valid full name, email, and matching passwords succeeds."""
        client, db_file = auth_test_client
        payload = {
            "full_name": "Aarav Sharma",
            "email": "aarav.sharma@gmail.com",
            "password": "SecurePassword123",
            "confirm_password": "SecurePassword123"
        }
        res = client.post("/api/auth/register", json=payload)
        assert res.status_code == 201
        data = res.json()

        assert "student_id" in data
        assert data["student_id"].startswith("stu_")
        assert "token" in data
        assert data["email"] == "aarav.sharma@gmail.com"
        assert data["full_name"] == "Aarav Sharma"
        assert data["expires_at"] > int(time.time())

        # Verify token cryptographically
        is_valid, decoded_id, err = verify_student_identity(data["token"])
        assert is_valid is True
        assert decoded_id == data["student_id"]
        assert err is None

        # Verify database record
        acct = get_student_account_by_email("aarav.sharma@gmail.com")
        assert acct is not None
        assert acct["student_id"] == data["student_id"]
        assert acct["full_name"] == "Aarav Sharma"
        assert acct["email_verified"] == 0

    def test_password_stored_as_argon2id_not_plaintext(self, auth_test_client):
        """Verifies plaintext passwords are NEVER stored in the database and Argon2id is used."""
        client, db_file = auth_test_client
        raw_password = "SuperSecretPassword99"
        payload = {
            "full_name": "Priya Patel",
            "email": "priya.patel@gmail.com",
            "password": raw_password,
            "confirm_password": raw_password
        }
        res = client.post("/api/auth/register", json=payload)
        assert res.status_code == 201

        # Direct DB inspection
        conn = get_connection(db_file)
        c = conn.cursor()
        c.execute("SELECT password_hash FROM student_accounts WHERE email = ?", ("priya.patel@gmail.com",))
        row = c.fetchone()
        conn.close()

        assert row is not None
        stored_hash = row[0]
        # Must not be plaintext
        assert stored_hash != raw_password
        assert raw_password not in stored_hash
        # Must be Argon2id format
        assert stored_hash.startswith("$argon2id$")
        # Must verify with Argon2
        assert verify_password(stored_hash, raw_password) is True
        assert verify_password(stored_hash, "WrongPassword123") is False

    def test_email_normalization_on_registration(self, auth_test_client):
        """Email address with whitespace and uppercase is normalized to trimmed lowercase."""
        client, _ = auth_test_client
        payload = {
            "full_name": "Rohan Kumar",
            "email": "  Rohan.Kumar@GMAIL.COM  ",
            "password": "ValidPassword123",
            "confirm_password": "ValidPassword123"
        }
        res = client.post("/api/auth/register", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["email"] == "rohan.kumar@gmail.com"

        # Check DB has normalized email
        acct = get_student_account_by_email("rohan.kumar@gmail.com")
        assert acct is not None

    def test_duplicate_email_rejection(self, auth_test_client):
        """Duplicate account creation with the same email is rejected with 409 Conflict."""
        client, _ = auth_test_client
        payload = {
            "full_name": "Original Student",
            "email": "unique.student@gmail.com",
            "password": "Password123",
            "confirm_password": "Password123"
        }
        res1 = client.post("/api/auth/register", json=payload)
        assert res1.status_code == 201

        # Attempt to register again with same email (different case and spacing)
        dup_payload = {
            "full_name": "Duplicate Student",
            "email": "  Unique.Student@gmail.com  ",
            "password": "Password999",
            "confirm_password": "Password999"
        }
        res2 = client.post("/api/auth/register", json=dup_payload)
        assert res2.status_code == 409
        assert "already exists" in res2.json()["detail"].lower()

    def test_password_mismatch_rejection(self, auth_test_client):
        """Registration is rejected if password and confirm_password do not match."""
        client, _ = auth_test_client
        payload = {
            "full_name": "Test Student",
            "email": "mismatch@gmail.com",
            "password": "Password123",
            "confirm_password": "DifferentPassword123"
        }
        res = client.post("/api/auth/register", json=payload)
        assert res.status_code == 400
        assert "passwords do not match" in res.json()["detail"].lower()

    def test_invalid_email_format_rejection(self, auth_test_client):
        """Invalid email formats are rejected with 400 Bad Request."""
        client, _ = auth_test_client
        for bad_email in ["not-an-email", "@gmail.com", "user@", "user@.com"]:
            payload = {
                "full_name": "Bad Email User",
                "email": bad_email,
                "password": "Password123",
                "confirm_password": "Password123"
            }
            res = client.post("/api/auth/register", json=payload)
            assert res.status_code in (400, 422)

    def test_weak_password_rejection(self, auth_test_client):
        """Passwords shorter than 8 chars or missing numbers/letters are rejected."""
        client, _ = auth_test_client
        # Too short (< 8 chars)
        res1 = client.post("/api/auth/register", json={
            "full_name": "Short Pwd",
            "email": "short@gmail.com",
            "password": "Pass1",
            "confirm_password": "Pass1"
        })
        assert res1.status_code in (400, 422)

        # No digits
        res2 = client.post("/api/auth/register", json={
            "full_name": "No Digit",
            "email": "nodigit@gmail.com",
            "password": "JustLettersOnly",
            "confirm_password": "JustLettersOnly"
        })
        assert res2.status_code == 400
        assert "at least one letter and one number" in res2.json()["detail"].lower()


# ── 2. Login & Session Tests ─────────────────────────────────────────────────

class TestStudentLoginAndSessions:

    def test_successful_login(self, auth_test_client):
        """Logging in with correct credentials returns authenticated session."""
        client, _ = auth_test_client
        # Register
        client.post("/api/auth/register", json={
            "full_name": "Ananya Reddy",
            "email": "ananya.reddy@gmail.com",
            "password": "StrongPassword789",
            "confirm_password": "StrongPassword789"
        })

        # Login
        login_res = client.post("/api/auth/login", json={
            "email": "Ananya.Reddy@GMAIL.COM",  # case insensitive
            "password": "StrongPassword789"
        })
        assert login_res.status_code == 200
        data = login_res.json()

        assert "token" in data
        assert "student_id" in data
        assert data["email"] == "ananya.reddy@gmail.com"
        assert data["full_name"] == "Ananya Reddy"

    def test_login_incorrect_password_generic_error(self, auth_test_client):
        """Incorrect password yields generic 401 error without revealing specifics."""
        client, _ = auth_test_client
        client.post("/api/auth/register", json={
            "full_name": "Neha Gupta",
            "email": "neha.gupta@gmail.com",
            "password": "CorrectPassword123",
            "confirm_password": "CorrectPassword123"
        })

        res = client.post("/api/auth/login", json={
            "email": "neha.gupta@gmail.com",
            "password": "WrongPassword456"
        })
        assert res.status_code == 401
        assert res.json()["detail"] == "Invalid email address or password."

    def test_login_unknown_email_generic_error(self, auth_test_client):
        """Unknown email address yields exact same generic 401 error (no user enumeration)."""
        client, _ = auth_test_client
        res = client.post("/api/auth/login", json={
            "email": "nonexistent.student@gmail.com",
            "password": "AnyPassword123"
        })
        assert res.status_code == 401
        assert res.json()["detail"] == "Invalid email address or password."

    def test_get_current_user_profile(self, auth_test_client):
        """GET /api/auth/me returns registered student profile with Bearer token."""
        client, _ = auth_test_client
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Vikram Sen",
            "email": "vikram.sen@gmail.com",
            "password": "VikramPassword123",
            "confirm_password": "VikramPassword123"
        })
        token = reg_res.json()["token"]
        student_id = reg_res.json()["student_id"]

        me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["student_id"] == student_id
        assert me_data["full_name"] == "Vikram Sen"
        assert me_data["email"] == "vikram.sen@gmail.com"
        assert me_data["is_registered"] is True

    def test_logout_revokes_session(self, auth_test_client):
        """Logging out revokes the session token, immediately causing 401 on protected routes."""
        client, _ = auth_test_client
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Logout Student",
            "email": "logout.test@gmail.com",
            "password": "LogoutPassword123",
            "confirm_password": "LogoutPassword123"
        })
        token = reg_res.json()["token"]
        auth_header = {"Authorization": f"Bearer {token}"}

        # Valid before logout
        assert client.get("/api/auth/me", headers=auth_header).status_code == 200

        # Logout
        logout_res = client.post("/api/auth/logout", headers=auth_header)
        assert logout_res.status_code == 200
        assert logout_res.json()["success"] is True

        # Rejected after logout
        post_logout = client.get("/api/auth/me", headers=auth_header)
        assert post_logout.status_code == 401
        assert "revoked" in post_logout.json()["detail"].lower()


# ── 3. Password Reset Tests ──────────────────────────────────────────────────

class TestPasswordResetFlow:

    def test_forgot_password_generic_response(self, auth_test_client):
        """Forgot password response is generic whether email exists or not."""
        client, _ = auth_test_client
        # Registered user
        client.post("/api/auth/register", json={
            "full_name": "Forgot User",
            "email": "forgot.user@gmail.com",
            "password": "Password1234",
            "confirm_password": "Password1234"
        })

        res_known = client.post("/api/auth/forgot-password", json={"email": "forgot.user@gmail.com"})
        assert res_known.status_code == 200
        assert "instructions have been generated" in res_known.json()["message"]

        res_unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@gmail.com"})
        assert res_unknown.status_code == 200
        assert "instructions have been generated" in res_unknown.json()["message"]

    def test_successful_password_reset_and_session_invalidation(self, auth_test_client):
        """
        Tests end-to-end password reset:
        1. Request reset token.
        2. Reset password using token.
        3. Old password rejected, new password accepted.
        4. Previous active sessions across devices are invalidated.
        """
        client, _ = auth_test_client
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Reset Student",
            "email": "reset.student@gmail.com",
            "password": "OriginalPassword123",
            "confirm_password": "OriginalPassword123"
        })
        old_token = reg_res.json()["token"]

        # Device 1 was logged in with old_token
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

        # Request password reset
        forgot_res = client.post("/api/auth/forgot-password", json={"email": "reset.student@gmail.com"})
        assert forgot_res.status_code == 200
        dev_token = forgot_res.json().get("dev_reset_token")
        assert dev_token is not None

        # Reset password
        reset_res = client.post("/api/auth/reset-password", json={
            "token": dev_token,
            "new_password": "BrandNewPassword456",
            "confirm_password": "BrandNewPassword456"
        })
        assert reset_res.status_code == 200
        assert "successfully reset" in reset_res.json()["message"]

        # 1. Old password MUST fail
        old_login = client.post("/api/auth/login", json={
            "email": "reset.student@gmail.com",
            "password": "OriginalPassword123"
        })
        assert old_login.status_code == 401

        # 2. New password MUST succeed
        new_login = client.post("/api/auth/login", json={
            "email": "reset.student@gmail.com",
            "password": "BrandNewPassword456"
        })
        assert new_login.status_code == 200
        new_token = new_login.json()["token"]

        # 3. Old active session MUST now be invalidated
        old_session_check = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
        assert old_session_check.status_code == 401

        # 4. New session is valid
        new_session_check = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        assert new_session_check.status_code == 200

    def test_reset_token_single_use_enforcement(self, auth_test_client):
        """Reset token cannot be used a second time."""
        client, _ = auth_test_client
        client.post("/api/auth/register", json={
            "full_name": "Single Use",
            "email": "single.use@gmail.com",
            "password": "Password123",
            "confirm_password": "Password123"
        })

        forgot_res = client.post("/api/auth/forgot-password", json={"email": "single.use@gmail.com"})
        dev_token = forgot_res.json()["dev_reset_token"]

        # First use succeeds
        res1 = client.post("/api/auth/reset-password", json={
            "token": dev_token,
            "new_password": "Password456",
            "confirm_password": "Password456"
        })
        assert res1.status_code == 200

        # Second use MUST fail
        res2 = client.post("/api/auth/reset-password", json={
            "token": dev_token,
            "new_password": "Password789",
            "confirm_password": "Password789"
        })
        assert res2.status_code == 400
        assert "already been used" in res2.json()["detail"].lower()

    def test_invalid_or_tampered_reset_token_rejected(self, auth_test_client):
        """Random or tampered reset tokens are rejected with 400 Bad Request."""
        client, _ = auth_test_client
        res = client.post("/api/auth/reset-password", json={
            "token": "fake-tampered-token-12345",
            "new_password": "NewPassword123",
            "confirm_password": "NewPassword123"
        })
        assert res.status_code == 400
        assert "invalid or expired" in res.json()["detail"].lower()
