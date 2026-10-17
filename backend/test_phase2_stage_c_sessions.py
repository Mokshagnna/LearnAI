import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_quiz_session,
    get_quiz_session,
    get_attempt_by_session_id,
    get_student_answers_for_session,
    get_topic_mastery,
    save_quiz_attempt,
    get_student_history,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)


SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i+1}: What is the role of backpropagation?",
        "options": {
            "A": "Forward activation",
            "B": "Compute gradients of loss function",
            "C": "Weight clipping",
            "D": "Data augmentation"
        },
        "correct_answer": "B",
        "difficulty": "Medium" if i % 2 == 0 else "Easy",
        "concept": "Backprop" if i % 2 == 0 else "Optimization",
        "explanation": "Backpropagation calculates loss gradients with respect to weights."
    }
    for i in range(10)
]


from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter

@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """
    Creates an isolated temporary SQLite database, initializes schema,
    resets rate limiters, and sets LEARNAI_DB_PATH so all application routes use the isolated database.
    """
    db_file = tmp_path / "stage_c_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()
    client = TestClient(app)
    return client, db_file


def mock_successful_quiz(course_code="22AM301", topic_name="McCulloch-Pitts Neuron"):
    return {
        "subject": "Deep Learning",
        "topic": topic_name,
        "course_code": course_code,
        "questions": SAMPLE_QUESTIONS,
        "recommended_difficulty": "mixed",
        "adaptive_applied": True,
        "adaptive_explanation": {"level": "mixed", "label": "Balanced"},
        "attempt_id": "mock_attempt_001",
        "attempt_number": 1,
        "novelty_repairs": 0,
        "provider_used": "groq",
        "is_fallback": False
    }


