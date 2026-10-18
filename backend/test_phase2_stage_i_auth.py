"""
test_phase2_stage_i_auth.py
===========================
Comprehensive Test Suite for Stage I: Server-Verified HMAC Authentication.
Verifies token issuance, validation, expiration, tampering, cross-student protection,
session access, submission, dashboard endpoints, and missing secret fallback.
"""

import pytest
import sqlite3
import time
import os
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import main
from main import app
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_quiz_session,
    save_quiz_attempt
)
from services.auth_utils import (
    sign_student_identity,
    verify_student_identity,
    auth_header_for,
    get_session_secret
)

SAMPLE_QUESTIONS = [
    {
        "id": i,
        "question": f"Sample authenticated question {i}?",
        "options": {"A": "Option 1", "B": "Option 2", "C": "Option 3", "D": "Option 4"},
        "correct_answer": "A",
        "explanation": f"Explanation for question {i}",
        "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
        "concept": "Core Concept"
    }
    for i in range(10)
]


@pytest.fixture
def isolated_auth_client(tmp_path, monkeypatch):
    """Fixture providing an isolated SQLite database and fresh TestClient."""
    db_file = tmp_path / "learnai_auth_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    return client, db_file


# ── 1. Token Issuance and Lifecycle ──────────────────────────────────────────

