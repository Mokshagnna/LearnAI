import pytest
import sqlite3
import time
import json
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import main
from main import app
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_or_update_study_material,
    get_student_study_materials,
    get_study_material_detail,
    archive_study_material,
    create_quiz_session,
    submit_quiz_atomic,
    save_quiz_attempt,
    get_student_dashboard_data
)
from services.auth_utils import (
    sign_student_identity,
    auth_header_for
)
from services.evaluator import evaluate_answers

SAMPLE_QUESTIONS = [
    {
        "id": i,
        "question": f"Question {i} about Backpropagation?",
        "options": {"A": "Option 1", "B": "Option 2", "C": "Option 3", "D": "Option 4"},
        "correct_answer": "A",
        "explanation": f"Explanation {i}",
        "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
        "concept": "Gradient Descent" if i < 5 else "Chain Rule"
    }
    for i in range(10)
]

@pytest.fixture
def materials_client(tmp_path, monkeypatch):
    """Fixture providing an isolated SQLite database and fresh TestClient."""
    from services.rate_limiter import reset_all_rate_limiters
    reset_all_rate_limiters()
    db_file = tmp_path / "learnai_materials_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    try:
        yield client, db_file
    finally:
        reset_all_rate_limiters()


# ── 1. Study Materials Persistence & Lifecycle ────────────────────────────────