class TestStageCSessionPersistence:
    @patch("main.generate_quiz")
    def test_generated_quiz_persisted_in_database_before_return(self, mock_gen, isolated_db_client):
        """When /api/quiz/generate succeeds, session must be committed to database before returning."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_stage_c_01"
        })
        assert res.status_code == 200, res.text
        data = res.json()
        quiz_id = data["quiz_id"]
        assert quiz_id is not None
        assert len(data["questions"]) == 10
        # Ensure correct answers and explanations are stripped from client response
        for q in data["questions"]:
            assert "correct_answer" not in q
            assert "explanation" not in q

        # Verify session is persisted directly in SQLite database
        conn = get_connection(db_file)
        session_row = conn.execute("SELECT * FROM quiz_sessions WHERE id = ?", (quiz_id,)).fetchone()
        assert session_row is not None
        assert session_row["student_id"] == "stud_stage_c_01"
        assert session_row["course_code"] == "22AM301"
        assert session_row["status"] == "in_progress"
        assert session_row["expires_at"] is not None

        # Verify server question snapshot holds full trusted data
        snapshot = json.loads(session_row["questions_snapshot"])
        assert len(snapshot) == 10
        assert snapshot[0]["correct_answer"] == "B"
        assert "explanation" in snapshot[0]
        conn.close()

    @patch("main.generate_quiz")
    def test_session_retrieval_endpoint(self, mock_gen, isolated_db_client):
        """GET /api/quiz/session/{id} returns sanitized question data for active session."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_get_sess"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # Valid retrieval by owner
        sess_res = client.get(f"/api/quiz/session/{quiz_id}?student_id=stud_get_sess")
        assert sess_res.status_code == 200
        sess_data = sess_res.json()
        assert sess_data["session_id"] == quiz_id
        assert sess_data["status"] == "in_progress"
        assert len(sess_data["questions"]) == 10
        for q in sess_data["questions"]:
            assert "correct_answer" not in q
            assert "explanation" not in q

    @patch("main.generate_quiz")
    def test_session_survives_application_process_restart(self, mock_gen, isolated_db_client):
        """A session created in one process can be retrieved and submitted in a newly initialized client."""
        client_initial, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        # Step 1: Generate quiz in initial client
        gen_res = client_initial.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_restart_01"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # Step 2: Simulate process restart by creating a brand new TestClient instance against the same database
        new_client = TestClient(app)

        # Step 3: Retrieve session through new client
        get_res = new_client.get(f"/api/quiz/session/{quiz_id}?student_id=stud_restart_01")
        assert get_res.status_code == 200
        assert get_res.json()["session_id"] == quiz_id

        # Step 4: Submit answers through new client — must grade successfully from database
        answers = ["B"] * 10  # All B (10/10 correct)
        with patch("main.generate_analysis") as mock_analysis:
            mock_analysis.return_value = {"summary": "Great work!", "revision": "None", "recommendation": "Advance"}
            sub_res = new_client.post("/api/quiz/submit", json={
                "quiz_id": quiz_id,
                "answers": answers,
                "student_id": "stud_restart_01"
            })
            assert sub_res.status_code == 200
            res_data = sub_res.json()
            assert res_data["score"] == 10
            assert res_data["total"] == 10
            assert res_data["percentage"] == 100.0

    @patch("main.generate_quiz")
    def test_session_ownership_enforcement(self, mock_gen, isolated_db_client):
        """A student cannot retrieve or submit another student's quiz session."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_legit_owner"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # Unauthorized student attempts to access session
        get_res = client.get(f"/api/quiz/session/{quiz_id}?student_id=stud_attacker")
        assert get_res.status_code == 403
        assert "Access denied" in get_res.json()["detail"]

        # Unauthorized student attempts to submit session
        sub_res = client.post("/api/quiz/submit", json={
            "quiz_id": quiz_id,
            "answers": ["B"] * 10,
            "student_id": "stud_attacker"
        })
        assert sub_res.status_code == 403
        assert "Access denied" in sub_res.json()["detail"]

    def test_missing_session_returns_404(self, isolated_db_client):
        """Querying or submitting a nonexistent session returns HTTP 404."""
        client, _ = isolated_db_client
        get_res = client.get("/api/quiz/session/nonexistent-uuid-1234")
        assert get_res.status_code == 404
        assert "Quiz session not found" in get_res.json()["detail"]

        sub_res = client.post("/api/quiz/submit", json={
            "quiz_id": "nonexistent-uuid-1234",
            "answers": ["A"] * 10,
            "student_id": "stud_any"
        })
        assert sub_res.status_code == 404
        assert "Quiz not found or expired" in sub_res.json()["detail"]

    def test_expired_session_rejected_on_retrieval_and_submission(self, isolated_db_client):
        """Sessions exceeding expiration duration cannot be retrieved or submitted."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        get_or_create_student("stud_exp", conn=conn)

        # Create session expired 1 hour ago
        create_quiz_session(
            session_id="sess_expired_test",
            student_id="stud_exp",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="McCulloch-Pitts Neuron",
            questions=SAMPLE_QUESTIONS,
            duration_seconds=-3600,
            conn=conn
        )
        conn.close()

        # GET must return 400
        get_res = client.get("/api/quiz/session/sess_expired_test?student_id=stud_exp")
        assert get_res.status_code == 400
        assert "expired" in get_res.json()["detail"].lower()

        # POST submit must return 400
        sub_res = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_expired_test",
            "answers": ["B"] * 10,
            "student_id": "stud_exp"
        })
        assert sub_res.status_code == 400
        assert "expired" in sub_res.json()["detail"].lower()


