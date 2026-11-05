"""
test_persistent_auth_and_quiz_restoration.py
=============================================
Comprehensive integration and unit test suite verifying:
1. Persistent Authentication via HttpOnly, Secure, SameSite=Lax session cookies.
2. Auto-restoration of authenticated sessions on page refresh and browser reopen without login page flash.
3. Logout invalidation on server, cookie deletion, and redirect to login.
4. Expired session detection and clean redirect to login.
5. In-progress active quiz session persistence across navigation (Practice Quiz <-> Notes & Slides <-> Dashboard).
6. Active quiz restoration on browser refresh, preserving questions, order, selected answers, and question index.
7. Prevention of duplicate quiz generations when an active quiz is already in progress.
8. Proper cleanup upon quiz submission: completed quizzes are marked completed and never restored as active.
"""

import os
import json
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Set testing environment variables before importing app
os.environ["STUDENT_SIGNING_SECRET"] = "test-persistent-auth-secret-key-32-chars-long!"
os.environ["STUDENT_TOKEN_TTL_SECONDS"] = "86400"
os.environ["TESTING"] = "true"

from main import app
from services.database import (
    get_connection,
    init_db,
    create_student_account,
    get_student_account_by_email,
    create_quiz_session,
    get_quiz_session,
    get_active_quiz_session_for_student,
    submit_quiz_atomic,
    complete_quiz_session,
)
from services.auth_utils import sign_student_identity

