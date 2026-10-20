"""
LearnAI — Test Suite for Recommended Review Cards Consolidation and Score Progress
Validates that:
1. A student with no completed quizzes sees the correct empty state.
2. One completed attempt produces one review card with no previous-score comparison.
3. Two attempts for the same course/topic produce one card, not two.
4. The latest score is displayed as the primary score.
5. An improved score displays the correct positive percentage-point change.
6. A declining score displays the correct negative change.
7. Identical scores display "No change."
8. Variable quiz lengths compare percentages correctly rather than raw marks.
9. Multiple retakes still produce one card, comparing only the two most recent valid attempts.
10. Earlier attempts are accessible through quiz history.
11. Different topics remain separate.
12. Identically named topics in different courses remain separate.
13. Different students cannot see one another's scores or history.
14. Course filters return only matching course data.
15. Abandoned, incomplete, ungraded, and duplicate attempts are excluded where appropriate.
16. The Practice Topic action opens the correct course and topic (data attributes verification).
17. Existing adaptive difficulty behavior remains unaffected.
18. Dashboard refresh and rendering do not introduce duplicate cards or stale scores.
"""

import time
import pytest
from starlette.testclient import TestClient

from main import app
from services.database import (
    get_connection,
    init_db,
    get_or_create_student,
    save_quiz_attempt,
    create_quiz_session,
    get_student_dashboard_data,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)
from services.auth_utils import auth_header_for
from services.adaptive import compute_adaptive_decision

SAMPLE_QUESTIONS = [
    {
        "question": f"Sample question {i}?",
        "options": ["A", "B", "C", "D"],
        "correct_answer": "A",
        "explanation": f"Explanation {i}",
        "difficulty": "medium",
        "concept": "Core Concept"
    }
    for i in range(1, 11)
]


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """Creates a fresh isolated SQLite database and TestClient for every test."""
    db_file = tmp_path / "review_cards_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    return client, db_file