class TestStageCSubmissionAndGrading:
    @patch("main.generate_quiz")
    def test_answers_graded_exclusively_from_server_snapshot(self, mock_gen, isolated_db_client):
        """Grading uses server-side correct_answers; client cannot forge scores."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_grade_test"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # Send 7 correct answers (B) and 3 incorrect answers (A)
        answers = ["B", "B", "B", "B", "B", "B", "B", "A", "A", "A"]

        # Attempt to tamper by injecting client-side score/mastery fields
        sub_payload = {
            "quiz_id": quiz_id,
            "answers": answers,
            "student_id": "stud_grade_test",
            "score": 10,          # Forged client field
            "percentage": 100.0,  # Forged client field
            "is_correct": True    # Forged client field
        }

        with patch("main.generate_analysis") as mock_analysis:
            mock_analysis.return_value = {"summary": "Analysis OK", "revision": "Rev", "recommendation": "Rec"}
            sub_res = client.post("/api/quiz/submit", json=sub_payload)
            assert sub_res.status_code == 200
            res = sub_res.json()

            # Server-evaluated score must be strictly 7, ignoring forged fields
            assert res["score"] == 7
            assert res["total"] == 10
            assert res["percentage"] == 70.0
            assert len(res["details"]) == 10

    @patch("main.generate_quiz")
    def test_malformed_answers_rejected(self, mock_gen, isolated_db_client):
        """Submitting answer count that differs from question count returns HTTP 400."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_malformed"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # 9 answers instead of 10
        sub_res = client.post("/api/quiz/submit", json={
            "quiz_id": quiz_id,
            "answers": ["A"] * 9,
            "student_id": "stud_malformed"
        })
        assert sub_res.status_code == 400
        assert "count" in sub_res.json()["detail"].lower()

    @patch("main.generate_quiz")
    def test_submission_persists_attempt_and_granular_answers(self, mock_gen, isolated_db_client):
        """Successful submission persists quiz_attempt, student_answers, and completes session."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_persist_check"
        })
        quiz_id = gen_res.json()["quiz_id"]

        with patch("main.generate_analysis") as mock_analysis:
            mock_analysis.return_value = {"summary": "Summary", "revision": "Rev", "recommendation": "Rec"}
            sub_res = client.post("/api/quiz/submit", json={
                "quiz_id": quiz_id,
                "answers": ["B"] * 10,
                "student_id": "stud_persist_check"
            })
            assert sub_res.status_code == 200

        # Direct DB checks
        conn = get_connection(db_file)
        # 1. Session is marked completed with submitted_at timestamp
        session = conn.execute("SELECT * FROM quiz_sessions WHERE id = ?", (quiz_id,)).fetchone()
        assert session["status"] == "completed"
        assert session["submitted_at"] is not None

        # 2. Attempt record created with session_id link
        attempt = conn.execute("SELECT * FROM quiz_attempts WHERE session_id = ?", (quiz_id,)).fetchone()
        assert attempt is not None
        assert attempt["score"] == 10
        assert attempt["student_id"] == "stud_persist_check"

        # 3. Exactly 10 student_answers rows created
        answers_rows = conn.execute("SELECT * FROM student_answers WHERE session_id = ?", (quiz_id,)).fetchall()
        assert len(answers_rows) == 10
        assert all(r["is_correct"] == 1 for r in answers_rows)

        # 4. Topic mastery created
        mastery = get_topic_mastery("stud_persist_check", "22AM301", "McCulloch-Pitts Neuron", conn=conn)
        assert mastery is not None
        assert mastery["total_questions"] == 10
        assert mastery["total_correct"] == 10
        assert mastery["current_level"] == ADAPTIVE_LEVEL_ADVANCED
        conn.close()


class TestStageCIdempotencyAndTransactions:
    @patch("main.generate_quiz")
    def test_idempotent_duplicate_submission_does_not_double_count(self, mock_gen, isolated_db_client):
        """Submitting an already-completed session returns existing result and does not increment attempts or mastery."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_idemp_check"
        })
        quiz_id = gen_res.json()["quiz_id"]

        with patch("main.generate_analysis") as mock_analysis:
            mock_analysis.return_value = {"summary": "Analysis", "revision": "Rev", "recommendation": "Rec"}
            # First submission
            sub1 = client.post("/api/quiz/submit", json={
                "quiz_id": quiz_id,
                "answers": ["B"] * 10,
                "student_id": "stud_idemp_check"
            })
            assert sub1.status_code == 200
            data1 = sub1.json()

            # Second (duplicate) submission for same session
            sub2 = client.post("/api/quiz/submit", json={
                "quiz_id": quiz_id,
                "answers": ["B"] * 10,
                "student_id": "stud_idemp_check"
            })
            assert sub2.status_code == 200
            data2 = sub2.json()

            # Result scores must match
            assert data1["score"] == data2["score"]
            assert data2.get("is_duplicate_submission") is True

        # Database verification: attempt and answers counts must NOT be duplicated
        conn = get_connection(db_file)
        attempt_count = conn.execute("SELECT count(*) FROM quiz_attempts WHERE session_id = ?", (quiz_id,)).fetchone()[0]
        assert attempt_count == 1, f"Expected exactly 1 attempt record, got {attempt_count}"

        answers_count = conn.execute("SELECT count(*) FROM student_answers WHERE session_id = ?", (quiz_id,)).fetchone()[0]
        assert answers_count == 10, f"Expected exactly 10 answers records, got {answers_count}"

        # Topic mastery must show only 1 attempt and 10 questions, NOT 2 attempts or 20 questions
        mastery = get_topic_mastery("stud_idemp_check", "22AM301", "McCulloch-Pitts Neuron", conn=conn)
        assert mastery["attempt_count"] == 1
        assert mastery["total_questions"] == 10
        assert mastery["total_correct"] == 10
        conn.close()

    @patch("main.generate_quiz")
    def test_simulated_database_failure_rolls_back_submission(self, mock_gen, isolated_db_client):
        """If a database error occurs during submission write, session remains in_progress and 0 partial records exist."""
        client, db_file = isolated_db_client
        mock_gen.return_value = mock_successful_quiz()

        gen_res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "student_id": "stud_rollback_check"
        })
        quiz_id = gen_res.json()["quiz_id"]

        # Simulate a database failure during save_student_answers inside transaction
        with patch("services.database.save_student_answers", side_effect=sqlite3.OperationalError("Disk simulated failure")):
            sub_res = client.post("/api/quiz/submit", json={
                "quiz_id": quiz_id,
                "answers": ["B"] * 10,
                "student_id": "stud_rollback_check"
            })
            assert sub_res.status_code == 500

        # Verify rollback: session must remain 'in_progress', no attempt or student answers saved
        conn = get_connection(db_file)
        sess = conn.execute("SELECT status FROM quiz_sessions WHERE id = ?", (quiz_id,)).fetchone()
        assert sess["status"] == "in_progress", "Session must remain in_progress after rollback"

        attempts = conn.execute("SELECT count(*) FROM quiz_attempts WHERE session_id = ?", (quiz_id,)).fetchone()[0]
        assert attempts == 0, "No quiz_attempt row should exist after rollback"

        answers = conn.execute("SELECT count(*) FROM student_answers WHERE session_id = ?", (quiz_id,)).fetchone()[0]
        assert answers == 0, "No student_answers row should exist after rollback"

        mastery = get_topic_mastery("stud_rollback_check", "22AM301", "McCulloch-Pitts Neuron", conn=conn)
        assert mastery is None, "Topic mastery must not be created on failed submission"
        conn.close()


class TestStageCHistoricalCompatibility:
    def test_historical_attempts_without_session_id_remain_accessible(self, isolated_db_client):
        """Historical quiz attempts with session_id=NULL remain readable via existing endpoints and methods."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        get_or_create_student("stud_hist_compat", conn=conn)

        # Insert historical attempt without session_id (legacy Phase 1 attempt)
        save_quiz_attempt(
            student_id="stud_hist_compat",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="McCulloch-Pitts Neuron",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Good",
            difficulty_accuracy={"Medium": {"correct": 4, "total": 5, "accuracy": 80.0}},
            concept_accuracy={"Backprop": {"correct": 4, "total": 5, "accuracy": 80.0}},
            weak_concepts=[],
            strong_concepts=["Backprop"],
            session_id=None,  # Legacy attempt without session ID
            conn=conn
        )
        conn.close()

        # Query history via API endpoint
        hist_res = client.get("/api/student/stud_hist_compat/history")
        assert hist_res.status_code == 200
        data = hist_res.json()
        assert len(data["attempts"]) == 1
        att = data["attempts"][0]
        assert att["score"] == 8
        assert att["session_id"] is None
        assert att["percentage"] == 80.0
