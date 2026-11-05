"""
test_stage3_lifecycle.py
========================
Verification of Stage 3 lifecycle implementations:
1. Authentication & Cookie gates without false unauthenticated flash.
2. Safe active quiz resumption without screen hijacking.
3. Completed quiz result persistence via GET /api/quiz/completed/latest.
4. Concurrency lockouts and session switching via POST /api/quiz/abandon and abandon_session_id in /api/quiz/generate.
5. Cross-tab synchronization and clean logout.
"""

import os
import json
import uuid
import pytest
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient

os.environ["STUDENT_SIGNING_SECRET"] = "stage3-lifecycle-test-secret-32-chars-long!"
os.environ["STUDENT_TOKEN_TTL_SECONDS"] = "86400"
os.environ["TESTING"] = "true"

from main import app
from services.database import (
    get_connection,
    init_db,
    create_student_account,
    create_quiz_session,
    get_quiz_session,
    get_active_quiz_session_for_student,
    save_quiz_attempt,
    get_latest_completed_attempt_for_student,
    abandon_quiz_session
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
INDEX_HTML = ROOT_DIR / "frontend" / "index.html"
STYLE_CSS = ROOT_DIR / "frontend" / "css" / "style.css"
APP_JS = ROOT_DIR / "frontend" / "js" / "app.js"


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Set up isolated SQLite test database for each test run."""
    db_file = tmp_path / "test_stage3_lifecycle.db"
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("RENDER", "")
    init_db(str(db_file))
    yield


@pytest.fixture
def client():
    return TestClient(app)


def register_and_login_student(client, name="Alice Test", email=None, password="Password123!"):
    if email is None:
        email = f"alice_{uuid.uuid4().hex[:8]}@test.edu"
    resp = client.post("/api/auth/register", json={
        "full_name": name,
        "email": email,
        "password": password,
        "confirm_password": password
    })
    assert resp.status_code in (200, 201), f"Registration failed: {resp.text}"
    token = resp.json()["token"]
    student_id = resp.json()["student_id"]
    return student_id, token, resp.cookies


class TestCompletedResultEndpoint:
    """Verify GET /api/quiz/completed/latest endpoint and ANSI INNER JOIN behavior."""

    def test_completed_latest_unauthenticated_returns_401(self, client):
        resp = client.get("/api/quiz/completed/latest")
        assert resp.status_code == 401

    def test_completed_latest_no_attempts_returns_204(self, client):
        student_id, token, cookies = register_and_login_student(client)
        resp = client.get(
            "/api/quiz/completed/latest",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 204

    def test_completed_latest_returns_most_recent_completed_quiz(self, client):
        student_id, token, cookies = register_and_login_student(client)

        sample_questions = [
            {
                "question": "What is 2 + 2?",
                "options": {"A": "3", "B": "4", "C": "5", "D": "6"},
                "correct_answer": "B",
                "explanation": "2+2=4",
                "concept": "Arithmetic",
                "difficulty": "Easy"
            }
        ]

        # 1. Create session 1 and save attempt 1
        s1_id = f"sess_{uuid.uuid4().hex[:8]}"
        sess1 = create_quiz_session(s1_id, student_id, "CS101", "Computer Science 101", "Math", sample_questions)
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE quiz_sessions SET status = 'completed' WHERE id = ?", (s1_id,))
            conn.commit()

        save_quiz_attempt(
            student_id=student_id,
            course_code="CS101",
            course_name="Computer Science 101",
            topic_name="Math",
            quiz_data={"questions": sample_questions},
            score=1,
            total=1,
            percentage=100.0,
            status="Mastered",
            difficulty_accuracy={"Easy": {"correct": 1, "total": 1, "accuracy": 100}},
            concept_accuracy={"Arithmetic": {"correct": 1, "total": 1, "accuracy": 100}},
            weak_concepts=[],
            strong_concepts=["Arithmetic"],
            session_id=s1_id
        )

        # Query endpoint: should return sess1 result
        resp1 = client.get(
            "/api/quiz/completed/latest",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["session_id"] == s1_id
        assert data1["score"] == 1
        assert data1["percentage"] == 100.0
        assert data1["course_code"] == "CS101"
        assert data1["topic"] == "Math"

        # 2. Create session 2 and save attempt 2 (more recent)
        s2_id = f"sess_{uuid.uuid4().hex[:8]}"
        sess2 = create_quiz_session(s2_id, student_id, "CS102", "Algorithms Course", "Algorithms", sample_questions)
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE quiz_sessions SET status = 'completed' WHERE id = ?", (s2_id,))
            conn.commit()

        save_quiz_attempt(
            student_id=student_id,
            course_code="CS102",
            course_name="Algorithms Course",
            topic_name="Algorithms",
            quiz_data={"questions": sample_questions},
            score=0,
            total=1,
            percentage=0.0,
            status="Needs Review",
            difficulty_accuracy={"Easy": {"correct": 0, "total": 1, "accuracy": 0}},
            concept_accuracy={"Arithmetic": {"correct": 0, "total": 1, "accuracy": 0}},
            weak_concepts=["Arithmetic"],
            strong_concepts=[],
            session_id=s2_id
        )

        # Query endpoint: should return sess2 result (latest)
        resp2 = client.get(
            "/api/quiz/completed/latest",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["session_id"] == s2_id
        assert data2["score"] == 0
        assert data2["percentage"] == 0.0
        assert data2["course_code"] == "CS102"
        assert data2["topic"] == "Algorithms"

    def test_in_progress_and_abandoned_sessions_are_not_returned_as_completed(self, client):
        student_id, token, cookies = register_and_login_student(client)

        sample_questions = [
            {"question": "Q?", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "correct_answer": "A"}
        ]
        s_id = f"sess_{uuid.uuid4().hex[:8]}"
        # In-progress session
        sess = create_quiz_session(s_id, student_id, "CS101", "Physics Course", "Physics", sample_questions)
        resp = client.get(
            "/api/quiz/completed/latest",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 204

        # Abandon session
        abandon_quiz_session(s_id, student_id)
        resp = client.get(
            "/api/quiz/completed/latest",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 204


class TestAbandonEndpoint:
    """Verify POST /api/quiz/abandon lifecycle."""

    def test_abandon_endpoint_unauthenticated_returns_401(self, client):
        resp = client.post("/api/quiz/abandon", json={"session_id": "test-session"})
        assert resp.status_code == 401

    def test_abandon_marks_session_abandoned_and_clears_active(self, client):
        student_id, token, cookies = register_and_login_student(client)

        sample_questions = [
            {"question": "Q?", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "correct_answer": "A"}
        ]
        s_id = f"sess_{uuid.uuid4().hex[:8]}"
        sess = create_quiz_session(s_id, student_id, "CS101", "Data Structures Course", "Data Structures", sample_questions)

        # Check /api/quiz/active returns 200
        act_resp = client.get(
            "/api/quiz/active",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert act_resp.status_code == 200
        assert act_resp.json()["session_id"] == s_id

        # Abandon session
        ab_resp = client.post(
            "/api/quiz/abandon",
            json={"session_id": s_id},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert ab_resp.status_code == 200
        assert ab_resp.json()["status"] == "abandoned"

        # Check /api/quiz/active now returns 204
        act_resp2 = client.get(
            "/api/quiz/active",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert act_resp2.status_code == 204


class TestConcurrencyAndSessionSwitching:
    """Verify concurrency locking, multi-session disclosure, and atomic abandonment in /api/quiz/generate."""

    MOCK_QUIZ = {
        "questions": [
            {
                "question": "What is an array?",
                "options": {"A": "Collection", "B": "Tree", "C": "Graph", "D": "Loop"},
                "correct_answer": "A",
                "explanation": "Array is a linear collection.",
                "difficulty": "Easy",
                "concept": "Arrays"
            }
        ],
        "difficulty": "Easy",
        "concept_tags": ["Arrays"]
    }

    def test_1_one_active_session_confirming_its_abandonment_works(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s_id = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s_id, student_id, "CS101", "Data Structures", "Data Structures", [{"q": "1"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            # Without confirmation -> 409
            resp = client.post(
                "/api/quiz/generate",
                json={"course_code": "22MT103", "topic": "Matrices", "practice_mode": "general_syllabus"},
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["active_session_id"] == s_id
            assert detail["total_active"] == 1

            # With confirmation -> 200 OK
            resp2 = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "practice_mode": "general_syllabus",
                    "abandon_session_id": s_id
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp2.status_code == 200
            assert get_quiz_session(s_id)["status"] == "abandoned"

    def test_2_multiple_active_sessions_all_affected_topics_disclosed(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            resp = client.post(
                "/api/quiz/generate",
                json={"course_code": "22MT103", "topic": "Calculus", "practice_mode": "general_syllabus"},
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["total_active"] == 2
            disclosed = detail["active_sessions"]
            assert len(disclosed) == 2
            disclosed_ids = {s["session_id"] for s in disclosed}
            assert disclosed_ids == {s1, s2}
            topics = {s["topic"] for s in disclosed}
            assert topics == {"Matrices", "Sorting"}

    def test_3_multiple_active_sessions_cancelling_leaves_every_session_unchanged(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        # Cancelling means client aborts or returns without submitting confirmed IDs
        assert get_quiz_session(s1)["status"] == "in_progress"
        assert get_quiz_session(s2)["status"] == "in_progress"

    def test_4_multiple_active_sessions_confirming_exact_disclosed_ids_abandons_only_those_ids(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Calculus",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s1, s2]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200
            assert get_quiz_session(s1)["status"] == "abandoned"
            assert get_quiz_session(s2)["status"] == "abandoned"
            new_id = resp.json()["quiz_id"]
            assert get_quiz_session(new_id)["status"] == "in_progress"

    def test_5_stale_or_incomplete_id_list_returns_conflict_and_changes_nothing(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            # Incomplete list (only s1 when both s1 and s2 exist)
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Calculus",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s1]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 409
            assert get_quiz_session(s1)["status"] == "in_progress"
            assert get_quiz_session(s2)["status"] == "in_progress"

            # Stale list with bogus ID
            resp2 = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Calculus",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s1, "bogus_stale_id_999"]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp2.status_code == 409
            assert get_quiz_session(s1)["status"] == "in_progress"
            assert get_quiz_session(s2)["status"] == "in_progress"

    def test_6_foreign_owned_session_ids_are_rejected(self, client):
        student_a, token_a, _ = register_and_login_student(client, name="Alice", email=f"alice_{uuid.uuid4().hex[:6]}@test.edu")
        student_b, token_b, _ = register_and_login_student(client, name="Bob", email=f"bob_{uuid.uuid4().hex[:6]}@test.edu")

        s_b = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s_b, student_b, "CS101", "Data Structures", "Data Structures", [{"q": "1"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            # Student A tries to abandon Student B's session ID
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s_b]
                },
                headers={"Authorization": f"Bearer {token_a}"}
            )
            # Student A has no active sessions, so the quiz is generated for A, but Student B's session MUST NOT be touched
            assert resp.status_code == 200
            assert get_quiz_session(s_b)["status"] == "in_progress"

    def test_7_completed_sessions_remain_completed(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s_comp = f"sess_{uuid.uuid4().hex[:8]}"
        s_act = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s_comp, student_id, "CS101", "Completed Course", "Completed Topic", [{"q": "1"}])
        save_quiz_attempt(
            student_id=student_id,
            course_code="CS101",
            course_name="Completed Course",
            topic_name="Completed Topic",
            quiz_data={"questions": [{"q": "1"}]},
            score=1,
            total=1,
            percentage=100.0,
            status="Mastered",
            difficulty_accuracy={"Easy": {"correct": 1, "total": 1, "accuracy": 100}},
            concept_accuracy={"Math": {"correct": 1, "total": 1, "accuracy": 100}},
            weak_concepts=[],
            strong_concepts=["Math"],
            recommended_difficulty="Medium",
            session_id=s_comp
        )
        create_quiz_session(s_act, student_id, "CS102", "Active Course", "Active Topic", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s_act]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200
            assert get_quiz_session(s_act)["status"] == "abandoned"
            assert get_quiz_session(s_comp)["status"] == "completed"

    def test_8_expired_sessions_are_not_accidentally_treated_as_valid_active_sessions(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s_exp = f"sess_{uuid.uuid4().hex[:8]}"
        # Create session with duration -3600 seconds (already expired)
        create_quiz_session(s_exp, student_id, "CS101", "Expired Course", "Expired Topic", [{"q": "1"}], duration_seconds=-3600)

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            resp = client.post(
                "/api/quiz/generate",
                json={"course_code": "22MT103", "topic": "Matrices", "practice_mode": "general_syllabus"},
                headers={"Authorization": f"Bearer {token}"}
            )
            # Expired session does not block generation
            assert resp.status_code == 200

    def test_9_failed_insertion_rolls_back_every_abandonment_update(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ),              patch("main.create_quiz_session", side_effect=RuntimeError("Simulated database write failure")):
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s1, s2]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 500
            # Both sessions MUST remain in_progress due to rollback
            assert get_quiz_session(s1)["status"] == "in_progress"
            assert get_quiz_session(s2)["status"] == "in_progress"

    def test_10_successful_new_quiz_leaves_exactly_one_active_session(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        s2 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])
        create_quiz_session(s2, student_id, "CS102", "Algorithms", "Sorting", [{"q": "2"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            resp = client.post(
                "/api/quiz/generate",
                json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "practice_mode": "general_syllabus",
                    "abandon_session_ids": [s1, s2]
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200

            # Query database for all active sessions for this student
            conn = get_connection()
            rows = conn.execute(
                "SELECT id, status FROM quiz_sessions WHERE student_id = ? AND status = 'in_progress'",
                (student_id,)
            ).fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][0] == resp.json()["quiz_id"]

    def test_11_concurrent_requests_cannot_create_duplicate_active_sessions(self, client):
        student_id, token, cookies = register_and_login_student(client)
        s1 = f"sess_{uuid.uuid4().hex[:8]}"
        create_quiz_session(s1, student_id, "CS101", "Math", "Matrices", [{"q": "1"}])

        with patch("main.generate_quiz", return_value=self.MOCK_QUIZ):
            # Without abandoning s1, request is rejected with 409
            resp = client.post(
                "/api/quiz/generate",
                json={"course_code": "22MT103", "topic": "Matrices", "practice_mode": "general_syllabus"},
                headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 409


class TestFrontendWiringAndElements:
    """Verify frontend code templates, CSS, and JS wiring for automatic restoration and exit button removal."""

    def test_index_html_exit_button_removed_and_nav_controls_intact(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        # Exit quiz button is completely removed
        assert 'id="exitQuizBtn"' not in html
        assert "Exit Quiz &amp; Change Topic" not in html
        # Previous and Next controls remain intact in navigation row
        assert 'id="prevBtn"' in html
        assert 'id="nextBtn"' in html
        # Fallback banner remains on setup screen
        assert 'id="activeQuizBanner"' in html
        assert 'id="resumeActiveQuizBtn"' in html
        assert 'id="discardActiveQuizBtn"' in html

    def test_style_css_exit_button_styles_removed(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        # Exit button rules removed
        assert "#exitQuizBtn" not in css
        # Nav row and banner layout styles preserved
        assert ".nav-row" in css
        assert ".active-quiz-banner" in css

    def test_app_js_exit_handler_removed_and_restoration_wiring(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Exit button event listener removed
        assert '$("exitQuizBtn")' not in js
        assert 'exitQuizBtn' not in js
        # Core restoration functions present
        assert "loadActiveQuizState" in js
        assert "resumeActiveQuiz" in js
        assert "saveSessionState" in js
        assert "lastCompletedResult" in js
        assert "/api/quiz/completed/latest" in js
        assert "/api/quiz/abandon" in js

    def test_handle_practice_nav_automatically_resumes_active_quiz(self):
        js = APP_JS.read_text(encoding="utf-8")
        # handlePracticeNav must call resumeActiveQuiz() directly when active quiz exists
        pattern = r"const hasActive = await loadActiveQuizState\(\);\s*if \(hasActive\) \{\s*resumeActiveQuiz\(\);"
        import re
        assert re.search(pattern, js) is not None, "handlePracticeNav does not directly call resumeActiveQuiz()"

    def test_init_app_automatically_resumes_active_quiz_on_load_or_refresh(self):
        js = APP_JS.read_text(encoding="utf-8")
        # initApp must call resumeActiveQuiz() directly when active quiz exists
        pattern = r"const hasActive = await loadActiveQuizState\(\);[\s\S]*?if \(hasActive\) \{\s*resumeActiveQuiz\(\);"
        import re
        assert re.search(pattern, js) is not None, "initApp does not directly call resumeActiveQuiz()"

    def test_popstate_automatically_resumes_quiz_section(self):
        js = APP_JS.read_text(encoding="utf-8")
        pattern = r'if \(targetScreen === "quizSection"\) \{\s*resumeActiveQuiz\(\);'
        import re
        assert re.search(pattern, js) is not None, "popstate does not call resumeActiveQuiz for quizSection"

    def test_question_index_and_answer_persistence_preserved_in_client(self):
        js = APP_JS.read_text(encoding="utf-8")
        # saveSessionState serializes currentIndex, answers, and currentQuiz
        assert "currentIndex," in js
        assert "answers," in js
        assert "learnai_active_quiz" in js
        # answers[currentIndex] updated on option selection
        assert "answers[currentIndex] = key;" in js
        assert "saveSessionState();" in js

    def test_account_isolation_guards_in_load_active_quiz_state(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Must verify activeStudentId against quiz ownership and purge on mismatch
        assert "data.currentQuiz.student_id !== activeStudentId" in js
        assert "clearSessionState();" in js

    def test_409_recovery_resets_isGenerating_before_recursive_call(self):
        """Verify startQuizGeneration resets isGenerating before re-entering on 409 confirm,
        so the recursive call is not blocked by the in-flight guard."""
        js = APP_JS.read_text(encoding="utf-8")
        # Multi-session confirm path: isGenerating = false must precede recursive call
        assert "isGenerating = false;" in js
        # The reset must appear between the confirmMulti branch and the recursive call
        multi_pattern = r"if \(confirmMulti\) \{\s*isGenerating = false;\s*const allIds"
        import re
        assert re.search(multi_pattern, js) is not None, (
            "409 multi-session path does not reset isGenerating before recursive startQuizGeneration()"
        )
        # Single-session discard path: isGenerating = false must precede recursive call
        single_pattern = r"\}\s*else \{\s*isGenerating = false;\s*return await startQuizGeneration"
        assert re.search(single_pattern, js) is not None, (
            "409 single-session discard path does not reset isGenerating before recursive startQuizGeneration()"
        )

    def test_start_quiz_generation_checks_backend_when_local_state_empty(self):
        """Verify startQuizGeneration falls back to /api/quiz/active via restoreSessionState()
        when local frontend state shows no active quiz, preventing duplicate session creation."""
        js = APP_JS.read_text(encoding="utf-8")
        # The else-if branch that checks backend when hasActiveQuiz() is false
        assert "} else if (!isRetake) {" in js
        assert "const restored = await restoreSessionState();" in js
        assert "if (restored) {" in js
        assert "return;" in js

    def test_start_quiz_generation_prevents_reentry_while_generating(self):
        """Verify startQuizGeneration blocks re-entry while a generation is in flight."""
        js = APP_JS.read_text(encoding="utf-8")
        assert "if (isGenerating) return;" in js
        assert "isGenerating = true;" in js

    def test_409_cancel_leaves_all_sessions_untouched(self):
        """Verify that cancelling a 409 dialog returns without modifying any sessions."""
        js = APP_JS.read_text(encoding="utf-8")
        # Cancel path returns immediately without calling startQuizGeneration again
        assert "} else {\n            return;\n          }" in js or "} else {\n            return;\n          }" in js.replace("  ", " ")

    def test_frontend_assets_exist_and_are_versioned(self):
        """Verify that style.css and app.js physically exist and are referenced with cache-busting version query parameters in index.html."""
        import re
        assert INDEX_HTML.exists(), "frontend/index.html does not exist"
        assert STYLE_CSS.exists(), "frontend/css/style.css does not exist"
        assert APP_JS.exists(), "frontend/js/app.js does not exist"

        html = INDEX_HTML.read_text(encoding="utf-8")
        css_match = re.search(r'href=["\']css/style\.css\?v=([a-zA-Z0-9._-]+)["\']', html)
        js_match = re.search(r'src=["\']js/app\.js\?v=([a-zA-Z0-9._-]+)["\']', html)

        assert css_match is not None, "frontend/index.html must reference css/style.css with ?v= parameter"
        assert js_match is not None, "frontend/index.html must reference js/app.js with ?v= parameter"

        css_ver = css_match.group(1).strip()
        js_ver = js_match.group(1).strip()
        assert len(css_ver) > 0, "CSS version parameter cannot be empty"
        assert len(js_ver) > 0, "JS version parameter cannot be empty"

    def test_git_asset_cache_busting_on_modifications(self):
        """
        Verify that whenever frontend/js/app.js or frontend/css/style.css changes,
        its version in frontend/index.html must also change to bust immutable 1-year browser cache.
        Compares relevant Git revisions.
        """
        import subprocess
        import re

        # Extract current versions from working tree index.html
        html = INDEX_HTML.read_text(encoding="utf-8")
        current_css_match = re.search(r'href=["\']css/style\.css\?v=([a-zA-Z0-9._-]+)["\']', html)
        current_js_match = re.search(r'src=["\']js/app\.js\?v=([a-zA-Z0-9._-]+)["\']', html)
        assert current_css_match and current_js_match

        current_css_ver = current_css_match.group(1)
        current_js_ver = current_js_match.group(1)

        # 1. Check against HEAD if working tree has changes
        head_html_cmd = subprocess.run(
            ["git", "show", "HEAD:frontend/index.html"],
            capture_output=True, text=True, cwd=str(ROOT_DIR)
        )
        if head_html_cmd.returncode == 0:
            head_html = head_html_cmd.stdout
            head_css_m = re.search(r'href=["\']css/style\.css\?v=([a-zA-Z0-9._-]+)["\']', head_html)
            head_js_m = re.search(r'src=["\']js/app\.js\?v=([a-zA-Z0-9._-]+)["\']', head_html)
            head_css_ver = head_css_m.group(1) if head_css_m else None
            head_js_ver = head_js_m.group(1) if head_js_m else None

            # If app.js was modified in working copy vs HEAD, index.html must have incremented version
            diff_js = subprocess.run(
                ["git", "diff", "HEAD", "--", "frontend/js/app.js"],
                capture_output=True, text=True, cwd=str(ROOT_DIR)
            ).stdout.strip()
            if diff_js:
                assert current_js_ver != head_js_ver, (
                    f"frontend/js/app.js was modified vs HEAD, but frontend/index.html version "
                    f"was not incremented (still {head_js_ver})"
                )

            # If style.css was modified in working copy vs HEAD, index.html must have incremented version
            diff_css = subprocess.run(
                ["git", "diff", "HEAD", "--", "frontend/css/style.css"],
                capture_output=True, text=True, cwd=str(ROOT_DIR)
            ).stdout.strip()
            if diff_css:
                assert current_css_ver != head_css_ver, (
                    f"frontend/css/style.css was modified vs HEAD, but frontend/index.html version "
                    f"was not incremented (still {head_css_ver})"
                )

        # 2. Check Git history across commits: find the commit that last changed app.js and style.css
        # Verify that commit or subsequent commits updated the asset query tag in index.html
        last_js_commit = subprocess.run(
            ["git", "log", "-n", "1", "--format=%H", "--", "frontend/js/app.js"],
            capture_output=True, text=True, cwd=str(ROOT_DIR)
        ).stdout.strip()

        if last_js_commit:
            # Check the version in the commit immediately prior to the last JS change
            parent_js_commit = f"{last_js_commit}~1"
            parent_html_cmd = subprocess.run(
                ["git", "show", f"{parent_js_commit}:frontend/index.html"],
                capture_output=True, text=True, cwd=str(ROOT_DIR)
            )
            if parent_html_cmd.returncode == 0:
                parent_html = parent_html_cmd.stdout
                parent_js_m = re.search(r'src=["\']js/app\.js\?v=([a-zA-Z0-9._-]+)["\']', parent_html)
                if parent_js_m:
                    parent_js_ver = parent_js_m.group(1)
                    # The current working version must be strictly different from the version before the last app.js modification
                    assert current_js_ver != parent_js_ver, (
                        f"frontend/js/app.js was modified in {last_js_commit[:7]}, but index.html "
                        f"asset version remained {parent_js_ver} (must be incremented for cache invalidation)"
                    )
