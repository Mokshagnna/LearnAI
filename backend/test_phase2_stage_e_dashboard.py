import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_quiz_session,
    save_quiz_attempt,
    save_student_answers,
    upsert_topic_mastery,
    get_student_dashboard_data,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)
from services.auth_utils import auth_header_for


SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i+1}: What is the operational principle of McCulloch-Pitts neuron?",
        "options": {
            "A": "Linear threshold activation",
            "B": "Radial basis kernel",
            "C": "Dynamic routing",
            "D": "Recurrent memory cell"
        },
        "correct_answer": "A",
        "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
        "concept": "Neuron Model" if i < 5 else "Activation Functions",
        "explanation": "McCulloch-Pitts neuron uses a threshold activation rule over binary inputs."
    }
    for i in range(10)
]


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """
    Creates an isolated temporary SQLite database, initializes schema,
    resets rate limiters, and sets LEARNAI_DB_PATH so all application routes use the isolated database.
    """
    db_file = tmp_path / "stage_e_dashboard_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()

    client = TestClient(app)
    return client, db_file


class TestDashboardOverviewMetrics:
    """Verifies overview statistics aggregation, accuracy calculation, and session filtering."""

    def test_empty_state_for_new_student(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("new-student-1", conn=conn)

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["student_id"] == student_id
        assert data["course_code"] is None
        assert data["overview"]["completed_quizzes"] == 0
        assert data["overview"]["total_questions"] == 0
        assert data["overview"]["total_correct"] == 0
        assert data["overview"]["overall_accuracy"] == 0.0
        assert data["overview"]["topics_practiced"] == 0
        assert data["overview"]["last_practiced_at"] is None

        assert data["recent_trends"] == []
        assert data["topic_progress"] == []
        assert data["concept_insights"]["weak_concepts"] == []
        assert data["concept_insights"]["strong_concepts"] == []
        assert len(data["recommended_topics"]) > 0
        assert "algorithmic practice heuristics" in data["disclaimer"]
        conn.close()

    def test_overview_accuracy_and_denominators(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("acc-student", conn=conn)

        # Attempt 1: 7 / 10
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="History of Deep Learning",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=7,
            total=10,
            percentage=70.0,
            status="Passed",
            difficulty_accuracy={"Easy": {"correct": 4, "total": 4}},
            concept_accuracy={"Neuron Model": {"correct": 4, "total": 5}},
            weak_concepts=["Activation Functions"],
            strong_concepts=["Neuron Model"],
            recommended_difficulty="mixed",
            conn=conn
        )

        # Attempt 2: 9 / 10
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="History of Deep Learning",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={"Easy": {"correct": 4, "total": 4}},
            concept_accuracy={"Neuron Model": {"correct": 5, "total": 5}},
            weak_concepts=[],
            strong_concepts=["Neuron Model", "Activation Functions"],
            recommended_difficulty="advanced",
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["overview"]["completed_quizzes"] == 2
        assert data["overview"]["total_questions"] == 20
        assert data["overview"]["total_correct"] == 16
        # 16 / 20 = 80.0%
        assert data["overview"]["overall_accuracy"] == 80.0
        assert data["overview"]["topics_practiced"] == 1
        assert data["overview"]["last_practiced_at"] is not None
        conn.close()

    def test_in_progress_and_expired_sessions_excluded(self, isolated_db_client):
        """Validates that in_progress and expired sessions are NOT counted as completed quizzes."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("session-filter-student", conn=conn)

        # 1. One completed attempt
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="History of Deep Learning",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )

        # 2. One in_progress quiz_session
        create_quiz_session(
            session_id="active-sess-1",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Convolutional Networks",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )

        # 3. One expired quiz_session
        create_quiz_session(
            session_id="expired-sess-2",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Recurrent Networks",
            questions=SAMPLE_QUESTIONS,
            duration_seconds=-3600,  # expired in the past
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        # Only the completed attempt must be counted!
        assert data["overview"]["completed_quizzes"] == 1
        assert data["overview"]["total_questions"] == 10
        assert data["overview"]["total_correct"] == 8
        assert data["overview"]["topics_practiced"] == 1
        conn.close()


class TestDashboardStudentAndCourseIsolation:
    """Verifies strict isolation between students and courses."""

    def test_strict_student_isolation(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("student-alpha", conn=conn)
        student_b = get_or_create_student("student-beta", conn=conn)

        # Student A completes 2 quizzes
        for i in range(2):
            save_quiz_attempt(
                student_id=student_a,
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name=f"Topic A{i}",
                quiz_data={"questions": SAMPLE_QUESTIONS},
                score=10,
                total=10,
                percentage=100.0,
                status="Passed",
                difficulty_accuracy={},
                concept_accuracy={},
                weak_concepts=[],
                strong_concepts=["ConceptAlpha"],
                conn=conn
            )

        # Student B completes 1 quiz
        save_quiz_attempt(
            student_id=student_b,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Topic B0",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["ConceptBeta"],
            strong_concepts=[],
            conn=conn
        )

        data_a = get_student_dashboard_data(student_id=student_a, conn=conn)
        data_b = get_student_dashboard_data(student_id=student_b, conn=conn)

        # Student A checks
        assert data_a["overview"]["completed_quizzes"] == 2
        assert data_a["overview"]["total_questions"] == 20
        assert data_a["overview"]["overall_accuracy"] == 100.0
        assert all("Topic B" not in t["topic_name"] for t in data_a["recent_trends"])
        assert "ConceptBeta" not in data_a["concept_insights"]["weak_concepts"]

        # Student B checks
        assert data_b["overview"]["completed_quizzes"] == 1
        assert data_b["overview"]["total_questions"] == 10
        assert data_b["overview"]["overall_accuracy"] == 30.0
        assert all("Topic A" not in t["topic_name"] for t in data_b["recent_trends"])
        assert "ConceptAlpha" not in data_b["concept_insights"]["strong_concepts"]
        conn.close()

    def test_course_code_filtering(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("course-filter-student", conn=conn)

        # 2 attempts on 22AM301
        for i in range(2):
            save_quiz_attempt(
                student_id=student_id,
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Deep Learning Topic",
                quiz_data={"questions": SAMPLE_QUESTIONS},
                score=8,
                total=10,
                percentage=80.0,
                status="Passed",
                difficulty_accuracy={},
                concept_accuracy={},
                weak_concepts=[],
                strong_concepts=[],
                conn=conn
            )

        # 1 attempt on 22CS201
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS201",
            course_name="Data Structures",
            topic_name="Binary Search Trees",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=6,
            total=10,
            percentage=60.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )

        # Query filtered by 22AM301
        data_filtered = get_student_dashboard_data(student_id=student_id, course_code="22AM301", conn=conn)
        assert data_filtered["course_code"] == "22AM301"
        assert data_filtered["overview"]["completed_quizzes"] == 2
        assert data_filtered["overview"]["total_questions"] == 20
        assert len(data_filtered["recent_trends"]) == 2
        assert all(t["course_code"] == "22AM301" for t in data_filtered["recent_trends"])

        # Query without filter (All courses)
        data_all = get_student_dashboard_data(student_id=student_id, conn=conn)
        assert data_all["overview"]["completed_quizzes"] == 3
        assert data_all["overview"]["total_questions"] == 30
        assert len(data_all["recent_trends"]) == 3
        conn.close()


class TestDashboardCumulativeVsRecentAccuracy:
    """Verifies clean distinction between cumulative accuracy and recent 10-question accuracy."""

    def test_cumulative_vs_recent_10_question_accuracy(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("acc-distinction-student", conn=conn)

        # Attempt 1: 0 / 10 (all 10 questions incorrect)
        create_quiz_session(
            session_id="sess-att-1",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        attempt_id_1 = save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=0,
            total=10,
            percentage=0.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Perceptron Learning Rule"],
            strong_concepts=[],
            session_id="sess-att-1",
            details=[
                {
                    "question": q["question"],
                    "selected_answer": "B",
                    "correct_answer": "A",
                    "is_correct": False,
                    "difficulty": q["difficulty"],
                    "concept": q["concept"],
                    "explanation": q["explanation"]
                }
                for q in SAMPLE_QUESTIONS
            ],
            conn=conn
        )

        # Attempt 2: 10 / 10 (all 10 questions correct)
        create_quiz_session(
            session_id="sess-att-2",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        attempt_id_2 = save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=10,
            total=10,
            percentage=100.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["Perceptron Learning Rule"],
            session_id="sess-att-2",
            details=[
                {
                    "question": q["question"],
                    "selected_answer": "A",
                    "correct_answer": "A",
                    "is_correct": True,
                    "difficulty": q["difficulty"],
                    "concept": q["concept"],
                    "explanation": q["explanation"]
                }
                for q in SAMPLE_QUESTIONS
            ],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        topic = next(t for t in data["topic_progress"] if t["topic_name"] == "Perceptrons")
        # Cumulative: 10 correct / 20 total = 50.0%
        assert topic["total_questions"] == 20
        assert topic["total_correct"] == 10
        assert topic["cumulative_accuracy"] == 50.0

        # Recent (last 10 questions from attempt 2): 10 correct / 10 = 100.0%
        assert topic["recent_accuracy"] == 100.0
        assert topic["recent_questions_count"] == 10
        conn.close()


class TestDashboardTopicMasteryAndAdaptiveProgression:
    """Verifies adaptive level and concepts propagation in topic progress."""

    def test_adaptive_level_and_concepts_in_topic_progress(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("adaptive-prog-student", conn=conn)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["Chain Rule"],
            recommended_difficulty="advanced",
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert len(data["topic_progress"]) == 1
        tp = data["topic_progress"][0]
        assert tp["topic_name"] == "Backpropagation"
        assert tp["current_level"] in [ADAPTIVE_LEVEL_FOUNDATIONAL, ADAPTIVE_LEVEL_MIXED, ADAPTIVE_LEVEL_ADVANCED]
        assert "Chain Rule" in tp["strong_concepts"]
        assert "Chain Rule" in data["concept_insights"]["strong_concepts"]
        conn.close()


class TestDashboardPedagogicalRecommendations:
    """Verifies deterministic recommendations heuristics and explicit disclaimer."""

    def test_recommendations_low_accuracy_high_priority(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("rec-student", conn=conn)

        # Struggling on topic: 3/10
        create_quiz_session(
            session_id="sess-vanish",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Vanishing Gradients",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Vanishing Gradients",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Sigmoid Saturation"],
            strong_concepts=[],
            session_id="sess-vanish",
            details=[
                {
                    "question": q["question"],
                    "selected_answer": "B",
                    "correct_answer": "A",
                    "is_correct": False,
                    "difficulty": q["difficulty"],
                    "concept": "Sigmoid Saturation",
                    "explanation": ""
                }
                for q in SAMPLE_QUESTIONS
            ],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        recs = data["recommended_topics"]
        assert len(recs) > 0
        high_rec = next((r for r in recs if r["topic_name"] == "Vanishing Gradients"), None)
        assert high_rec is not None
        assert high_rec["priority"] == "high"
        assert "Needs review" in high_rec["reason"]
        assert "Sigmoid Saturation" in high_rec["reason"]
        conn.close()

    def test_unpracticed_syllabus_topics_recommended(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("unpracticed-student", conn=conn)

        data = get_student_dashboard_data(student_id=student_id, course_code="22AM301", conn=conn)

        # For a new student with no attempts, curriculum topics should be suggested
        recs = data["recommended_topics"]
        assert len(recs) > 0
        assert all(r["course_code"] == "22AM301" for r in recs)
        assert any("Unpracticed syllabus topic" in r["reason"] for r in recs)
        conn.close()


class TestDashboardHistoricalCompatibility:
    """Verifies backward compatibility with historical attempts without session_id."""

    def test_historical_attempts_without_session_id_included(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("legacy-student", conn=conn)

        # Insert attempt without session_id (legacy Phase 1 attempt)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Historical Deep Learning",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["History"],
            session_id=None,
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)

        assert data["overview"]["completed_quizzes"] == 1
        assert data["overview"]["total_questions"] == 10
        assert data["overview"]["total_correct"] == 8
        assert data["overview"]["overall_accuracy"] == 80.0
        assert len(data["recent_trends"]) == 1
        assert data["recent_trends"][0]["topic_name"] == "Historical Deep Learning"
        assert len(data["topic_progress"]) == 1
        assert data["topic_progress"][0]["topic_name"] == "Historical Deep Learning"
        conn.close()


class TestDashboardApiEndpoint:
    """Verifies FastAPI HTTP endpoint /api/student/{student_id}/dashboard."""

    def test_dashboard_endpoint_http_200(self, isolated_db_client):
        client, db_file = isolated_db_client

        # Seed one attempt via database function
        conn = get_connection(db_file)
        student_id = get_or_create_student("api-student-1", conn=conn)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="API Deep Learning",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["API"],
            conn=conn
        )
        conn.close()

        response = client.get(
            f"/api/student/{student_id}/dashboard",
            headers=auth_header_for(student_id)
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["student_id"] == student_id
        assert "overview" in payload
        assert payload["overview"]["completed_quizzes"] == 1
        assert payload["overview"]["overall_accuracy"] == 90.0
        assert "recent_trends" in payload
        assert "topic_progress" in payload
        assert "concept_insights" in payload
        assert "recommended_topics" in payload
        assert "disclaimer" in payload

    def test_dashboard_endpoint_course_filter_and_limit(self, isolated_db_client):
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("api-student-2", conn=conn)

        for i in range(5):
            save_quiz_attempt(
                student_id=student_id,
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name=f"DL Topic {i}",
                quiz_data={"questions": SAMPLE_QUESTIONS},
                score=7,
                total=10,
                percentage=70.0,
                status="Passed",
                difficulty_accuracy={},
                concept_accuracy={},
                weak_concepts=[],
                strong_concepts=[],
                conn=conn
            )
        conn.close()

        # Query with limit=3
        res = client.get(
            f"/api/student/{student_id}/dashboard?course_code=22AM301&limit=3",
            headers=auth_header_for(student_id)
        )
        assert res.status_code == 200
        data = res.json()

        assert data["course_code"] == "22AM301"
        assert data["overview"]["completed_quizzes"] == 5
        # recent_trends is bounded by limit=3
        assert len(data["recent_trends"]) == 3
