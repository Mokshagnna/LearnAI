"""
LearnAI — Test Suite for First-Time vs Returning Student Dashboard States
Validates pedagogical correctness of dashboard recommendations and metrics:
- First-time student state: no false performance recommendations, empty review topics, clean explore topics.
- Returning student state: verified attempts, differentiated review topics vs syllabus exploration.
"""

import pytest
from starlette.testclient import TestClient
from main import app
from services.database import (
    get_connection,
    init_db,
    get_or_create_student,
    save_quiz_attempt,
    create_quiz_session,
    get_student_dashboard_data
)
from services.auth_utils import auth_header_for

SAMPLE_QUESTIONS = [
    {
        "question": f"Sample question {i}?",
        "options": ["A", "B", "C", "D"],
        "correct_answer": "A",
        "explanation": f"Explanation {i}",
        "difficulty": "medium",
        "concept": "Neural Networks"
    }
    for i in range(1, 11)
]





@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    db_file = tmp_path / "dashboard_state_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    return client, db_file


class TestFirstTimeStudentDashboardState:
    """Verifies that a first-time student with zero completed quizzes receives no false performance recommendations."""

    def test_first_time_student_has_completed_quizzes_is_false(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("first-time-user-01", conn=conn)

        # Unscoped query (course_code=None)
        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["student_id"] == student_id
        assert data["has_completed_quizzes"] is False
        assert data["overview"]["completed_quizzes"] == 0
        assert data["overview"]["total_questions"] == 0
        assert data["overview"]["total_correct"] == 0
        assert data["overview"]["overall_accuracy"] == 0.0
        assert data["overview"]["topics_practiced"] == 0
        assert data["overview"]["last_practiced_at"] is None

        # Zero performance records
        assert data["recent_trends"] == []
        assert data["topic_progress"] == []
        assert data["concept_insights"]["weak_concepts"] == []
        assert data["concept_insights"]["strong_concepts"] == []

        # No performance-based review recommendations
        assert data["review_topics"] == []
        assert data["recommended_topics"] == []

        # In unscoped view, first-time student with 0 attempts has no practiced courses to explore from
        assert data["explore_topics"] == []

        # Scoped query (course_code="22AM301"): unpracticed syllabus topics are provided for that course
        scoped = get_student_dashboard_data(student_id=student_id, course_code="22AM301", conn=conn)
        assert len(scoped["explore_topics"]) > 0
        assert all(t["course_code"] == "22AM301" for t in scoped["explore_topics"])
        assert all(t["priority"] == "low" for t in scoped["explore_topics"])
        assert all("Unpracticed syllabus topic" in t["reason"] for t in scoped["explore_topics"])
        assert scoped["review_topics"] == []
        assert scoped["recommended_topics"] == []
        conn.close()

    def test_first_time_student_with_uncompleted_session_remains_first_time(self, isolated_db_client):
        """An abandoned or in-progress session with 0 completed attempts must NOT trigger returning-user state."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("in-progress-student", conn=conn)

        # Create session but DO NOT complete or score it
        create_quiz_session(
            session_id="sess-abandoned-1",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Convolutional Networks",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["has_completed_quizzes"] is False
        assert data["overview"]["completed_quizzes"] == 0
        assert data["review_topics"] == []
        assert data["recommended_topics"] == []
        assert data["explore_topics"] == []

        scoped = get_student_dashboard_data(student_id=student_id, course_code="22AM301", conn=conn)
        assert len(scoped["explore_topics"]) > 0
        assert all(t["course_code"] == "22AM301" for t in scoped["explore_topics"])
        conn.close()

    def test_first_time_student_api_endpoint(self, isolated_db_client):
        """HTTP GET /api/student/{id}/dashboard returns has_completed_quizzes=False."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("api-newbie", conn=conn)
        conn.close()

        # Unscoped endpoint call
        res = client.get(f"/api/student/{student_id}/dashboard", headers=auth_header_for(student_id))
        assert res.status_code == 200
        payload = res.json()

        assert payload["has_completed_quizzes"] is False
        assert payload["overview"]["completed_quizzes"] == 0
        assert payload["review_topics"] == []
        assert payload["recommended_topics"] == []
        assert payload["explore_topics"] == []

        # Scoped endpoint call
        scoped_res = client.get(f"/api/student/{student_id}/dashboard?course_code=22AM301", headers=auth_header_for(student_id))
        assert scoped_res.status_code == 200
        scoped_payload = scoped_res.json()

        assert scoped_payload["has_completed_quizzes"] is False
        assert len(scoped_payload["explore_topics"]) > 0
        assert all(t["course_code"] == "22AM301" for t in scoped_payload["explore_topics"])


class TestReturningStudentDashboardState:
    """Verifies that returning students with verified attempts receive real analytics and differentiated recommendations."""

    def test_returning_student_low_accuracy_gets_review_topic(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("returning-user-struggling", conn=conn)

        # Record a low-scoring attempt (3/10 = 30%) with weak concept
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={"medium": 30.0},
            concept_accuracy={"Neural Networks": 30.0},
            weak_concepts=["Chain Rule"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["has_completed_quizzes"] is True
        assert data["overview"]["completed_quizzes"] == 1
        assert data["overview"]["total_questions"] == 10
        assert data["overview"]["total_correct"] == 3
        assert data["overview"]["overall_accuracy"] == 30.0
        assert data["overview"]["topics_practiced"] == 1
        assert data["overview"]["last_practiced_at"] is not None

        # Review topics must contain Backpropagation with high priority
        assert len(data["review_topics"]) >= 1
        rev = data["review_topics"][0]
        assert rev["topic_name"] == "Backpropagation"
        assert rev["priority"] == "high"
        assert "Needs review" in rev["reason"]

        # Practiced topic must NOT appear in explore_topics
        assert all(t["topic_name"] != "Backpropagation" for t in data["explore_topics"])
        conn.close()

    def test_returning_student_all_topics_mastered_on_track(self, isolated_db_client):
        """When a returning student has high accuracy and topic is at advanced level, review_topics is clean (on track)."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("returning-user-master", conn=conn)

        # Record a high-scoring attempt (9/10 = 90%)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Optimization Algorithms",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={"medium": 90.0},
            concept_accuracy={"Neural Networks": 90.0},
            weak_concepts=[],
            strong_concepts=["Adam Optimizer"],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["has_completed_quizzes"] is True
        assert data["overview"]["completed_quizzes"] == 1
        assert data["overview"]["overall_accuracy"] == 90.0
        # When fully advanced and no weak concepts, review_topics is empty (clean on-track state)
        assert data["review_topics"] == []
        # Explore topics provides next syllabus topics to conquer within the practiced course
        assert len(data["explore_topics"]) > 0
        assert all(t["course_code"] == "22AM301" for t in data["explore_topics"])
        conn.close()

    def test_returning_student_ready_to_advance_from_foundational(self, isolated_db_client):
        """When a student achieves high mastery at foundational level, they are recommended for advancement."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("returning-user-foundational", conn=conn)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Activation Functions",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            recommended_difficulty="foundational",
            difficulty_accuracy={"foundational": 90.0},
            concept_accuracy={"ReLU": 90.0},
            weak_concepts=[],
            strong_concepts=["ReLU"],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["has_completed_quizzes"] is True
        assert len(data["review_topics"]) >= 1
        adv = data["review_topics"][0]
        assert adv["topic_name"] == "Activation Functions"
        assert adv["priority"] == "medium"
        assert "Strong mastery" in adv["reason"]
        assert all(t["course_code"] == "22AM301" for t in data["explore_topics"])
        conn.close()


class TestDashboardCourseScopingIsolation:
    """Verifies that dashboard recommendations and explore topics never leak unrelated courses."""

    def test_student_attempted_programming_in_c_never_sees_unrelated_mathematics(self, isolated_db_client):
        """A student who only attempted Programming in C must never see Mathematics/Linear Algebra."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("c-programming-student", conn=conn)

        # Record attempt strictly in Programming in C (22CS101)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers and Dynamic Memory Allocation",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={"medium": 40.0},
            concept_accuracy={"Pointers": 40.0},
            weak_concepts=["Pointer Arithmetic"],
            strong_concepts=[],
            conn=conn
        )

        # Unscoped dashboard query (default view)
        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["has_completed_quizzes"] is True
        assert data["overview"]["completed_quizzes"] == 1
        assert data["overview"]["topics_practiced"] == 1

        # All review and explore topics must strictly belong to 22CS101
        assert len(data["review_topics"]) >= 1
        assert all(t["course_code"] == "22CS101" for t in data["review_topics"])
        assert all(t["course_code"] == "22CS101" for t in data["explore_topics"])
        assert all(t["course_code"] == "22CS101" for t in data["recommended_topics"])

        # Absolutely NO mathematics (22MA101) topics (e.g. Linear Algebra, Adjoint of a Matrix)
        all_topics = data["review_topics"] + data["explore_topics"] + data["recommended_topics"]
        for topic in all_topics:
            assert topic["course_code"] != "22MA101", f"Found unrelated course in topic: {topic}"
            assert "Linear Algebra" not in topic["topic_name"]
            assert "Matrix" not in topic["topic_name"]

        conn.close()