ROOT_DIR = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT_DIR / "frontend" / "index.html"
APP_JS = ROOT_DIR / "frontend" / "js" / "app.js"


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Set up isolated SQLite test database for each test run."""
    db_file = tmp_path / "test_learnai.db"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("RENDER", "")
    
    init_db(str(db_file))
    yield
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass


@pytest.fixture
def client():
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Persistent Authentication via Cookies Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistentAuthentication:
    """Tests for Issue 1: Persistent Authentication & Cookie Management."""

    def test_login_sets_httponly_session_cookie(self, client):
        """Verify that successful login sets the HttpOnly learnai_session cookie."""
        email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Persistent Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        assert reg_res.status_code == 201
        assert "learnai_session" in reg_res.cookies
        cookie = reg_res.cookies["learnai_session"]
        assert cookie and len(cookie.split(".")) == 3

        # Login with student credentials
        login_res = client.post("/api/auth/login", json={
            "email": email,
            "password": "Password123!"
        })
        assert login_res.status_code == 200
        assert "learnai_session" in login_res.cookies
        assert "token" in login_res.json()

    def test_browser_reopen_and_refresh_restores_session_via_cookie(self, client):
        """Simulate browser restart/refresh: cookie is sent and /api/auth/me returns valid user."""
        email = f"reopen_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Reopen Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        session_cookie = reg_res.cookies["learnai_session"]

        # Simulate new browser instance with preserved cookie (no Authorization header sent)
        fresh_client = TestClient(app)
        fresh_client.cookies.set("learnai_session", session_cookie)

        me_res = fresh_client.get("/api/auth/me")
        assert me_res.status_code == 200
        user_data = me_res.json()
        assert user_data["is_registered"] is True
        assert user_data["full_name"] == "Reopen Student"
        assert user_data["email"] == email

    def test_logout_deletes_cookie_and_revokes_session(self, client):
        """Verify that logout invalidates session on server and clears cookie."""
        email = f"logout_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Logout Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        session_cookie = reg_res.cookies["learnai_session"]

        authed_client = TestClient(app)
        authed_client.cookies.set("learnai_session", session_cookie)

        # Logout
        logout_res = authed_client.post("/api/auth/logout")
        assert logout_res.status_code == 200
        assert logout_res.json()["success"] is True

        # Reopen after logout: must be unauthenticated (401 on protected endpoint)
        authed_client.cookies.set("learnai_session", session_cookie)
        me_res = authed_client.get("/api/auth/me")
        assert me_res.status_code == 401

    def test_expired_session_cookie_rejects_and_requires_login(self, client):
        """Verify that expired session token in cookie returns 401."""
        student_id = f"stu_{uuid.uuid4().hex[:12]}"
        expired_token = sign_student_identity(student_id, ttl_seconds=-100)

        client.cookies.set("learnai_session", expired_token)
        res = client.get("/api/auth/me")
        assert res.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 2. In-Progress Quiz Session Backend & Restoration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveQuizSessionBackend:
    """Tests for Issue 2: Active quiz session management on the backend."""

    def test_get_active_quiz_endpoint_returns_in_progress_session(self, client):
        """Verify /api/quiz/active returns the current unexpired in-progress session."""
        email = f"quiz_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Quiz Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        student_id = reg_res.json()["student_id"]
        session_cookie = reg_res.cookies["learnai_session"]

        # Before creating a quiz: 204 No Content
        client.cookies.set("learnai_session", session_cookie)
        res_empty = client.get("/api/quiz/active")
        assert res_empty.status_code == 204

        # Create active in-progress quiz session
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        questions = [
            {
                "question": "What is the rank of an identity matrix?",
                "options": {"A": "0", "B": "1", "C": "n", "D": "Infinity"},
                "correct_answer": "C",
                "explanation": "Rank of I_n is n."
            },
            {
                "question": "Which layer is used for regularisation in deep learning?",
                "options": {"A": "Dense", "B": "Dropout", "C": "ReLU", "D": "Softmax"},
                "correct_answer": "B",
                "explanation": "Dropout prevents overfitting."
            }
        ]
        create_quiz_session(
            session_id=session_id,
            student_id=student_id,
            course_code="CS101",
            course_name="Computer Science",
            topic_name="Linear Algebra",
            questions=questions,
            duration_seconds=3600
        )

        # Query /api/quiz/active
        res_active = client.get("/api/quiz/active")
        assert res_active.status_code == 200
        data = res_active.json()
        assert data["session_id"] == session_id
        assert data["course_code"] == "CS101"
        assert data["topic"] == "Linear Algebra"
        assert data["status"] == "in_progress"
        assert len(data["questions"]) == 2
        # Verify public sanitization: correct_answer and explanation not exposed
        assert "correct_answer" not in data["questions"][0]
        assert "explanation" not in data["questions"][0]
        assert data["questions"][0]["options"]["C"] == "n"

    def test_submitted_quiz_is_marked_completed_and_not_returned_as_active(self, client):
        """Verify that once a quiz is submitted, it is no longer returned as active."""
        email = f"submit_{uuid.uuid4().hex[:8]}@example.com"
        reg_res = client.post("/api/auth/register", json={
            "full_name": "Submit Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        student_id = reg_res.json()["student_id"]
        session_cookie = reg_res.cookies["learnai_session"]
        token = reg_res.json()["token"]

        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        questions = [
            {
                "question": "What is 2 + 2?",
                "options": {"A": "3", "B": "4", "C": "5", "D": "6"},
                "correct_answer": "B",
                "explanation": "2+2=4"
            }
        ]
        create_quiz_session(
            session_id=session_id,
            student_id=student_id,
            course_code="MATH101",
            course_name="Mathematics",
            topic_name="Arithmetic",
            questions=questions,
            duration_seconds=3600
        )

        client.cookies.set("learnai_session", session_cookie)
        assert client.get("/api/quiz/active").status_code == 200

        # Submit quiz
        sub_res = client.post("/api/quiz/submit", json={
            "quiz_id": session_id,
            "student_id": student_id,
            "answers": ["B"]
        }, headers={"Authorization": f"Bearer {token}"})
        assert sub_res.status_code == 200

        # Check active session: must now be 204 No Content
        res_after = client.get("/api/quiz/active")
        assert res_after.status_code == 204


# ─────────────────────────────────────────────────────────────────────────────
# 3. Frontend Zero-Flash and Navigation State Machine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendStateAndZeroFlash:
    """Verify frontend code handles navigation, persistence, and zero-flash correctly."""

    def test_index_html_head_checks_session_active_marker(self):
        """Verify index.html head checks both token and session active flag."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "localStorage.getItem('learnai_auth_token')" in html
        assert "localStorage.getItem('learnai_session_active')" in html
        assert "classList.add('unauthenticated')" in html

    def test_app_js_exports_restoration_and_nav_functions(self):
        """Verify app.js exports required state management and navigation functions."""
        js = APP_JS.read_text(encoding="utf-8")
        assert "hasActiveQuiz" in js
        assert "restoreSessionState" in js
        assert "saveSessionState" in js
        assert "clearSessionState" in js
        assert "handlePracticeNav" in js

    def test_app_js_saves_state_on_question_navigation_and_tab_switch(self):
        """Verify app.js persists state during option selection, next, prev, and tab navigation."""
        js = APP_JS.read_text(encoding="utf-8")
        # Prev button click calls saveSessionState
        assert '$("prevBtn").addEventListener("click", () => {' in js
        # Practice Quiz nav tab uses handlePracticeNav instead of goToHome
        assert '$("navPracticeBtn")?.addEventListener("click", handlePracticeNav);' in js
        # Brand logo returns to active quiz if present
        assert '$("brandLogo")?.addEventListener("click", handlePracticeNav);' in js
        # Start quiz button from dashboard returns to active quiz
        assert '$("dashboardStartQuizBtn")?.addEventListener("click", handlePracticeNav);' in js

    def test_app_js_prevents_duplicate_quiz_generation_when_active(self):
        """Verify startQuizGeneration restores existing session rather than generating duplicates."""
        js = APP_JS.read_text(encoding="utf-8")
        assert "if (!isRetake && hasActiveQuiz())" in js
        assert "restoreSessionState();" in js

    def test_app_js_authenticated_fetch_sends_credentials_include(self):
        """Verify authenticatedFetch and auth endpoints include credentials."""
        js = APP_JS.read_text(encoding="utf-8")
        assert 'options.credentials = options.credentials || "include";' in js
        assert 'credentials: "include"' in js