class TestTokenIssuanceAndLifecycle:

    def test_create_auth_session_returns_valid_token_and_uuid(self, isolated_auth_client):
        """POST /api/auth/session mints a new student identity and valid HMAC token."""
        client, db_file = isolated_auth_client
        res = client.post("/api/auth/session")
        assert res.status_code == 200
        data = res.json()

        assert "student_id" in data
        assert "token" in data
        assert "expires_at" in data
        assert data["expires_at"] > int(time.time())

        # Verify token cryptographically
        is_valid, decoded_id, err = verify_student_identity(data["token"])
        assert is_valid is True
        assert decoded_id == data["student_id"]
        assert err is None

        # Verify student exists in DB
        conn = get_connection(db_file)
        c = conn.cursor()
        c.execute("SELECT id FROM students WHERE id = ?", (data["student_id"],))
        assert c.fetchone() is not None
        conn.close()

    def test_create_auth_session_with_existing_token_refreshes_token(self, isolated_auth_client):
        """POST /api/auth/session with existing valid token returns refreshed token for same student."""
        client, _ = isolated_auth_client
        # Initial session
        res1 = client.post("/api/auth/session")
        data1 = res1.json()

        # Present existing token
        headers = {"Authorization": f"Bearer {data1['token']}"}
        res2 = client.post("/api/auth/session", headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()

        assert data2["student_id"] == data1["student_id"]
        is_valid, decoded_id, _ = verify_student_identity(data2["token"])
        assert is_valid is True
        assert decoded_id == data1["student_id"]

    def test_refresh_endpoint_with_valid_token_extends_ttl(self, isolated_auth_client):
        """POST /api/auth/refresh with valid token issues a refreshed token."""
        client, _ = isolated_auth_client
        student_id = "test-stud-refresh-01"
        token = sign_student_identity(student_id)

        res = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        data = res.json()
        assert data["student_id"] == student_id

        is_valid, decoded_id, err = verify_student_identity(data["token"])
        assert is_valid is True
        assert decoded_id == student_id

    def test_refresh_endpoint_missing_token_returns_401(self, isolated_auth_client):
        """POST /api/auth/refresh without token returns HTTP 401."""
        client, _ = isolated_auth_client
        res = client.post("/api/auth/refresh")
        assert res.status_code == 401

    def test_refresh_endpoint_tampered_token_returns_401(self, isolated_auth_client):
        """POST /api/auth/refresh with tampered token signature returns HTTP 401."""
        client, _ = isolated_auth_client
        token = sign_student_identity("test-stud-tamper")
        parts = token.split(".")
        tampered_token = f"{parts[0]}.{parts[1]}.badbadbadsignature"

        res = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {tampered_token}"})
        assert res.status_code == 401
        assert "Invalid authentication token" in res.json()["detail"]

    def test_generate_quiz_for_existing_student_without_token_returns_401(self, isolated_auth_client):
        """POST /api/quiz/generate with an existing student_id but no token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        get_or_create_student("stud_already_exists", conn=conn)
        conn.commit()
        conn.close()

        res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_already_exists"
        })
        assert res.status_code == 401
        assert "Authentication token required" in res.json()["detail"]

    def test_generate_quiz_fails_fast_on_existing_student_before_ai_service(self, isolated_auth_client):
        """Verify that an existing student without token is rejected with 401 before calling AI generation."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        get_or_create_student("stud_fail_fast_check", conn=conn)
        conn.commit()
        conn.close()

        with patch("main.generate_quiz", side_effect=RuntimeError("AI service should never be called")):
            res = client.post("/api/quiz/generate", json={
                "course_code": "22AM301",
                "topic": "McCulloch-Pitts Neuron",
                "student_id": "stud_fail_fast_check"
            })
            assert res.status_code == 401
            assert "Authentication token required" in res.json()["detail"]


# ── 2. Protected Session Retrieval ───────────────────────────────────────────

class TestProtectedSessionRetrieval:

    def test_session_retrieval_with_valid_token_succeeds(self, isolated_auth_client):
        """GET /api/quiz/session/{id} with valid Bearer token for session owner returns 200."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_valid", conn=conn)
        create_quiz_session(
            session_id="sess_auth_valid",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        headers = auth_header_for(student_id)
        res = client.get("/api/quiz/session/sess_auth_valid", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == "sess_auth_valid"
        assert data["student_id"] == student_id
        assert len(data["questions"]) == 10

    def test_session_retrieval_without_token_returns_401(self, isolated_auth_client):
        """GET /api/quiz/session/{id} without token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_missing", conn=conn)
        create_quiz_session(
            session_id="sess_auth_missing",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        res = client.get(f"/api/quiz/session/sess_auth_missing?student_id={student_id}")
        assert res.status_code == 401
        assert "Authentication token required" in res.json()["detail"]

    def test_session_retrieval_with_tampered_token_returns_401(self, isolated_auth_client):
        """GET /api/quiz/session/{id} with tampered token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_tamp", conn=conn)
        create_quiz_session(
            session_id="sess_auth_tamp",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        token = sign_student_identity(student_id)
        parts = token.split(".")
        tampered_token = f"{parts[0]}.{parts[1]}.ff{parts[2][2:]}"

        res = client.get("/api/quiz/session/sess_auth_tamp", headers={"Authorization": f"Bearer {tampered_token}"})
        assert res.status_code == 401

    def test_session_retrieval_with_expired_token_returns_401(self, isolated_auth_client):
        """GET /api/quiz/session/{id} with expired token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_exp", conn=conn)
        create_quiz_session(
            session_id="sess_auth_exp",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        expired_token = sign_student_identity(student_id, ttl_seconds=-200)
        res = client.get("/api/quiz/session/sess_auth_exp", headers={"Authorization": f"Bearer {expired_token}"})
        assert res.status_code == 401
        assert "expired" in res.json()["detail"].lower()

    def test_session_retrieval_by_cross_student_token_returns_403(self, isolated_auth_client):
        """GET /api/quiz/session/{id} using Student B's valid token for Student A's session returns 403."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("student_victim_a", conn=conn)
        student_b = get_or_create_student("student_attacker_b", conn=conn)
        create_quiz_session(
            session_id="sess_cross_test",
            student_id=student_a,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Student B presents their own valid token to access Student A's session
        headers_b = auth_header_for(student_b)
        res = client.get("/api/quiz/session/sess_cross_test", headers=headers_b)
        assert res.status_code == 403
        assert "Access denied" in res.json()["detail"]

    def test_session_retrieval_nonexistent_session_returns_404(self, isolated_auth_client):
        """GET /api/quiz/session/nonexistent returns HTTP 404 regardless of token."""
        client, _ = isolated_auth_client
        res = client.get("/api/quiz/session/nonexistent-uuid-9999")
        assert res.status_code == 404


# ── 3. Protected Quiz Submission ─────────────────────────────────────────────

class TestProtectedQuizSubmission:

    def test_submit_quiz_with_valid_token_succeeds(self, isolated_auth_client):
        """POST /api/quiz/submit with valid Bearer token grades and completes quiz."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_sub_valid", conn=conn)
        create_quiz_session(
            session_id="sess_sub_ok",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        headers = auth_header_for(student_id)
        payload = {
            "quiz_id": "sess_sub_ok",
            "answers": ["A"] * 10,
            "student_id": student_id
        }
        res = client.post("/api/quiz/submit", json=payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["score"] == 10
        assert data["student_id"] == student_id

    def test_submit_quiz_with_body_auth_token_succeeds(self, isolated_auth_client):
        """POST /api/quiz/submit with auth_token in JSON payload succeeds."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_sub_body", conn=conn)
        create_quiz_session(
            session_id="sess_sub_body",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        token = sign_student_identity(student_id)
        payload = {
            "quiz_id": "sess_sub_body",
            "answers": ["A"] * 10,
            "student_id": student_id,
            "auth_token": token
        }
        res = client.post("/api/quiz/submit", json=payload)
        assert res.status_code == 200

    def test_submit_quiz_without_token_returns_401(self, isolated_auth_client):
        """POST /api/quiz/submit without token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_sub_notoken", conn=conn)
        create_quiz_session(
            session_id="sess_sub_notoken",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        payload = {
            "quiz_id": "sess_sub_notoken",
            "answers": ["A"] * 10,
            "student_id": student_id
        }
        res = client.post("/api/quiz/submit", json=payload)
        assert res.status_code == 401
        assert "Authentication token required" in res.json()["detail"]

    def test_submit_quiz_with_cross_student_token_returns_403(self, isolated_auth_client):
        """POST /api/quiz/submit with Student B's token for Student A's session returns 403."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("stud_victim_sub", conn=conn)
        student_b = get_or_create_student("stud_attacker_sub", conn=conn)
        create_quiz_session(
            session_id="sess_cross_sub",
            student_id=student_a,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        headers_b = auth_header_for(student_b)
        payload = {
            "quiz_id": "sess_cross_sub",
            "answers": ["A"] * 10,
            "student_id": student_a
        }
        res = client.post("/api/quiz/submit", json=payload, headers=headers_b)
        assert res.status_code == 403


# ── 4. Protected Student Dashboard, History & Performance ────────────────────

class TestProtectedStudentDashboardAndHistory:

    def test_dashboard_with_valid_token_succeeds(self, isolated_auth_client):
        """GET /api/student/{id}/dashboard with valid token returns HTTP 200."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_dash_auth", conn=conn)
        conn.close()

        headers = auth_header_for(student_id)
        res = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["student_id"] == student_id
        assert "overview" in data

    def test_dashboard_without_token_returns_401(self, isolated_auth_client):
        """GET /api/student/{id}/dashboard without token returns HTTP 401."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_dash_noauth", conn=conn)
        conn.close()

        res = client.get(f"/api/student/{student_id}/dashboard")
        assert res.status_code == 401

    def test_dashboard_cross_student_token_returns_403(self, isolated_auth_client):
        """GET /api/student/{id}/dashboard with another student's token returns 403."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("stud_dash_victim", conn=conn)
        student_b = get_or_create_student("stud_dash_attacker", conn=conn)
        conn.close()

        # Attacker B attempts to view Victim A's dashboard
        headers_b = auth_header_for(student_b)
        res = client.get(f"/api/student/{student_a}/dashboard", headers=headers_b)
        assert res.status_code == 403
        assert "Access denied" in res.json()["detail"]

    def test_history_and_performance_require_valid_token(self, isolated_auth_client):
        """GET /history and /performance reject missing tokens (401) and cross-student tokens (403)."""
        client, db_file = isolated_auth_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("stud_hp_a", conn=conn)
        student_b = get_or_create_student("stud_hp_b", conn=conn)
        conn.close()

        # Missing token
        assert client.get(f"/api/student/{student_a}/history").status_code == 401
        assert client.get(f"/api/student/{student_a}/performance").status_code == 401

        # Cross-student token
        headers_b = auth_header_for(student_b)
        assert client.get(f"/api/student/{student_a}/history", headers=headers_b).status_code == 403
        assert client.get(f"/api/student/{student_a}/performance", headers=headers_b).status_code == 403

        # Valid token
        headers_a = auth_header_for(student_a)
        assert client.get(f"/api/student/{student_a}/history", headers=headers_a).status_code == 200
        assert client.get(f"/api/student/{student_a}/performance", headers=headers_a).status_code == 200


# ── 5. Signing Secret Missing and Fallback ───────────────────────────────────

class TestSigningSecretMissingAndFallback:

    def test_missing_signing_secret_uses_dev_fallback_safely(self, monkeypatch):
        """When SESSION_SECRET_KEY is absent, system safely uses dev fallback without crashing."""
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.delenv("LEARNAI_SECRET_KEY", raising=False)

        secret = get_session_secret()
        assert len(secret) >= 32
        assert b"learnai" in secret

        # Verify tokens can still be signed and verified
        student_id = "test-fallback-secret"
        token = sign_student_identity(student_id)
        is_valid, decoded_id, err = verify_student_identity(token)
        assert is_valid is True
        assert decoded_id == student_id

    def test_token_signed_with_different_secret_rejected(self):
        """Tokens signed with a different secret are rejected with Signature mismatch."""
        student_id = "test-diff-secret"
        token = sign_student_identity(student_id, secret_key=b"secret-key-alpha-32-bytes-long!")

        is_valid, _, err = verify_student_identity(token, secret_key=b"secret-key-beta--32-bytes-long!")
        assert is_valid is False
        assert "Signature mismatch" in err