class TestStudyMaterialsPersistence:

    def test_list_materials_requires_auth(self, materials_client):
        """GET /api/study-materials without token returns 401."""
        client, _ = materials_client
        res = client.get("/api/study-materials")
        assert res.status_code == 401

    def test_list_materials_empty_for_new_student(self, materials_client):
        """GET /api/study-materials for fresh student returns empty list."""
        client, _ = materials_client
        student_id = "stud_fresh_01"
        headers = auth_header_for(student_id)
        res = client.get("/api/study-materials", headers=headers)
        assert res.status_code == 200
        assert res.json() == {"materials": []}

    def test_create_and_list_multiple_materials(self, materials_client):
        """Student can have multiple persistent study materials."""
        client, db_file = materials_client
        student_id = "stud_multi_01"
        headers = auth_header_for(student_id)

        # 1. Material A
        topics_a = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"},
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "CNNs", "topic_id": "dl_2"}
        ]
        mat_a = create_or_update_study_material(
            student_id=student_id,
            filename="DL_Lecture_01.pptx",
            file_type="PPTX",
            file_size=120000,
            extracted_text="Deep Learning Lecture 1 notes...",
            matched_topics=topics_a
        )

        # 2. Material B
        topics_b = [
            {"course_code": "22CS104", "course_name": "Python Programming", "topic_name": "Lists and Dictionaries", "topic_id": "py_1"}
        ]
        mat_b = create_or_update_study_material(
            student_id=student_id,
            filename="Python_CheatSheet.pdf",
            file_type="PDF",
            file_size=45000,
            extracted_text="Python language basics notes...",
            matched_topics=topics_b
        )

        res = client.get("/api/study-materials", headers=headers)
        assert res.status_code == 200
        materials = res.json()["materials"]
        assert len(materials) == 2
        filenames = [m["filename"] for m in materials]
        assert "DL_Lecture_01.pptx" in filenames
        assert "Python_CheatSheet.pdf" in filenames

    def test_idempotent_upload_deduplication(self, materials_client):
        """Uploading identical content hash updates existing material instead of duplicating."""
        client, _ = materials_client
        student_id = "stud_dedup_01"
        headers = auth_header_for(student_id)

        topics = [{"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}]
        res1 = create_or_update_study_material(
            student_id=student_id,
            filename="Notes.txt",
            file_type="TXT",
            file_size=1000,
            extracted_text="Exact same text content",
            matched_topics=topics
        )
        res2 = create_or_update_study_material(
            student_id=student_id,
            filename="Notes.txt",
            file_type="TXT",
            file_size=1000,
            extracted_text="Exact same text content",
            matched_topics=topics
        )
        assert res1["id"] == res2["id"]

        res = client.get("/api/study-materials", headers=headers)
        assert len(res.json()["materials"]) == 1


# ── 2. Topic Retrieval, Status, and Quiz Progress ────────────────────────────

class TestStudyMaterialTopicProgress:

    def test_topic_initial_status_not_attempted(self, materials_client):
        """New topics have status 'not_attempted' with attempt_count 0."""
        client, _ = materials_client
        student_id = "stud_topic_prog_01"
        headers = auth_header_for(student_id)

        topics = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}
        ]
        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Lecture.pptx",
            file_type="PPTX",
            file_size=50000,
            extracted_text="Backpropagation notes",
            matched_topics=topics
        )

        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["filename"] == "Lecture.pptx"
        assert len(data["topics"]) == 1
        top = data["topics"][0]
        assert top["status"] == "not_attempted"
        assert top["status_label"] == "Not Attempted"
        assert top["attempt_count"] == 0
        assert top["latest_score"] is None

    def test_topic_status_attempted_after_quiz(self, materials_client):
        """After taking a quiz with score < 80%, topic status transitions to 'attempted'."""
        client, db_file = materials_client
        student_id = "stud_topic_prog_02"
        headers = auth_header_for(student_id)

        topics = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}
        ]
        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Lecture.pptx",
            file_type="PPTX",
            file_size=50000,
            extracted_text="Backpropagation notes",
            matched_topics=topics
        )

        # Simulate completed quiz attempt (4/10 = 40%)
        conn = get_connection(db_file)
        create_quiz_session("sess_prog_1", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        answers = ["A" if i < 4 else "B" for i in range(10)]
        submit_quiz_atomic("sess_prog_1", student_id, answers, evaluator_fn=evaluate_answers, conn=conn)
        conn.close()

        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        top = data["topics"][0]
        assert top["status"] == "attempted"
        assert top["status_label"] == "Attempted"
        assert top["attempt_count"] == 1
        assert top["latest_score"] == 40.0

    def test_topic_status_mastered_when_rule_satisfied(self, materials_client):
        """When score >= 80%, topic status transitions to 'mastered'."""
        client, db_file = materials_client
        student_id = "stud_topic_prog_03"
        headers = auth_header_for(student_id)

        topics = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}
        ]
        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Lecture.pptx",
            file_type="PPTX",
            file_size=50000,
            extracted_text="Backpropagation notes",
            matched_topics=topics
        )

        # Simulate 9/10 = 90% score
        conn = get_connection(db_file)
        create_quiz_session("sess_prog_mastered", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        answers = ["A" if i < 9 else "B" for i in range(10)]
        submit_quiz_atomic("sess_prog_mastered", student_id, answers, evaluator_fn=evaluate_answers, conn=conn)
        conn.close()

        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        top = data["topics"][0]
        assert top["status"] == "mastered"
        assert top["status_label"] == "Mastered"
        assert top["attempt_count"] == 1
        assert top["latest_score"] == 90.0

        # Overall material summary
        assert data["mastered_topics"] == 1
        assert data["mastery_percentage"] == 100.0

    def test_topic_status_attempted_not_mastered_when_level_advanced_but_score_below_80(self, materials_client):
        """Scoring 60% promotes difficulty to advanced, but MUST NOT falsely mark topic as mastered."""
        client, db_file = materials_client
        student_id = "stud_topic_prog_adv_60"
        headers = auth_header_for(student_id)

        topics = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}
        ]
        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Lecture.pptx",
            file_type="PPTX",
            file_size=50000,
            extracted_text="Backpropagation notes",
            matched_topics=topics
        )

        # 6/10 = 60%: promotes adaptive engine to 'advanced', but is below 80% mastery threshold
        conn = get_connection(db_file)
        create_quiz_session("sess_prog_60", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        answers = ["A" if i < 6 else "B" for i in range(10)]
        submit_quiz_atomic("sess_prog_60", student_id, answers, evaluator_fn=evaluate_answers, conn=conn)
        conn.close()

        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        top = data["topics"][0]
        # Current level is 'advanced', but status MUST be 'attempted', NOT 'mastered'
        assert top["current_level"] == "advanced"
        assert top["status"] == "attempted"
        assert top["status_label"] == "Attempted"
        assert top["attempt_count"] == 1
        assert top["latest_score"] == 60.0

        # Overall summary should show 0 mastered topics
        assert data["mastered_topics"] == 0
        assert data["mastery_percentage"] == 0.0

        # Also verify summary card list API matches
        list_res = client.get("/api/study-materials", headers=headers)
        assert list_res.status_code == 200
        summary_mat = list_res.json()["materials"][0]
        assert summary_mat["mastered_topics"] == 0
        assert summary_mat["mastery_percentage"] == 0.0

    def test_multiple_quiz_attempts_preserve_history_and_increment_count(self, materials_client):
        """Retaking a quiz creates a new attempt, increments attempt_count, and updates latest_score."""
        client, db_file = materials_client
        student_id = "stud_topic_prog_04"
        headers = auth_header_for(student_id)

        topics = [
            {"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}
        ]
        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Lecture.pptx",
            file_type="PPTX",
            file_size=50000,
            extracted_text="Backpropagation notes",
            matched_topics=topics
        )

        conn = get_connection(db_file)
        # Attempt 1: 50%
        create_quiz_session("sess_retake_1", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        submit_quiz_atomic("sess_retake_1", student_id, ["A" if i < 5 else "B" for i in range(10)], evaluator_fn=evaluate_answers, conn=conn)

        time.sleep(0.02)  # Ensure monotonic timestamp progression across sessions

        # Attempt 2: 80%
        create_quiz_session("sess_retake_2", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        submit_quiz_atomic("sess_retake_2", student_id, ["A" if i < 8 else "B" for i in range(10)], evaluator_fn=evaluate_answers, conn=conn)
        conn.close()

        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        top = data["topics"][0]
        assert top["attempt_count"] == 2
        assert top["latest_score"] == 80.0
        assert top["status"] == "mastered"


# ── 3. Authorization & Ownership Enforcement ──────────────────────────────────

class TestStudyMaterialsAuthorization:

    def test_get_nonexistent_material_returns_404(self, materials_client):
        """GET /api/study-materials/{id} for unknown ID returns 404."""
        client, _ = materials_client
        headers = auth_header_for("stud_auth_01")
        res = client.get("/api/study-materials/fake-uuid-not-found", headers=headers)
        assert res.status_code == 404

    def test_cross_student_access_returns_403(self, materials_client):
        """Student B cannot retrieve Student A's study material (HTTP 403)."""
        client, _ = materials_client
        mat = create_or_update_study_material(
            student_id="student_A",
            filename="StudentA_Notes.pdf",
            file_type="PDF",
            file_size=5000,
            extracted_text="Private notes",
            matched_topics=[{"course_code": "22AM301", "course_name": "DL", "topic_name": "Backprop", "topic_id": "t1"}]
        )

        headers_b = auth_header_for("student_B")
        res = client.get(f"/api/study-materials/{mat['id']}", headers=headers_b)
        assert res.status_code == 403
        assert "Forbidden" in res.json()["detail"]

    def test_cross_student_removal_returns_403(self, materials_client):
        """Student B cannot delete Student A's study material (HTTP 403)."""
        client, _ = materials_client
        mat = create_or_update_study_material(
            student_id="student_A",
            filename="StudentA_Notes.pdf",
            file_type="PDF",
            file_size=5000,
            extracted_text="Private notes",
            matched_topics=[{"course_code": "22AM301", "course_name": "DL", "topic_name": "Backprop", "topic_id": "t1"}]
        )

        headers_b = auth_header_for("student_B")
        res = client.delete(f"/api/study-materials/{mat['id']}", headers=headers_b)
        assert res.status_code == 403


# ── 4. Safe Removal & Quiz History Preservation ───────────────────────────────

class TestSafeMaterialRemoval:

    def test_remove_material_preserves_quiz_attempts_and_dashboard(self, materials_client):
        """
        Removing study material deletes/archives the material view but
        STRICTLY PRESERVES all historical quiz attempts, answers, and mastery.
        """
        client, db_file = materials_client
        student_id = "stud_safe_remove_01"
        headers = auth_header_for(student_id)

        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Important_Lecture.pptx",
            file_type="PPTX",
            file_size=80000,
            extracted_text="Lecture content",
            matched_topics=[{"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}]
        )

        # Complete a quiz attempt
        conn = get_connection(db_file)
        create_quiz_session("sess_safe_remove", student_id, "22AM301", "Deep Learning", "Backpropagation", SAMPLE_QUESTIONS, conn=conn)
        submit_quiz_atomic("sess_safe_remove", student_id, ["A" for _ in range(10)], evaluator_fn=evaluate_answers, conn=conn)
        conn.close()

        # Verify initial dashboard
        dash_res1 = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
        assert dash_res1.status_code == 200
        assert dash_res1.json()["overview"]["completed_quizzes"] == 1

        # Delete material
        del_res = client.delete(f"/api/study-materials/{mat['id']}", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True

        # Material is no longer listed or accessible
        mats_res = client.get("/api/study-materials", headers=headers)
        assert len(mats_res.json()["materials"]) == 0

        mat_detail = client.get(f"/api/study-materials/{mat['id']}", headers=headers)
        assert mat_detail.status_code == 404

        # CRUCIAL: Historical quiz attempt and dashboard stats remain 100% intact!
        dash_res2 = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
        assert dash_res2.status_code == 200
        assert dash_res2.json()["overview"]["completed_quizzes"] == 1
        assert len(dash_res2.json()["recent_trends"]) == 1
        assert dash_res2.json()["recent_trends"][0]["topic_name"] == "Backpropagation"


# ── 5. End-to-End Quiz Grounding with Persistent Study Material ───────────────

class TestStudyMaterialQuizGrounding:

    def test_generate_quiz_grounded_by_study_material_id(self, materials_client):
        """POST /api/quiz/generate with study_material_id retrieves material text for grounding."""
        client, _ = materials_client
        student_id = "stud_grounding_01"
        headers = auth_header_for(student_id)

        mat = create_or_update_study_material(
            student_id=student_id,
            filename="Grounding_Lecture.txt",
            file_type="TXT",
            file_size=2000,
            extracted_text="Deep Learning backpropagation algorithms with learning rate alpha and stochastic gradient descent.",
            matched_topics=[{"course_code": "22AM301", "course_name": "Deep Learning", "topic_name": "Backpropagation", "topic_id": "dl_1"}]
        )

        with patch("main.generate_quiz", return_value={"questions": SAMPLE_QUESTIONS, "provider_used": "mock"}) as mock_gen:
            req_body = {
                "course_code": "22AM301",
                "topic": "Backpropagation",
                "student_id": student_id,
                "study_material_id": mat["id"]
            }
            res = client.post("/api/quiz/generate", json=req_body, headers=headers)
            assert res.status_code == 200
            assert mock_gen.called

            # Check that syllabus_context was extracted and passed
            call_kwargs = mock_gen.call_args[1]
            syllabus_ctx = call_kwargs.get("syllabus_context") or ""
            assert "stochastic gradient descent" in syllabus_ctx