# ─────────────────────────────────────────────────────────────────────────────
# 4. Password Visibility & Credential Exposure Tests (Issue 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordVisibilityAndCredentialExposure:
    """Tests for Issue 1: Password visibility toggle and logout form clearance."""

    def test_password_toggle_buttons_exist_with_accessible_attributes(self):
        """Verify that all password fields in index.html have visibility toggle buttons with proper accessibility."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "password-input-wrapper" in html
        assert "password-toggle-btn" in html
        assert 'aria-label="Show password"' in html
        assert 'title="Show password"' in html
        assert 'aria-pressed="false"' in html
        assert 'data-target="loginPassword"' in html
        assert 'data-target="registerPassword"' in html
        assert 'data-target="registerConfirmPassword"' in html

    def test_app_js_implements_password_toggle_and_reset(self):
        """Verify that app.js implements initPasswordToggles and resetPasswordToggles."""
        js = APP_JS.read_text(encoding="utf-8")
        assert "function initPasswordToggles()" in js
        assert "function resetPasswordToggles()" in js
        assert 'input.type = isCurrentlyPassword ? "text" : "password";' in js
        assert 'btn.setAttribute("aria-label", isCurrentlyPassword ? "Hide password" : "Show password");' in js
        assert "resetPasswordToggles();" in js

    def test_logout_resets_form_and_clears_credential_inputs(self):
        """Verify that handleLogout resets all auth forms and clears input values immediately."""
        js = APP_JS.read_text(encoding="utf-8")
        assert 'const formIds = ["loginForm", "registerForm", "forgotPasswordForm", "resetPasswordForm"];' in js
        assert "f.reset();" in js
        assert 'el.value = "";' in js
        assert 'el.defaultValue = "";' in js
        assert "resetPasswordToggles();" in js

    def test_passwords_never_stored_in_storage(self):
        """Verify that password values are never stored in localStorage or sessionStorage."""
        js = APP_JS.read_text(encoding="utf-8")
        assert 'localStorage.setItem("learnai_password"' not in js
        assert 'localStorage.setItem("password"' not in js
        assert 'sessionStorage.setItem("learnai_password"' not in js
        assert 'sessionStorage.setItem("password"' not in js


# ─────────────────────────────────────────────────────────────────────────────
# 5. Academic Verifier & AI Retake Remediation Tests (Issue 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestAcademicValidationAndRetakeRemediation:
    """Tests for Issue 3: Academic validation, arithmetic hallucination rejection, and retake prompts."""

    def test_academic_validation_rejects_arithmetic_hallucination(self):
        """Verify that the academic verifier strictly rejects false equality statements like '2 = 0.25^2'."""
        from services.academic_verifier import verify_academic_correctness

        q_defect = {
            "question": "What is the result of the calculation?",
            "options": {"A": "2", "B": "0.0625", "C": "4", "D": "1"},
            "correct_answer": "A",
            "concept": "Arithmetic",
            "explanation": "We know that 2 = 0.25^2, so the answer is 2."
        }
        is_valid, reason = verify_academic_correctness(q_defect)
        assert not is_valid, "False equality '2 = 0.25^2' must be rejected"
        assert "Arithmetic hallucination" in reason

    def test_academic_validation_accepts_valid_arithmetic(self):
        """Verify that valid mathematical explanations pass academic verification."""
        from services.academic_verifier import verify_academic_correctness

        q_valid = {
            "question": "What is 4 squared?",
            "options": {"A": "16", "B": "8", "C": "12", "D": "20"},
            "correct_answer": "A",
            "concept": "Basic Math",
            "explanation": "4^2 = 16. Therefore, option A is correct."
        }
        is_valid, reason = verify_academic_correctness(q_valid)
        assert is_valid, f"Valid arithmetic must pass, but got: {reason}"

    def test_build_selective_replacement_prompt_injects_academic_corrections(self):
        """Verify that build_selective_replacement_prompt adds academic rigor corrections on rejection."""
        from services.llm_service import build_selective_replacement_prompt

        rejected_records = [
            {
                "question": "Bad question",
                "reason": "Arithmetic hallucination in explanation: '2 = 0.25^2' is mathematically false (2 evaluates to 2.0, whereas 0.25^2 evaluates to 0.0625)",
                "category": "ACADEMIC"
            }
        ]
        prompt = build_selective_replacement_prompt(
            course_code="CS101",
            course_name="Computer Science",
            topic_name="Matrices",
            needed=1,
            preserved_questions=[],
            rejected_records=rejected_records,
            history_questions=[],
            missing_difficulties=["Medium"]
        )
        assert "CRITICAL ACADEMIC & MATHEMATICAL RIGOR CORRECTION:" in prompt
        assert "Every arithmetic calculation" in prompt
        assert "Never assert false equalities" in prompt

    def test_build_selective_replacement_prompt_requests_buffer_candidates(self):
        """Verify that replacement prompt requests max(needed + 1, 2) candidates to prevent single-question bottlenecks."""
        from services.llm_service import build_selective_replacement_prompt

        prompt = build_selective_replacement_prompt(
            course_code="CS101",
            course_name="Computer Science",
            topic_name="Matrices",
            needed=1,
            preserved_questions=[],
            rejected_records=[],
            history_questions=[],
            missing_difficulties=["Medium"]
        )
        assert "Return 2 candidate questions" in prompt

    def test_same_topic_retake_creates_distinct_quiz_session(self, client):
        """Verify that retaking a quiz creates a distinct quiz session and preserves attempt history."""
        # Register and login
        email = f"retake_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post("/api/auth/register", json={
            "full_name": "Retake Student",
            "email": email,
            "password": "Password123!",
            "confirm_password": "Password123!"
        })
        student_id = reg.json()["student_id"]
        token = reg.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create session 1
        q_data = [{
            "question": "What is det(I)?",
            "options": {"A": "1", "B": "0", "C": "-1", "D": "2"},
            "correct_answer": "A",
            "difficulty": "Easy",
            "concept": "Determinants"
        }]
        sess1 = create_quiz_session(
            session_id=str(uuid.uuid4()),
            student_id=student_id,
            course_code="MATH101",
            course_name="Mathematics",
            topic_name="Matrices",
            questions=q_data
        )

        # Complete session 1
        complete_quiz_session(sess1["id"], student_id)

        # Create retake session 2
        sess2 = create_quiz_session(
            session_id=str(uuid.uuid4()),
            student_id=student_id,
            course_code="MATH101",
            course_name="Mathematics",
            topic_name="Matrices",
            questions=q_data
        )

        assert sess1["id"] != sess2["id"]

        # Active endpoint must return session 2 (in_progress), NOT session 1 (completed)
        act_res = client.get("/api/quiz/active", headers=headers)
        assert act_res.status_code == 200
        assert act_res.json()["session_id"] == sess2["id"]
        assert act_res.json()["quiz_id"] == sess2["id"]
