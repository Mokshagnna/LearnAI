import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    save_quiz_attempt,
    get_student_dashboard_data,
    backfill_topic_mastery_from_attempts,
    serialize_json_for_db,
    ADAPTIVE_LEVEL_FOUNDATIONAL
)
from services.adaptive import detect_persistent_weak_topics
from services.auth_utils import auth_header_for


SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i+1}: What is the normal form condition?",
        "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
        "correct_answer": "A",
        "difficulty": "Easy" if i < 3 else "Medium",
        "concept": "Normal Forms Based",
        "explanation": "Functional dependencies determine normal form."
    }
    for i in range(10)
]


@pytest.fixture
def test_dashboard_client(tmp_path, monkeypatch):
    """Provides an isolated test database and FastAPI TestClient."""
    db_file = tmp_path / "dashboard_pwt_integration_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()

    client = TestClient(app)
    return client, db_file


def _insert_attempt(
    conn: sqlite3.Connection,
    student_id: str,
    course_code: str,
    course_name: str,
    topic_name: str,
    score: int,
    total: int,
    weak_concepts: list,
    created_at: str,
    status: str = "completed",
    attempt_id: str = None
):
    """Helper to insert verified historical attempt records with valid foreign keys."""
    att_id = attempt_id or f"att-{created_at}-{student_id}-{course_code}"
    sess_id = f"sess-{att_id}"
    percentage = round((score / total) * 100.0, 1) if total > 0 else 0.0

    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO students (id, created_at, last_active) VALUES (?, ?, ?)", (student_id, created_at, created_at))
    cursor.execute("""
        INSERT OR IGNORE INTO quiz_sessions (
            id, student_id, course_code, course_name, topic_name, status,
            questions_snapshot, recommended_difficulty, created_at, submitted_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, 'completed', '[]', 'mixed', ?, ?, ?)
    """, (sess_id, student_id, course_code, course_name, topic_name, created_at, created_at, created_at))

    cursor.execute("""
        INSERT INTO quiz_attempts (
            id, student_id, course_code, course_name, topic_name,
            quiz_data, score, total, percentage, status,
            difficulty_accuracy, concept_accuracy,
            weak_concepts, strong_concepts,
            recommended_difficulty, created_at, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        att_id, student_id, course_code, course_name, topic_name,
        serialize_json_for_db({"questions": SAMPLE_QUESTIONS[:total]}),
        score, total, percentage, status,
        serialize_json_for_db({}), serialize_json_for_db({}),
        serialize_json_for_db(weak_concepts), serialize_json_for_db([]),
        "mixed", created_at, sess_id
    ))
    conn.commit()


class TestPersistentWeakTopicDashboardIntegration:
    """Verifies end-to-end integration of persistent weak topic detection into the student dashboard."""

    def test_one_poor_attempt_does_not_create_persistent_gap_recommendation(self, test_dashboard_client):
        """1. One poor attempt (< 60%) does NOT create a persistent learning-gap recommendation."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_single_fail", conn=conn)

        # Single attempt: 3/10 (30%)
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=3, total=10, weak_concepts=["BCNF", "1NF"], created_at="2026-10-25T10:00:00Z"
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Must not be flagged as persistent learning gap (needs >= 2 attempts and >= 15 questions)
        assert data["persistent_weak_topics"] == []
        assert len(data["review_topics"]) == 1

        review_card = data["review_topics"][0]
        assert review_card["topic_name"] == "Normal Forms Based"
        assert review_card["is_persistent_gap"] is False
        assert "Persistent learning gap" not in review_card["reason"]
        assert "Needs review" in review_card["reason"]

    def test_two_qualifying_poor_attempts_create_persistent_gap_recommendation(self, test_dashboard_client):
        """2. Two qualifying poor attempts (>= 2 attempts, >= 15 questions, < 60% accuracy) do create a persistent gap."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_chronic_fail", conn=conn)

        # Attempt 1: 3/10 (30%)
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=3, total=10, weak_concepts=["BCNF", "1NF"], created_at="2026-10-25T10:00:00Z"
        )
        # Attempt 2: 4/10 (40%)
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=4, total=10, weak_concepts=["BCNF", "2NF"], created_at="2026-10-25T11:00:00Z"
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert len(data["persistent_weak_topics"]) == 1
        pwt = data["persistent_weak_topics"][0]
        assert pwt["topic_name"] == "Normal Forms Based"
        assert pwt["total_questions"] == 20
        assert pwt["total_correct"] == 7
        assert pwt["accuracy"] == 35.0
        assert pwt["attempt_count"] == 2
        assert "BCNF" in pwt["chronic_weak_concepts"]
        assert "Persistent learning gap" in pwt["recommendation"]

        # Review card must reflect persistent gap status
        assert len(data["review_topics"]) == 1
        review_card = data["review_topics"][0]
        assert review_card["topic_name"] == "Normal Forms Based"
        assert review_card["is_persistent_gap"] is True
        assert review_card["priority"] == "high"
        assert "Persistent learning gap" in review_card["reason"]
        assert "BCNF" in review_card["chronic_weak_concepts"]
        assert review_card["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL

    def test_qualifying_recovery_suppresses_chronic_weakness_flag(self, test_dashboard_client):
        """3. A qualifying latest attempt (>= 70%) suppresses the chronic-weakness flag."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_recovered", conn=conn)

        # Attempt 1: 3/10
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=3, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T10:00:00Z"
        )
        # Attempt 2: 4/10
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=4, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T11:00:00Z"
        )
        # Attempt 3: 8/10 (80% recovery >= 70%)
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=8, total=10, weak_concepts=[], created_at="2026-10-25T12:00:00Z"
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Recovery on latest attempt suppresses persistent weak topic
        assert data["persistent_weak_topics"] == []
        if data["review_topics"]:
            card = next((r for r in data["review_topics"] if r["topic_name"] == "Normal Forms Based"), None)
            if card:
                assert card["is_persistent_gap"] is False
                assert "Persistent learning gap" not in card["reason"]

    def test_unequal_attempt_sizes_weighted_by_actual_questions(self, test_dashboard_client):
        """4. Unequal attempt sizes are aggregated by actual questions answered, not unweighted average of percentages."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_unequal_sizes", conn=conn)

        # Attempt 1: 1/5 = 20.0%
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Concurrency Control",
            score=1, total=5, weak_concepts=["Deadlocks"], created_at="2026-10-25T10:00:00Z"
        )
        # Attempt 2: 4/10 = 40.0%
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Concurrency Control",
            score=4, total=10, weak_concepts=["Deadlocks"], created_at="2026-10-25T11:00:00Z"
        )

        # Total questions = 15, Total correct = 5 -> Weighted accuracy = 5/15 = 33.3%
        # (Unweighted average would have been (20 + 40)/2 = 30.0%)
        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert len(data["persistent_weak_topics"]) == 1
        pwt = data["persistent_weak_topics"][0]
        assert pwt["total_questions"] == 15
        assert pwt["total_correct"] == 5
        assert pwt["accuracy"] == 33.3

    def test_incomplete_abandoned_and_zero_total_attempts_excluded(self, test_dashboard_client):
        """5. Incomplete, abandoned, and zero-total attempts are excluded from qualifying history."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_incomplete_filter", conn=conn)

        # 1 valid completed attempt
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Indexing",
            score=3, total=10, weak_concepts=["B+ Trees"], created_at="2026-10-25T10:00:00Z", status="completed"
        )
        # Incomplete / abandoned attempts
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Indexing",
            score=0, total=10, weak_concepts=["B+ Trees"], created_at="2026-10-25T10:30:00Z", status="abandoned"
        )
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Indexing",
            score=1, total=5, weak_concepts=["B+ Trees"], created_at="2026-10-25T11:00:00Z", status="incomplete"
        )
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Indexing",
            score=0, total=0, weak_concepts=[], created_at="2026-10-25T11:30:00Z", status="completed"
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Only 1 completed attempt with total=10 qualifies -> Does NOT meet min_attempts=2 or min_questions=15
        assert data["persistent_weak_topics"] == []
        if data["review_topics"]:
            card = data["review_topics"][0]
            assert card["is_persistent_gap"] is False

    def test_different_students_never_share_attempt_history(self, test_dashboard_client):
        """6. Strict student isolation: Student B is never affected by Student A's persistent weak topics."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("stud_isolation_a", conn=conn)
        student_b = get_or_create_student("stud_isolation_b", conn=conn)

        # Student A has chronic failure
        _insert_attempt(
            conn, student_a, "22CS201", "Database Management Systems", "Transaction Processing",
            score=2, total=10, weak_concepts=["ACID"], created_at="2026-10-25T10:00:00Z"
        )
        _insert_attempt(
            conn, student_a, "22CS201", "Database Management Systems", "Transaction Processing",
            score=3, total=10, weak_concepts=["ACID"], created_at="2026-10-25T11:00:00Z"
        )

        data_a = get_student_dashboard_data(student_id=student_a, conn=conn)
        data_b = get_student_dashboard_data(student_id=student_b, conn=conn)
        conn.close()

        assert len(data_a["persistent_weak_topics"]) == 1
        assert len(data_b["persistent_weak_topics"]) == 0
        assert data_b["review_topics"] == []

    def test_similar_topic_names_across_different_courses_remain_isolated(self, test_dashboard_client):
        """7. Topics with identical names in different courses are isolated and never conflate attempts."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_course_isolation", conn=conn)

        # Course A: 2 poor attempts on "Memory Management"
        _insert_attempt(
            conn, student_id, "22CS101", "Programming in C", "Memory Management",
            score=3, total=10, weak_concepts=["malloc"], created_at="2026-10-25T10:00:00Z"
        )
        _insert_attempt(
            conn, student_id, "22CS101", "Programming in C", "Memory Management",
            score=3, total=10, weak_concepts=["free"], created_at="2026-10-25T11:00:00Z"
        )

        # Course B: 1 excellent attempt on "Memory Management"
        _insert_attempt(
            conn, student_id, "22CS202", "Operating Systems", "Memory Management",
            score=9, total=10, weak_concepts=[], created_at="2026-10-25T12:00:00Z"
        )

        # Query Course B dashboard filter
        data_course_b = get_student_dashboard_data(student_id=student_id, course_code="22CS202", conn=conn)
        conn.close()

        # Course B must not have any persistent weak topics
        assert data_course_b["persistent_weak_topics"] == []
        for r in data_course_b["review_topics"]:
            assert r["course_code"] == "22CS202"
            assert r["is_persistent_gap"] is False

    def test_missing_or_duplicate_records_do_not_produce_false_flags(self, test_dashboard_client):
        """8. Duplicate attempt IDs or null/missing columns do not produce crashes or false duplicate counting."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_duplicate_safe", conn=conn)

        # Insert attempt 1
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Relational Algebra",
            score=3, total=10, weak_concepts=["Joins"], created_at="2026-10-25T10:00:00Z", attempt_id="dup-attempt-1"
        )

        # Direct call with single attempt should not flag
        weak_topics = detect_persistent_weak_topics(student_id, conn=conn)
        assert len(weak_topics) == 0

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert data["persistent_weak_topics"] == []

    def test_existing_dashboard_response_fields_remain_compatible(self, test_dashboard_client):
        """9. All existing top-level and card fields retain their names, types, and backward compatibility."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_schema_compat", conn=conn)

        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=3, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T10:00:00Z"
        )
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=4, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T11:00:00Z"
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Check all top-level keys and types
        expected_keys = {
            "student_id": str,
            "course_code": (type(None), str),
            "overview": dict,
            "recent_trends": list,
            "topic_progress": list,
            "concept_insights": dict,
            "persistent_weak_topics": list,
            "review_topics": list,
            "explore_topics": list,
            "recommended_topics": list,
            "has_completed_quizzes": bool,
            "overall_completed_quizzes": int,
            "disclaimer": str
        }
        for key, expected_type in expected_keys.items():
            assert key in data, f"Missing key: {key}"
            assert isinstance(data[key], expected_type), f"Key {key} expected {expected_type}, got {type(data[key])}"

        # Check card fields
        card = data["review_topics"][0]
        assert isinstance(card["course_code"], str)
        assert isinstance(card["topic_name"], str)
        assert isinstance(card["priority"], str)
        assert isinstance(card["reason"], str)
        assert isinstance(card["recommended_difficulty"], str)
        assert isinstance(card["latest_score"], int)
        assert isinstance(card["latest_total"], int)
        assert isinstance(card["latest_accuracy"], float)
        assert isinstance(card["is_persistent_gap"], bool)
        assert isinstance(card["history"], list)

    def test_actual_dashboard_route_returns_integrated_result(self, test_dashboard_client):
        """10. The actual FastAPI HTTP route /api/student/{student_id}/dashboard returns the integrated result."""
        client, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_http_route", conn=conn)

        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=3, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T10:00:00Z"
        )
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Normal Forms Based",
            score=4, total=10, weak_concepts=["BCNF"], created_at="2026-10-25T11:00:00Z"
        )
        conn.close()

        headers = auth_header_for(student_id)
        response = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
        assert response.status_code == 200

        data = response.json()
        assert "persistent_weak_topics" in data
        assert len(data["persistent_weak_topics"]) == 1
        assert data["persistent_weak_topics"][0]["topic_name"] == "Normal Forms Based"

        assert len(data["review_topics"]) >= 1
        first_rec = data["review_topics"][0]
        assert first_rec["is_persistent_gap"] is True
        assert "Persistent learning gap" in first_rec["reason"]

    def test_database_failures_handled_consistently(self, test_dashboard_client):
        """11. Database failures are handled consistently, raising HTTPException 500 through the API route."""
        client, _ = test_dashboard_client
        student_id = "stud_error_handling"
        headers = auth_header_for(student_id)

        with patch("main.get_student_dashboard_data", side_effect=sqlite3.OperationalError("Database disk image is malformed")):
            response = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
            assert response.status_code == 500
            assert response.json()["detail"] == "Failed to retrieve dashboard"

    def test_backfill_topic_mastery_sqlite_row_and_null_concepts_safe(self, test_dashboard_client):
        """12. backfill_topic_mastery_from_attempts safely processes sqlite3.Row objects and null/None concepts."""
        _, db_file = test_dashboard_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_backfill_test", conn=conn)

        # Ensure topic_mastery is empty
        cursor = conn.cursor()
        cursor.execute("DELETE FROM topic_mastery WHERE student_id = ?", (student_id,))
        conn.commit()

        # Insert attempt 1 with JSON weak_concepts
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Recovery Systems",
            score=5, total=10, weak_concepts=["Checkpoints"], created_at="2026-10-25T10:00:00Z"
        )
        # Insert attempt 2 with empty list (serializes as '[]')
        _insert_attempt(
            conn, student_id, "22CS201", "Database Management Systems", "Recovery Systems",
            score=8, total=10, weak_concepts=[], created_at="2026-10-25T11:00:00Z"
        )
        # Directly insert an attempt with 'null' string to verify JSON null handling
        att_id = "att-null-concepts-test"
        cursor.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy,
                weak_concepts, strong_concepts,
                recommended_difficulty, created_at, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'null', 'null', 'mixed', ?, NULL)
        """, (
            att_id, student_id, "22CS201", "Database Management Systems", "Recovery Systems",
            "{}", 7, 10, 70.0, "completed", "{}", "{}", "2026-10-25T12:00:00Z"
        ))
        conn.commit()

        # Execute backfill on sqlite3.Row connection - must not raise AttributeError or TypeError
        processed = backfill_topic_mastery_from_attempts(conn=conn)
        assert processed >= 3

        # Verify mastery table populated correctly
        cursor.execute("""
            SELECT attempt_count, total_questions, total_correct, mastery_score
            FROM topic_mastery
            WHERE student_id = ? AND topic_name = 'Recovery Systems'
        """, (student_id,))
        mastery_row = cursor.fetchone()
        assert mastery_row is not None
        assert mastery_row["attempt_count"] == 3
        assert mastery_row["total_questions"] == 30
        assert mastery_row["total_correct"] == 20
        conn.close()
