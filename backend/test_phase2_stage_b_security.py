"""
test_phase2_stage_b_security.py
================================
Verification tests for Stage B:
- Consistent authentication and session-revocation enforcement across all student-specific endpoints
- Immediate rejection (401) of revoked sessions/tokens
- Immediate rejection (401) of expired tokens
- Immediate rejection (401) of tampered/invalid tokens
- Strict cross-student data isolation (403 when Student A tries to view Student B)
"""

import time
import uuid
import hashlib
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from main import app
from services.auth_utils import sign_student_identity
from services.database import (
    get_or_create_student,
    create_student_session,
    revoke_student_session,
    create_quiz_session
)

client = TestClient(app)


@pytest.fixture
def test_students():
    """Create two isolated test students and return their IDs and tokens."""
    student_a = f"test_sec_student_a_{uuid.uuid4().hex[:8]}"
    student_b = f"test_sec_student_b_{uuid.uuid4().hex[:8]}"

    get_or_create_student(student_a)
    get_or_create_student(student_b)

    token_a = sign_student_identity(student_a)
    token_b = sign_student_identity(student_b)

    return {
        "student_a": student_a,
        "token_a": token_a,
        "student_b": student_b,
        "token_b": token_b
    }


def test_valid_access_to_own_endpoints(test_students):
    """Student A can successfully access their own history, performance, and dashboard."""
    student_a = test_students["student_a"]
    headers = {"Authorization": f"Bearer {test_students['token_a']}"}

    # 1. History
    res_hist = client.get(f"/api/student/{student_a}/history", headers=headers)
    assert res_hist.status_code == 200
    assert res_hist.json()["student_id"] == student_a

    # 2. Performance
    res_perf = client.get(f"/api/student/{student_a}/performance", headers=headers)
    assert res_perf.status_code == 200
    assert res_perf.json()["student_id"] == student_a

    # 3. Dashboard
    res_dash = client.get(f"/api/student/{student_a}/dashboard", headers=headers)
    assert res_dash.status_code == 200
    assert res_dash.json()["student_id"] == student_a


def test_cross_student_access_rejected_with_403(test_students):
    """Student A cannot access Student B's history, performance, or dashboard."""
    student_b = test_students["student_b"]
    headers_a = {"Authorization": f"Bearer {test_students['token_a']}"}

    # 1. History
    res_hist = client.get(f"/api/student/{student_b}/history", headers=headers_a)
    assert res_hist.status_code == 403
    assert "Access denied" in res_hist.json()["detail"]

    # 2. Performance
    res_perf = client.get(f"/api/student/{student_b}/performance", headers=headers_a)
    assert res_perf.status_code == 403
    assert "Access denied" in res_perf.json()["detail"]

    # 3. Dashboard
    res_dash = client.get(f"/api/student/{student_b}/dashboard", headers=headers_a)
    assert res_dash.status_code == 403
    assert "Access denied" in res_dash.json()["detail"]


def test_revoked_session_rejected_immediately(test_students):
    """Once a token/session is revoked, it is rejected with 401 across all student endpoints."""
    student_a = test_students["student_a"]
    token_a = test_students["token_a"]
    headers = {"Authorization": f"Bearer {token_a}"}

    # Record active session in DB
    token_hash = hashlib.sha256(token_a.strip().encode("utf-8")).hexdigest()
    expires_at_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    create_student_session(student_id=student_a, token_hash=token_hash, expires_at=expires_at_iso)

    # Now revoke session (e.g. user logged out)
    revoked = revoke_student_session(token_hash=token_hash)
    assert revoked is True

    # 1. History
    res_hist = client.get(f"/api/student/{student_a}/history", headers=headers)
    assert res_hist.status_code == 401
    assert "revoked" in res_hist.json()["detail"].lower()

    # 2. Performance
    res_perf = client.get(f"/api/student/{student_a}/performance", headers=headers)
    assert res_perf.status_code == 401
    assert "revoked" in res_perf.json()["detail"].lower()

    # 3. Dashboard
    res_dash = client.get(f"/api/student/{student_a}/dashboard", headers=headers)
    assert res_dash.status_code == 401
    assert "revoked" in res_dash.json()["detail"].lower()

    # 4. Refresh
    res_refresh = client.post("/api/auth/refresh", headers=headers)
    assert res_refresh.status_code == 401
    assert "revoked" in res_refresh.json()["detail"].lower()


def test_tampered_and_malformed_token_rejected():
    """Tampered and malformed tokens must be rejected with 401."""
    fake_token = "invalid.token.payload"
    headers = {"Authorization": f"Bearer {fake_token}"}

    res_hist = client.get("/api/student/student_123/history", headers=headers)
    assert res_hist.status_code == 401

    res_dash = client.get("/api/student/student_123/dashboard", headers=headers)
    assert res_dash.status_code == 401


def test_expired_token_rejected(test_students):
    """Expired tokens (past clock-skew tolerance) must be rejected with 401."""
    student_a = test_students["student_a"]
    # Generate token expired 120 seconds ago (exceeding 60-second skew tolerance)
    expired_token = sign_student_identity(student_a, ttl_seconds=-120)
    headers = {"Authorization": f"Bearer {expired_token}"}

    res_hist = client.get(f"/api/student/{student_a}/history", headers=headers)
    assert res_hist.status_code == 401
    assert "expired" in res_hist.json()["detail"].lower()


