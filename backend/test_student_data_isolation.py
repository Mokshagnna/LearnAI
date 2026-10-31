"""
test_student_data_isolation.py
==============================
Mandatory Security Test Suite: Multi-Tenant Student Data Isolation.

Enforces that Student A and Student B have 100% strict isolation:
Student A's private materials, extracted topics, quiz sessions, quiz attempts,
answers, scores, topic mastery, and dashboard statistics CANNOT be accessed,
modified, deleted, or inferred by Student B under any circumstances.

Test Flow:
1. Register Student A and Student B.
2. Authenticate both independently with distinct tokens.
3. As Student A:
   - Create study material and extracted topics.
   - Create active quiz session.
   - Complete quiz attempt, answers, scores, and topic mastery.
4. As Student B, attempt to:
   - List study materials (must NOT include Student A's material).
   - Retrieve Student A's material by ID (HTTP 403 Forbidden).
   - Delete / archive Student A's material (HTTP 403 Forbidden).
   - Generate quiz referencing Student A's study_material_id (HTTP 403 Forbidden).
   - Retrieve Student A's quiz session by ID (HTTP 403 Forbidden).
   - Submit answers against Student A's quiz session (HTTP 403 Forbidden).
   - Retrieve Student A's quiz history (HTTP 403 Forbidden).
   - Retrieve Student A's concept / difficulty performance (HTTP 403 Forbidden).
   - Retrieve Student A's dashboard analytics (HTTP 403 Forbidden).
   - Manipulate query parameters and payloads to access Student A's data.
5. Cross-Device Learning Continuity:
   - Student A logs out.
   - Student A logs back in from another simulated device.
   - Student A can still access 100% of their own saved materials, quiz history, and progress.
"""

import pytest
import sqlite3
import time
import os
import uuid
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from services.database import (
    init_db,
    get_connection,
    create_or_update_study_material,
    create_quiz_session,
    save_quiz_attempt,
    save_student_answers,
    upsert_topic_mastery,
)
from services.rate_limiter import reset_all_rate_limiters

COURSE_CODE = "22CS201"
COURSE_NAME = "Database Management Systems"
TOPIC_NAME = "Databases And Database Users & Database System Concepts And Architecture"


@pytest.fixture
def isolation_client(tmp_path, monkeypatch):
    """Fixture providing isolated database and clean rate limiters."""
    reset_all_rate_limiters()
    db_file = tmp_path / "learnai_data_isolation_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    yield client, db_file
    reset_all_rate_limiters()


