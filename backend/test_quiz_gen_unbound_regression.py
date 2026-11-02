"""
Regression test for UnboundLocalError in create_quiz endpoint when practice_mode == 'general_syllabus'
and notes_session_id or study_material_id is provided.
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app
from services.database import init_db
from services.auth_utils import sign_student_identity
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()
    yield
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_unbound_regression.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    return TestClient(app)


def test_general_syllabus_with_notes_session_id_no_unbound_error(client):
    """
    Ensure that passing notes_session_id and study_material_id with practice_mode='general_syllabus'
    does not cause an UnboundLocalError for session_topics or mat_topics.
    """
    student_id = "reg_test_student_01"
    token = sign_student_identity(student_id)

    from services.curriculum_service import curriculum_service
    curr = curriculum_service.get_curriculum()
    first_course = curr["courses"][0]
    first_topic = first_course["topics"][0]

    mock_quiz = {
        "questions": [
            {
                "question": "What is an algorithm?",
                "options": ["A step-by-step procedure", "A computer monitor", "A programming language", "A variable"],
                "correct_answer": "A step-by-step procedure",
                "explanation": "An algorithm is a step-by-step procedure for solving a problem.",
                "difficulty": "medium",
                "topic": first_topic["name"],
                "concept": "Algorithm Definition",
            }
        ],
        "metadata": {"provider": "mock_groq"}
    }

    with patch("main.generate_quiz", return_value=mock_quiz):
        response = client.post(
            "/api/quiz/generate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "student_id": student_id,
                "course_code": first_course["code"],
                "topic": first_topic["name"],
                "practice_mode": "general_syllabus",
                "notes_session_id": "non_existent_session_id_123",
                "study_material_id": "non_existent_material_id_456",
            },
        )

    # Should succeed with 200 OK, not crash with 500 UnboundLocalError
    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
    data = response.json()
    assert "questions" in data
    assert len(data["questions"]) > 0
    assert data["questions"][0]["question"] == "What is an algorithm?"
    assert data["questions"][0]["concept"] == "Algorithm Definition"