def test_quiz_session_and_submit_cross_student_and_revocation(test_students):
    """
    Verify GET /api/quiz/session/{id} and POST /api/quiz/submit:
    - 403 when Student B tries to view or submit Student A's quiz
    - 401 when Student A submits with a revoked session
    """
    student_a = test_students["student_a"]
    token_a = test_students["token_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    student_b = test_students["student_b"]
    token_b = test_students["token_b"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    session_id = f"test_sec_quiz_session_{uuid.uuid4().hex[:8]}"
    sample_q = [{
        "question": "What is 2+2?",
        "options": {"A": "4", "B": "3", "C": "5", "D": "2"},
        "correct_answer": "A",
        "concept": "Arithmetic",
        "explanation": "2+2=4."
    }]

    create_quiz_session(
        session_id=session_id,
        student_id=student_a,
        course_code="CS101",
        course_name="Computer Science",
        topic_name="Math",
        questions=sample_q
    )

    # 1. Student A can access their own session
    res_own = client.get(f"/api/quiz/session/{session_id}", headers=headers_a)
    assert res_own.status_code == 200
    assert res_own.json()["student_id"] == student_a

    # 2. Student B is rejected with 403 on Student A's session
    res_b = client.get(f"/api/quiz/session/{session_id}", headers=headers_b)
    assert res_b.status_code == 403
    assert "belongs to another student" in res_b.json()["detail"].lower()

    # 3. Student B cannot submit answers for Student A's session
    submit_payload_b = {
        "quiz_id": session_id,
        "answers": ["A"],
        "student_id": student_b
    }
    res_sub_b = client.post("/api/quiz/submit", json=submit_payload_b, headers=headers_b)
    assert res_sub_b.status_code == 403
    assert "another student" in res_sub_b.json()["detail"].lower()

    # 4. Revoke Student A's session and verify submit is rejected with 401
    token_a_hash = hashlib.sha256(token_a.strip().encode("utf-8")).hexdigest()
    expires_at_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    create_student_session(student_id=student_a, token_hash=token_a_hash, expires_at=expires_at_iso)
    revoke_student_session(token_hash=token_a_hash)

    submit_payload_a = {
        "quiz_id": session_id,
        "answers": ["A"],
        "student_id": student_a
    }
    res_sub_revoked = client.post("/api/quiz/submit", json=submit_payload_a, headers=headers_a)
    assert res_sub_revoked.status_code == 401
    assert "revoked" in res_sub_revoked.json()["detail"].lower()


def test_study_materials_and_session_init_revocation_behavior(test_students):
    """
    Verify /api/study-materials rejects revoked session with 401,
    and /api/auth/session refuses to renew revoked identities.
    """
    student_a = test_students["student_a"]
    token_a = test_students["token_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Revoke Student A session
    token_a_hash = hashlib.sha256(token_a.strip().encode("utf-8")).hexdigest()
    expires_at_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    create_student_session(student_id=student_a, token_hash=token_a_hash, expires_at=expires_at_iso)
    revoke_student_session(token_hash=token_a_hash)

    # 1. Study materials with revoked token -> 401
    res_mats = client.get("/api/study-materials", headers=headers_a)
    assert res_mats.status_code == 401
    assert "revoked" in res_mats.json()["detail"].lower()

    # 2. Session init with revoked token in headers must NOT renew the revoked student_id
    res_init = client.post("/api/auth/session", headers=headers_a)
    assert res_init.status_code == 200
    new_data = res_init.json()
    assert new_data["student_id"] != student_a
    assert new_data["token"] != token_a


def test_missing_token_rejected_across_all_protected_endpoints(test_students):
    """Missing Authorization header must immediately return 401 across all protected routes."""
    student_a = test_students["student_a"]

    # 1. History
    assert client.get(f"/api/student/{student_a}/history").status_code == 401
    # 2. Performance
    assert client.get(f"/api/student/{student_a}/performance").status_code == 401
    # 3. Dashboard
    assert client.get(f"/api/student/{student_a}/dashboard").status_code == 401
    # 4. Refresh
    assert client.post("/api/auth/refresh").status_code == 401
    # 5. Study materials list
    assert client.get("/api/study-materials").status_code == 401
    # 6. Study material detail
    assert client.get("/api/study-materials/dummy_mat_id").status_code == 401
    # 7. Study material delete
    assert client.delete("/api/study-materials/dummy_mat_id").status_code == 401
    # 8. Quiz submit
    q_sess_id = f"dummy_session_missing_tok_{uuid.uuid4().hex[:8]}"
    create_quiz_session(
        session_id=q_sess_id,
        student_id=student_a,
        course_code="22AM301",
        course_name="Deep Learning",
        topic_name="Perceptrons",
        questions=[{"question": "Q?", "options": {"A":"1","B":"2","C":"3","D":"4"}, "correct_answer":"A", "explanation":"E"}]
    )
    submit_payload = {
        "quiz_id": q_sess_id,
        "answers": ["A"],
        "student_id": student_a
    }
    assert client.post("/api/quiz/submit", json=submit_payload).status_code == 401