class TestStudentDataIsolation:

    def _setup_students_and_data(self, client, db_file):
        """Helper to register Student A and B, and populate Student A's learning data."""
        # 1. Register Student A
        res_a = client.post("/api/auth/register", json={
            "full_name": "Student Alpha",
            "email": "student.alpha@gmail.com",
            "password": "PasswordAlpha123",
            "confirm_password": "PasswordAlpha123"
        })
        assert res_a.status_code == 201
        data_a = res_a.json()
        student_a_id = data_a["student_id"]
        token_a = data_a["token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. Register Student B
        res_b = client.post("/api/auth/register", json={
            "full_name": "Student Beta",
            "email": "student.beta@gmail.com",
            "password": "PasswordBeta456",
            "confirm_password": "PasswordBeta456"
        })
        assert res_b.status_code == 201
        data_b = res_b.json()
        student_b_id = data_b["student_id"]
        token_b = data_b["token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 3. Create Student A's Study Material
        conn = get_connection(db_file)
        mat_a = create_or_update_study_material(
            student_id=student_a_id,
            filename="DBMS_Unit_1_Notes.pdf",
            file_type="PDF",
            file_size=10240,
            extracted_text="Databases, Database Users, System Architecture, Schemas and Instances",
            matched_topics=[{
                "course_code": COURSE_CODE,
                "course_name": COURSE_NAME,
                "topic_id": "cs201_intro",
                "topic_name": TOPIC_NAME,
                "confidence": 0.95,
                "matched_concepts": ["Database System", "Schema"]
            }],
            conn=conn
        )
        material_a_id = mat_a["id"]

        # 4. Create Student A's Quiz Session
        session_a_id = f"sess_alpha_{uuid.uuid4().hex[:12]}"
        sample_questions = [
            {
                "id": i,
                "question": f"Question {i} on Database Systems?",
                "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
                "correct_answer": "A",
                "explanation": "DBMS manages database schemas.",
                "difficulty": "Easy" if i < 4 else "Medium",
                "concept": "Database System"
            }
            for i in range(10)
        ]
        create_quiz_session(
            session_id=session_a_id,
            student_id=student_a_id,
            course_code=COURSE_CODE,
            course_name=COURSE_NAME,
            topic_name=TOPIC_NAME,
            questions=sample_questions,
            conn=conn
        )

        # 5. Create Student A's Completed Quiz Attempt & Answers
        attempt_a_id = save_quiz_attempt(
            student_id=student_a_id,
            course_code=COURSE_CODE,
            course_name=COURSE_NAME,
            topic_name=TOPIC_NAME,
            quiz_data={"questions": sample_questions},
            score=9,
            total=10,
            percentage=90.0,
            status="completed",
            difficulty_accuracy={"Easy": {"correct": 4, "total": 4}, "Medium": {"correct": 5, "total": 6}},
            concept_accuracy={"Database System": {"correct": 9, "total": 10}},
            weak_concepts=[],
            strong_concepts=["Database System"],
            recommended_difficulty="Hard",
            session_id=session_a_id,
            conn=conn
        )
        save_student_answers(
            session_id=session_a_id,
            attempt_id=attempt_a_id,
            student_id=student_a_id,
            answers_details=[
                {
                    "question_index": i,
                    "question_text": f"Question {i} on Database Systems?",
                    "selected_answer": "A",
                    "correct_answer": "A",
                    "is_correct": 1,
                    "difficulty": "Easy",
                    "concept": "Database System",
                    "explanation": "Explanation here"
                }
                for i in range(10)
            ],
            conn=conn
        )

        # 6. Create Student A's Topic Mastery Record
        upsert_topic_mastery(
            student_id=student_a_id,
            course_code=COURSE_CODE,
            topic_name=TOPIC_NAME,
            score=9,
            total=10,
            weak_concepts=[],
            strong_concepts=["Database System"],
            current_level="Hard",
            conn=conn
        )
        conn.close()

        return {
            "student_a": {"id": student_a_id, "token": token_a, "headers": headers_a},
            "student_b": {"id": student_b_id, "token": token_b, "headers": headers_b},
            "material_a_id": material_a_id,
            "session_a_id": session_a_id,
            "attempt_a_id": attempt_a_id
        }

    def test_student_b_cannot_see_student_a_study_materials_in_list(self, isolation_client):
        """Student B listing materials sees only empty list, never Student A's files."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]

        res_b = client.get("/api/study-materials", headers=headers_b)
        assert res_b.status_code == 200
        materials_b = res_b.json().get("materials", [])
        assert len(materials_b) == 0

        # Contrast with Student A
        headers_a = data["student_a"]["headers"]
        res_a = client.get("/api/study-materials", headers=headers_a)
        assert res_a.status_code == 200
        materials_a = res_a.json().get("materials", [])
        assert len(materials_a) == 1
        assert materials_a[0]["id"] == data["material_a_id"]

    def test_student_b_cannot_get_student_a_material_detail_by_id(self, isolation_client):
        """Student B attempting direct ID access to Student A's material receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        mat_id = data["material_a_id"]

        res_b = client.get(f"/api/study-materials/{mat_id}", headers=headers_b)
        assert res_b.status_code == 403
        assert "access" in res_b.json()["detail"].lower()

    def test_student_b_cannot_delete_student_a_material(self, isolation_client):
        """Student B attempting to delete Student A's material receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        mat_id = data["material_a_id"]

        del_res = client.delete(f"/api/study-materials/{mat_id}", headers=headers_b)
        assert del_res.status_code == 403

        # Confirm Student A's material is STILL active and untouched
        headers_a = data["student_a"]["headers"]
        check_a = client.get(f"/api/study-materials/{mat_id}", headers=headers_a)
        assert check_a.status_code == 200

    def test_student_b_cannot_generate_quiz_from_student_a_material(self, isolation_client):
        """Student B passing Student A's study_material_id to quiz generation receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_b_id = data["student_b"]["id"]
        mat_id = data["material_a_id"]

        gen_payload = {
            "course_code": COURSE_CODE,
            "topic": TOPIC_NAME,
            "study_material_id": mat_id,
            "student_id": student_b_id
        }
        res = client.post("/api/quiz/generate", json=gen_payload, headers=headers_b)
        assert res.status_code == 403
        assert "belongs to another student" in res.json()["detail"].lower()

    def test_student_b_cannot_read_student_a_quiz_session(self, isolation_client):
        """Student B requesting Student A's active quiz session receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        session_a_id = data["session_a_id"]

        res = client.get(f"/api/quiz/session/{session_a_id}", headers=headers_b)
        assert res.status_code == 403
        assert "belongs to another student" in res.json()["detail"].lower()

    def test_student_b_cannot_submit_answers_to_student_a_session(self, isolation_client):
        """Student B cannot submit answers against Student A's quiz session."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_b_id = data["student_b"]["id"]
        session_a_id = data["session_a_id"]

        # Attempt with student_b_id in payload
        res1 = client.post("/api/quiz/submit", json={
            "quiz_id": session_a_id,
            "answers": ["A"] * 10,
            "student_id": student_b_id
        }, headers=headers_b)
        assert res1.status_code == 403

        # Attempt with student_a_id spoofed in payload
        res2 = client.post("/api/quiz/submit", json={
            "quiz_id": session_a_id,
            "answers": ["A"] * 10,
            "student_id": data["student_a"]["id"]
        }, headers=headers_b)
        assert res2.status_code == 403

    def test_student_b_cannot_access_student_a_history(self, isolation_client):
        """Student B requesting Student A's history endpoint receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_a_id = data["student_a"]["id"]

        res = client.get(f"/api/student/{student_a_id}/history", headers=headers_b)
        assert res.status_code == 403
        assert "cannot view another student" in res.json()["detail"].lower()

    def test_student_b_cannot_access_student_a_performance(self, isolation_client):
        """Student B requesting Student A's performance endpoint receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_a_id = data["student_a"]["id"]

        res = client.get(f"/api/student/{student_a_id}/performance", headers=headers_b)
        assert res.status_code == 403
        assert "cannot view another student" in res.json()["detail"].lower()

    def test_student_b_cannot_access_student_a_dashboard(self, isolation_client):
        """Student B requesting Student A's dashboard receives 403 Forbidden."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_a_id = data["student_a"]["id"]

        res = client.get(f"/api/student/{student_a_id}/dashboard", headers=headers_b)
        assert res.status_code == 403
        assert "cannot view another student" in res.json()["detail"].lower()

    def test_student_b_own_dashboard_is_clean_and_empty(self, isolation_client):
        """Student B's own dashboard contains 0 attempts and 0% accuracy, completely isolated from Student A."""
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        headers_b = data["student_b"]["headers"]
        student_b_id = data["student_b"]["id"]

        res = client.get(f"/api/student/{student_b_id}/dashboard", headers=headers_b)
        assert res.status_code == 200
        dash_b = res.json()
        assert dash_b["student_id"] == student_b_id
        assert dash_b["overview"]["completed_quizzes"] == 0
        assert len(dash_b["recent_trends"]) == 0

        # Contrast with Student A's dashboard
        headers_a = data["student_a"]["headers"]
        student_a_id = data["student_a"]["id"]
        res_a = client.get(f"/api/student/{student_a_id}/dashboard", headers=headers_a)
        assert res_a.status_code == 200
        dash_a = res_a.json()
        assert dash_a["overview"]["completed_quizzes"] == 1
        assert len(dash_a["recent_trends"]) == 1

    def test_cross_device_learning_continuity_for_student_a(self, isolation_client):
        """
        Simulates Student A closing laptop (logging out), opening phone (logging in),
        and verifying that all learning data, study materials, and history remain 100% accessible.
        """
        client, db_file = isolation_client
        data = self._setup_students_and_data(client, db_file)
        student_a_id = data["student_a"]["id"]
        token_laptop = data["student_a"]["token"]

        # Laptop logs out
        logout_res = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token_laptop}"})
        assert logout_res.status_code == 200

        # Laptop session is now revoked
        check_laptop = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_laptop}"})
        assert check_laptop.status_code == 401

        # Mobile Phone logs in with same credentials
        mobile_login = client.post("/api/auth/login", json={
            "email": "student.alpha@gmail.com",
            "password": "PasswordAlpha123"
        })
        assert mobile_login.status_code == 200
        data_mobile = mobile_login.json()
        assert data_mobile["student_id"] == student_a_id  # Stable internal ID
        token_mobile = data_mobile["token"]
        headers_mobile = {"Authorization": f"Bearer {token_mobile}"}

        # Mobile Phone verifies profile
        me_mobile = client.get("/api/auth/me", headers=headers_mobile)
        assert me_mobile.status_code == 200
        assert me_mobile.json()["email"] == "student.alpha@gmail.com"

        # Mobile Phone retrieves study materials
        mat_res = client.get("/api/study-materials", headers=headers_mobile)
        assert mat_res.status_code == 200
        materials = mat_res.json().get("materials", [])
        assert len(materials) == 1
        assert materials[0]["filename"] == "DBMS_Unit_1_Notes.pdf"

        # Mobile Phone retrieves full dashboard progress
        dash_res = client.get(f"/api/student/{student_a_id}/dashboard", headers=headers_mobile)
        assert dash_res.status_code == 200
        dash_data = dash_res.json()
        assert dash_data["overview"]["completed_quizzes"] == 1
        assert len(dash_data["recent_trends"]) == 1
        assert dash_data["recent_trends"][0]["percentage"] == 90.0