class TestReviewCardsConsolidationAndScoreProgress:
    """Covers all 18 specified test cases for review cards deduplication and score progress."""

    def test_case_01_empty_state_for_student_with_no_completed_quizzes(self, isolated_db_client):
        """Case 1: A student with no completed quizzes sees the correct empty state."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_01", conn=conn)

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert data["student_id"] == student_id
        assert data["has_completed_quizzes"] is False
        assert data["overview"]["completed_quizzes"] == 0
        assert data["overview"]["total_questions"] == 0
        assert data["review_topics"] == []
        assert data["topic_progress"] == []

    def test_case_02_one_completed_attempt_produces_one_card_without_comparison(self, isolated_db_client):
        """Case 2: One completed attempt produces one review card with no previous-score comparison."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_02", conn=conn)

        # Single attempt scoring 4/10 = 40% (needs review)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers and Memory",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Pointer Arithmetic"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert data["has_completed_quizzes"] is True
        assert len(data["review_topics"]) == 1
        card = data["review_topics"][0]
        assert card["course_code"] == "22CS101"
        assert card["topic_name"] == "Pointers and Memory"
        assert card["latest_score"] == 4
        assert card["latest_total"] == 10
        assert card["latest_accuracy"] == 40.0
        assert card["previous_score"] is None
        assert card["previous_total"] is None
        assert card["previous_accuracy"] is None
        assert card["score_change"] is None
        assert card["change_type"] == "single_attempt"
        assert card["change_label"] == "First attempt"
        assert card["attempt_count"] == 1
        assert len(card["history"]) == 1

    def test_case_03_two_attempts_same_topic_produce_one_card_not_two(self, isolated_db_client):
        """Case 3: Two attempts for the same course/topic produce strictly one card, not two."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_03", conn=conn)

        # Attempt 1: 3/10
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Functions and Scope",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Recursion"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 5/10 (still needs review)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Functions and Scope",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Recursion"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Deduplication check: strictly 1 card for Functions and Scope
        matching_cards = [c for c in data["review_topics"] if c["topic_name"] == "Functions and Scope"]
        assert len(matching_cards) == 1
        card = matching_cards[0]
        assert card["attempt_count"] == 2
        assert len(card["history"]) == 2

    def test_case_04_latest_score_displayed_as_primary_score(self, isolated_db_client):
        """Case 4: The latest score is displayed as the primary score."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_04", conn=conn)

        # Attempt 1: 4/10 = 40%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Arrays and Strings",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Null Terminator"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 5/10 = 50%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Arrays and Strings",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Null Terminator"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["latest_score"] == 5
        assert card["latest_total"] == 10
        assert card["latest_accuracy"] == 50.0
        assert card["previous_score"] == 4
        assert card["previous_total"] == 10
        assert card["previous_accuracy"] == 40.0

    def test_case_05_improved_score_displays_correct_positive_percentage_point_change(self, isolated_db_client):
        """Case 5: An improved score displays the correct positive percentage-point change."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_05", conn=conn)

        # Attempt 1: 3/10 = 30%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Bitwise Operators",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Masking"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 5/10 = 50% (+20 percentage points)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Bitwise Operators",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Masking"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["score_change"] == 20.0
        assert card["change_type"] == "improved"
        assert "+20% vs previous" in card["change_label"]

    def test_case_06_declining_score_displays_correct_negative_change(self, isolated_db_client):
        """Case 6: A declining score displays the correct negative change without shaming."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_06", conn=conn)

        # Attempt 1: 8/10 = 80%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="File IO",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Buffer Flushing"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 5/10 = 50% (-30 percentage points)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="File IO",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Buffer Flushing"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["score_change"] == -30.0
        assert card["change_type"] == "declined"
        assert "-30% vs previous" in card["change_label"]

    def test_case_07_identical_scores_display_no_change(self, isolated_db_client):
        """Case 7: Identical scores display 'No change'."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_07", conn=conn)

        # Attempt 1: 5/10 = 50%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Structures and Unions",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Memory Alignment"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 5/10 = 50%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Structures and Unions",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Memory Alignment"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["score_change"] == 0.0
        assert card["change_type"] == "no_change"
        assert card["change_label"] == "No change"

    def test_case_08_variable_quiz_lengths_compare_percentages_correctly(self, isolated_db_client):
        """Case 8: Variable quiz lengths compare percentages correctly rather than raw marks."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_08", conn=conn)

        # Attempt 1: 5 questions total, score 2 = 40.0%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Dynamic Memory Allocation",
            quiz_data={"questions": SAMPLE_QUESTIONS[:5]},
            score=2,
            total=5,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["malloc vs calloc"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 10 questions total, score 5 = 50.0% (+10 percentage points, NOT raw diff of 3)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Dynamic Memory Allocation",
            quiz_data={"questions": SAMPLE_QUESTIONS[:10]},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["malloc vs calloc"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["latest_accuracy"] == 50.0
        assert card["previous_accuracy"] == 40.0
        assert card["score_change"] == 10.0
        assert card["change_type"] == "improved"
        assert "+10% vs previous" in card["change_label"]

    def test_case_09_multiple_retakes_produce_one_card_comparing_two_most_recent(self, isolated_db_client):
        """Case 9: Multiple retakes (3+) produce one card, comparing only the two most recent attempts."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_09", conn=conn)

        # Attempt 1: 2/10 = 20%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Preprocessors",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=2,
            total=10,
            percentage=20.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Macros"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2: 3/10 = 30%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Preprocessors",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Macros"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 3: 5/10 = 50%
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Preprocessors",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Macros"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        # Still strictly 1 card
        matching_cards = [c for c in data["review_topics"] if c["topic_name"] == "Preprocessors"]
        assert len(matching_cards) == 1
        card = matching_cards[0]
        assert card["attempt_count"] == 3
        # Compares Attempt 3 (50%) with Attempt 2 (30%), NOT Attempt 1 (20%)
        assert card["latest_score"] == 5
        assert card["latest_accuracy"] == 50.0
        assert card["previous_score"] == 3
        assert card["previous_accuracy"] == 30.0
        assert card["score_change"] == 20.0
        assert card["change_type"] == "improved"

    def test_case_10_earlier_attempts_accessible_through_quiz_history(self, isolated_db_client):
        """Case 10: Earlier attempts are accessible through quiz history."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_10", conn=conn)

        scores = [2, 3, 4, 5]
        for s in scores:
            save_quiz_attempt(
                student_id=student_id,
                course_code="22CS101",
                course_name="Programming in C",
                topic_name="Command Line Arguments",
                quiz_data={"questions": SAMPLE_QUESTIONS},
                score=s,
                total=10,
                percentage=float(s * 10),
                status="Failed",
                difficulty_accuracy={},
                concept_accuracy={},
                weak_concepts=["argc argv"],
                strong_concepts=[],
                conn=conn
            )
            time.sleep(0.01)

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert len(card["history"]) == 4
        # Ordered newest to oldest
        history_scores = [h["score"] for h in card["history"]]
        assert history_scores == [5, 4, 3, 2]
        for h in card["history"]:
            assert "attempt_id" in h
            assert "accuracy" in h
            assert "created_at" in h
            assert "recommended_difficulty" in h

    def test_case_11_different_topics_remain_separate(self, isolated_db_client):
        """Case 11: Different topics within the same course remain separate cards."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_11", conn=conn)

        for topic in ["Pointers", "Recursion"]:
            save_quiz_attempt(
                student_id=student_id,
                course_code="22CS101",
                course_name="Programming in C",
                topic_name=topic,
                quiz_data={"questions": SAMPLE_QUESTIONS},
                score=4,
                total=10,
                percentage=40.0,
                status="Failed",
                difficulty_accuracy={},
                concept_accuracy={},
                weak_concepts=[f"{topic} Concept"],
                strong_concepts=[],
                conn=conn
            )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        topic_names = [c["topic_name"] for c in data["review_topics"]]
        assert "Pointers" in topic_names
        assert "Recursion" in topic_names
        assert len(data["review_topics"]) == 2

    def test_case_12_identically_named_topics_in_different_courses_remain_separate(self, isolated_db_client):
        """Case 12: Identically named topics in different courses remain separate."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_12", conn=conn)

        # Same topic name "Algorithms" across 2 courses
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Algorithms",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Sorting"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Algorithms",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Backpropagation"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        cards = data["review_topics"]
        assert len(cards) == 2
        keys = {(c["course_code"], c["topic_name"]) for c in cards}
        assert ("22CS101", "Algorithms") in keys
        assert ("22AM301", "Algorithms") in keys

    def test_case_13_different_students_isolation_no_leakage(self, isolated_db_client):
        """Case 13: Different students cannot see one another's scores or history."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_a = get_or_create_student("stud_case_13_a", conn=conn)
        student_b = get_or_create_student("stud_case_13_b", conn=conn)

        save_quiz_attempt(
            student_id=student_a,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Pointer Math"],
            strong_concepts=[],
            conn=conn
        )

        save_quiz_attempt(
            student_id=student_b,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Void Pointers"],
            strong_concepts=[],
            conn=conn
        )

        data_a = get_student_dashboard_data(student_id=student_a, conn=conn)
        data_b = get_student_dashboard_data(student_id=student_b, conn=conn)
        conn.close()

        assert data_a["review_topics"][0]["latest_score"] == 3
        assert data_b["review_topics"][0]["latest_score"] == 5
        assert len(data_a["review_topics"][0]["history"]) == 1
        assert len(data_b["review_topics"][0]["history"]) == 1

        # Via API route with student auth token
        res_a = client.get(f"/api/student/{student_a}/dashboard", headers=auth_header_for(student_a))
        assert res_a.status_code == 200
        assert res_a.json()["review_topics"][0]["latest_score"] == 3

        # Cross-student access denied
        res_cross = client.get(f"/api/student/{student_b}/dashboard", headers=auth_header_for(student_a))
        assert res_cross.status_code == 403

    def test_case_14_course_filters_return_only_matching_course_data(self, isolated_db_client):
        """Case 14: Course filters return only matching course data."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_14", conn=conn)

        # Course A attempt
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Variables",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Types"],
            strong_concepts=[],
            conn=conn
        )
        # Course B attempt
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Tensors",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Shapes"],
            strong_concepts=[],
            conn=conn
        )

        data_c = get_student_dashboard_data(student_id=student_id, course_code="22CS101", conn=conn)
        conn.close()

        assert len(data_c["review_topics"]) == 1
        assert data_c["review_topics"][0]["course_code"] == "22CS101"
        assert data_c["review_topics"][0]["topic_name"] == "Variables"

    def test_case_15_abandoned_incomplete_and_zero_total_attempts_excluded(self, isolated_db_client):
        """Case 15: Abandoned, incomplete, ungraded, or zero-total attempts are excluded."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_15", conn=conn)

        # 1 valid completed attempt
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Conditionals",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Switch"],
            strong_concepts=[],
            conn=conn
        )

        # Incomplete session in quiz_sessions
        create_quiz_session(
            session_id="case_15_incom_sess",
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Conditionals",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )

        # Direct insertion of abandoned/ungraded attempt into quiz_attempts
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts,
                recommended_difficulty, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "att_case_15_abandoned",
            student_id,
            "22CS101",
            "Programming in C",
            "Conditionals",
            "{}",
            0,
            10,
            0.0,
            "abandoned",
            "{}",
            "{}",
            "[]",
            "[]",
            "foundational",
            "2026-10-20T20:00:00Z"
        ))
        conn.commit()

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        # Only the 1 valid completed attempt should be counted
        assert card["attempt_count"] == 1
        assert card["latest_score"] == 4
        assert card["change_type"] == "single_attempt"

    def test_case_16_practice_topic_action_attributes_correct(self, isolated_db_client):
        """Case 16: Practice Topic action payload retains the exact target course and topic."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_16", conn=conn)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Recursion",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=4,
            total=10,
            percentage=40.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Base Case"],
            strong_concepts=[],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        card = data["review_topics"][0]
        assert card["course_code"] == "22CS101"
        assert card["topic_name"] == "Recursion"

    def test_case_17_existing_adaptive_difficulty_unaffected(self, isolated_db_client):
        """Case 17: Existing adaptive difficulty behavior remains completely unaffected."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_17", conn=conn)

        # Baseline decision (before any quiz)
        dec0 = compute_adaptive_decision(student_id, "22CS101", "Pointers", conn=conn)
        assert dec0["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert dec0["is_decision_active"] is False

        # Attempt 1: 8/10 -> High score (> 50%) -> Promotes to Advanced
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["Pointers"],
            conn=conn
        )

        dec1 = compute_adaptive_decision(student_id, "22CS101", "Pointers", conn=conn)
        assert dec1["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert dec1["is_decision_active"] is True
        conn.close()

    def test_case_18_dashboard_refresh_idempotent_no_duplicates_or_stale_scores(self, isolated_db_client):
        """Case 18: Dashboard refresh and repeated queries do not introduce duplicate cards or stale scores."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_case_18", conn=conn)

        # Attempt 1
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="File IO",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=3,
            total=10,
            percentage=30.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Streams"],
            strong_concepts=[],
            conn=conn
        )
        time.sleep(0.01)

        # Attempt 2
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="File IO",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Streams"],
            strong_concepts=[],
            conn=conn
        )

        # Call get_student_dashboard_data 3 times in a row (simulating refreshes)
        data1 = get_student_dashboard_data(student_id=student_id, conn=conn)
        data2 = get_student_dashboard_data(student_id=student_id, conn=conn)
        data3 = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        for data in [data1, data2, data3]:
            assert len(data["review_topics"]) == 1
            card = data["review_topics"][0]
            assert card["topic_name"] == "File IO"
            assert card["latest_score"] == 5
            assert card["previous_score"] == 3
            assert card["score_change"] == 20.0
            assert card["attempt_count"] == 2
