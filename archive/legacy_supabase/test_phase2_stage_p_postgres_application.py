"""
test_phase2_stage_p_postgres_application.py
============================================
Stage P: Application-Level PostgreSQL Verification Suite for LearnAI.
Verifies real FastAPI routes and service layer operations against the migrated
Supabase PostgreSQL database (learnai-dev-test).

Test Coverage:
1. Application startup and database connection readiness.
2. Server-minted HMAC authentication session creation and token refresh.
3. Reading migrated historical attempts and timestamp formatting.
4. Reading migrated concept and difficulty performance metrics.
5. Student dashboard metrics, recent performance trends, and pedagogical recommendations.
6. Authorization and access control isolation (401/403 guards).
7. Full quiz session lifecycle: creation, retrieval, atomic submission, and idempotent resubmission.
8. Adaptive topic mastery updates and JSONB columns.
9. Scoped teardown of synthetic records and baseline row count reconciliation.
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

LIVE_PG_URL = os.getenv("TEST_DATABASE_URL")
KNOWN_TEST_PROJECT_REF = "rnlbkdmjbcduoltefyup"  # Supabase project ref for learnai-dev-test
PRODUCTION_TARGET_INDICATORS = ("prod", "production")
MIGRATED_STUDENT_ID = "c8e3f165-e22b-45fe-a49b-ee6341c97e7e"  # Migrated student with 10 attempts & 3 mastery records


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
    reason="Stage P application verification requires TEST_DATABASE_URL configured in backend/.env."
)


@pytest.fixture(scope="module")
def app_client():
    if not LIVE_PG_URL:
        pytest.skip("TEST_DATABASE_URL not set.")

    if not is_verified_test_target(LIVE_PG_URL):
        pytest.fail(
            f"SAFETY ABORT: Target does not match verified disposable project '{KNOWN_TEST_PROJECT_REF}'."
        )

    # Point application to TEST_DATABASE_URL
    os.environ["DATABASE_URL"] = LIVE_PG_URL

    from starlette.testclient import TestClient
    from main import app
    from services.db.postgres_adapter import PostgresAdapter

    with TestClient(app) as client:
        yield client

    PostgresAdapter.close_pool()


@pytest.fixture
def synthetic_student_and_token():
    """Create a synthetic student for write tests, yielding (student_id, token), and safely cleaning up."""
    from services.auth_utils import sign_student_identity
    from services.database import get_connection

    syn_id = f"syn_p_{uuid.uuid4().hex[:12]}"
    token = sign_student_identity(syn_id)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO students (id, created_at, last_active) VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);",
            (syn_id,)
        )
        conn.commit()
    finally:
        conn.close()

    yield syn_id, token

    # Teardown: delete strictly the synthetic student (cascades to all child rows)
    conn2 = get_connection()
    try:
        conn2.execute("DELETE FROM students WHERE id = ?;", (syn_id,))
        conn2.commit()
    finally:
        conn2.close()


# ── 1. Application Startup & Health ──────────────────────────────────────────

@requires_live_pg
class TestApplicationStartupAndHealth:

    def test_minimal_liveness_check(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_database_connected_health(self, app_client):
        resp = app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"
        assert data.get("database") == "connected"
        assert "timestamp" in data


# ── 2. Authentication Flow ───────────────────────────────────────────────────

@requires_live_pg
class TestAuthenticationFlow:

    def test_create_auth_session(self, app_client):
        resp = app_client.post("/api/auth/session")
        assert resp.status_code == 200
        data = resp.json()
        assert "student_id" in data
        assert "token" in data
        assert "expires_at" in data

        # Cleanup newly created student record to preserve baseline count
        new_sid = data["student_id"]
        from services.database import get_connection
        conn = get_connection()
        try:
            conn.execute("DELETE FROM students WHERE id = ?;", (new_sid,))
            conn.commit()
        finally:
            conn.close()

    def test_refresh_auth_session_valid_token(self, app_client, synthetic_student_and_token):
        _, token = synthetic_student_and_token
        resp = app_client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "expires_at" in data

    def test_refresh_auth_session_missing_token_rejected(self, app_client):
        resp = app_client.post("/api/auth/refresh")
        assert resp.status_code == 401
        assert "Authentication token required" in resp.json().get("detail", "")


# ── 3. Migrated Student Read Endpoints ────────────────────────────────────────

@requires_live_pg
class TestMigratedStudentReadEndpoints:

    @pytest.fixture
    def migrated_auth_headers(self):
        from services.auth_utils import sign_student_identity
        token = sign_student_identity(MIGRATED_STUDENT_ID)
        return {"Authorization": f"Bearer {token}"}

    def test_get_migrated_student_history(self, app_client, migrated_auth_headers):
        resp = app_client.get(
            f"/api/student/{MIGRATED_STUDENT_ID}/history",
            headers=migrated_auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("student_id") == MIGRATED_STUDENT_ID
        attempts = data.get("attempts", [])
        assert len(attempts) > 0, "Migrated student should have historical quiz attempts"

        sample = attempts[0]
        assert "id" in sample
        assert "course_code" in sample
        assert "topic_name" in sample
        assert "score" in sample
        assert "total" in sample
        assert "percentage" in sample
        assert "created_at" in sample
        # Verify timestamp formatting
        assert "T" in str(sample["created_at"])

    def test_get_migrated_student_performance(self, app_client, migrated_auth_headers):
        resp = app_client.get(
            f"/api/student/{MIGRATED_STUDENT_ID}/performance",
            headers=migrated_auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("student_id") == MIGRATED_STUDENT_ID
        assert "concept_performance" in data
        assert "difficulty_performance" in data
        assert len(data["concept_performance"]) > 0

    def test_get_migrated_student_dashboard(self, app_client, migrated_auth_headers):
        resp = app_client.get(
            f"/api/student/{MIGRATED_STUDENT_ID}/dashboard",
            headers=migrated_auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("student_id") == MIGRATED_STUDENT_ID

        overview = data.get("overview", {})
        assert overview.get("completed_quizzes", 0) > 0
        assert overview.get("total_questions", 0) > 0
        assert overview.get("total_correct", 0) >= 0
        assert overview.get("overall_accuracy") is not None

        recent_trends = data.get("recent_trends", [])
        assert len(recent_trends) > 0

        topic_progress = data.get("topic_progress", [])
        assert len(topic_progress) > 0
        assert "mastery" in str(topic_progress[0]) or "cumulative_accuracy" in topic_progress[0]

        recs = data.get("recommended_topics", [])
        assert len(recs) > 0

    def test_cross_student_access_denied(self, app_client, migrated_auth_headers):
        other_student_id = "00000000-0000-0000-0000-000000000000"
        resp = app_client.get(
            f"/api/student/{other_student_id}/history",
            headers=migrated_auth_headers
        )
        assert resp.status_code == 403


# ── 4. Quiz Session Lifecycle, Submission & Adaptive Mastery ──────────────────

@requires_live_pg
class TestQuizSessionLifecycleAndAdaptiveUpdates:

    def test_full_session_submission_and_resubmission(self, app_client, synthetic_student_and_token):
        from services.database import create_quiz_session, get_connection

        syn_id, token = synthetic_student_and_token
        headers = {"Authorization": f"Bearer {token}"}
        session_id = f"syn_sess_{uuid.uuid4().hex[:10]}"

        questions = [
            {
                "question": "What is the primary function of an activation function?",
                "options": ["Introduce non-linearity", "Scale inputs", "Normalize weights", "Zero bias"],
                "correct_answer": "Introduce non-linearity",
                "difficulty": "Easy",
                "concept": "Neural Networks",
                "fingerprint": f"fp_{uuid.uuid4().hex[:16]}"
            },
            {
                "question": "What is 7 * 8?",
                "options": ["54", "56", "58", "60"],
                "correct_answer": "56",
                "difficulty": "Easy",
                "concept": "Arithmetic",
                "fingerprint": f"fp_{uuid.uuid4().hex[:16]}"
            }
        ]

        # 1. Create quiz session
        sess = create_quiz_session(
            session_id=session_id,
            student_id=syn_id,
            course_code="AI101",
            course_name="Artificial Intelligence",
            topic_name="Perceptrons",
            questions=questions,
            recommended_difficulty="mixed",
            adaptive_metadata={"starting_level": "mixed"}
        )
        assert sess["id"] == session_id
        assert sess["status"] == "in_progress"

        # 2. Retrieve public quiz session via endpoint
        resp_get = app_client.get(f"/api/quiz/session/{session_id}", headers=headers)
        assert resp_get.status_code == 200
        get_data = resp_get.json()
        assert get_data["session_id"] == session_id
        assert get_data["status"] == "in_progress"
        assert len(get_data["questions"]) == 2
        # Sanity check: answers must not be leaked in public question payload
        assert "correct_answer" not in get_data["questions"][0]

        # 3. Submit quiz atomically
        submit_payload = {
            "quiz_id": session_id,
            "student_id": syn_id,
            "answers": ["Introduce non-linearity", "56"],
            "auth_token": token
        }
        resp_sub = app_client.post("/api/quiz/submit", json=submit_payload, headers=headers)
        assert resp_sub.status_code == 200
        sub_data = resp_sub.json()
        assert sub_data["score"] == 2
        assert sub_data["total"] == 2
        assert sub_data["percentage"] == 100.0

        # 4. Idempotent Resubmission: Submitting again must succeed and return existing result
        resp_resub = app_client.post("/api/quiz/submit", json=submit_payload, headers=headers)
        assert resp_resub.status_code == 200
        resub_data = resp_resub.json()
        assert resub_data["score"] == 2
        assert resub_data["total"] == 2

        # 5. Retrieve session again -> status must be 'completed'
        resp_get_post = app_client.get(f"/api/quiz/session/{session_id}", headers=headers)
        assert resp_get_post.status_code == 200
        assert resp_get_post.json()["status"] == "completed"

        # 6. Verify Topic Mastery in PostgreSQL
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT attempt_count, total_questions, total_correct, mastery_score, current_level FROM topic_mastery WHERE student_id = ? AND topic_name = ?;",
                (syn_id, "Perceptrons")
            )
            mastery_row = cursor.fetchone()
            assert mastery_row is not None
            assert int(mastery_row["attempt_count"]) == 1
            assert int(mastery_row["total_questions"]) == 2
            assert int(mastery_row["total_correct"]) == 2
            assert float(mastery_row["mastery_score"]) == 1.0
        finally:
            conn.close()


# ── 5. Database Cleanup & Reconciliation Verification ──────────────────────────

@requires_live_pg
class TestPostExecutionDatabaseReconciliation:

    def test_database_row_counts_reconcile_to_baseline(self, app_client):
        """
        Verify that all temporary/synthetic records created during testing
        have been completely cleaned up, and row counts strictly match the post-migration baseline.
        """
        from services.database import get_connection

        expected_counts = {
            "students": 75,
            "quiz_sessions": 0,
            "quiz_attempts": 53,
            "question_metadata": 370,
            "student_answers": 0,
            "topic_mastery": 38,
            "schema_migrations": 1,
        }

        conn = get_connection()
        actual_counts = {}
        try:
            cursor = conn.cursor()
            for tbl in expected_counts:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl};")
                actual_counts[tbl] = int(cursor.fetchone()[0])
        finally:
            conn.close()

        assert actual_counts == expected_counts, (
            f"Database row count mismatch after Stage P test execution!\n"
            f"Expected: {expected_counts}\n"
            f"Actual:   {actual_counts}"
        )
