"""
LearnAI — Step-Wise Adaptive Quiz Difficulty Test Suite
Comprehensive verification of Rule A, Rule B, Rule C, Rule D, boundaries, and student/topic isolation:
1. First attempt scores 8/10: next quiz difficulty increases by one level.
2. A second consecutive high score increases it by another level.
3. First attempt scores 4/10: difficulty does not immediately decrease.
4. Two consecutive scores below 5/10: next quiz difficulty decreases by one level.
5. Scores 4/10, 7/10, 3/10 do not trigger a low-streak decrease.
6. A score of exactly 5/10 does not trigger an increase or count as a low score.
7. Difficulty never exceeds the maximum or falls below the minimum.
8. Adaptive state for one topic does not affect another topic.
9. Adaptive state for one student does not affect another student.
10. Abandoned and incomplete quizzes do not affect adaptive state.
11. Duplicate submission does not apply an adjustment twice.
12. Variable-length quizzes use percentage-based thresholds correctly.
13. The adjusted difficulty reaches the actual quiz-generation request.
14. Manual difficulty override preserves existing quiz setup behavior when provided.
"""

import os
import json
import sqlite3
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_quiz_session,
    get_quiz_session,
    get_topic_mastery,
    save_quiz_attempt,
    save_student_answers,
    submit_quiz_atomic,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)
from services.adaptive import (
    compute_adaptive_decision,
    calculate_adaptive_progression,
    increase_level,
    decrease_level,
    normalize_level
)
from services.auth_utils import auth_header_for

SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i+1}: What is the primary characteristic of this concept?",
        "options": {
            "A": "Option A (Correct)",
            "B": "Option B (Incorrect)",
            "C": "Option C (Incorrect)",
            "D": "Option D (Incorrect)"
        },
        "correct_answer": "A",
        "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
        "concept": "Core Concepts",
        "explanation": f"Explanation for question {i+1}"
    }
    for i in range(20)
]


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    db_file = tmp_path / "stepwise_adaptive_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()
    client = TestClient(app)
    return client, db_file


def create_and_submit_quiz(
    client: TestClient,
    db_file,
    student_id: str,
    course_code: str,
    topic_name: str,
    score: int,
    total: int = 10,
    session_id: str = "sess_adaptive",
    initial_difficulty: str = "mixed"
):
    """Helper to create an active session, submit answers to achieve exact score/total, and commit atomic grading."""
    conn = get_connection(db_file)
    get_or_create_student(student_id, conn=conn)

    questions = SAMPLE_QUESTIONS[:total]
    create_quiz_session(
        session_id=session_id,
        student_id=student_id,
        course_code=course_code,
        course_name="Programming in C",
        topic_name=topic_name,
        questions=questions,
        recommended_difficulty=initial_difficulty,
        conn=conn
    )
    conn.commit()
    conn.close()

    # Build answers: first `score` answers correct ('A'), remainder incorrect ('B')
    answers = ["A" if i < score else "B" for i in range(total)]

    submit_res = client.post(
        "/api/quiz/submit",
        json={
            "quiz_id": session_id,
            "answers": answers,
            "student_id": student_id
        },
        headers=auth_header_for(student_id)
    )
    assert submit_res.status_code == 200, f"Submit failed: {submit_res.text}"
    return submit_res.json()


# =============================================================================
# 1. High Score Progression (Rule A)
# =============================================================================
class TestStepwiseRuleA_HighScores:
    def test_first_attempt_scores_8_of_10_increases_difficulty_by_one_level(self, isolated_db_client):
        """Case 1: First attempt scores 8/10: next quiz difficulty increases by one level."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_a_1"

        # Attempt 1: Started at Foundational (Easy), scores 8/10
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Pointers and Memory",
            score=8,
            total=10,
            session_id="sess_a1",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Pointers and Memory", conn=conn)
        mastery = get_topic_mastery(student_id, "22CS101", "Pointers and Memory", conn=conn)
        conn.close()

        assert decision["is_decision_active"] is True
        assert decision["previous_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert mastery["current_level"] == ADAPTIVE_LEVEL_MIXED
        assert "Promoted from Foundational to Mixed" in decision["reason"]

    def test_second_consecutive_high_score_increases_difficulty_by_another_level(self, isolated_db_client):
        """Case 2: A second consecutive high score increases it by another level (Foundational -> Mixed -> Advanced)."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_a_2"

        # Attempt 1: 8/10 at Foundational -> promotes to Mixed
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Dynamic Memory",
            score=8,
            total=10,
            session_id="sess_a2_1",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )

        # Attempt 2: 8/10 at Mixed -> promotes to Advanced
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Dynamic Memory",
            score=8,
            total=10,
            session_id="sess_a2_2",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Dynamic Memory", conn=conn)
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert "Promoted from Mixed to Advanced" in decision["reason"]

    def test_difficulty_ceiling_at_advanced(self, isolated_db_client):
        """Rule A: Difficulty never exceeds the application ceiling (Advanced / Hard)."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_a_ceil"

        # Attempt at Advanced scores 10/10
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Bitwise Operations",
            score=10,
            total=10,
            session_id="sess_ceil_1",
            initial_difficulty=ADAPTIVE_LEVEL_ADVANCED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Bitwise Operations", conn=conn)
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_ADVANCED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert "ceiling" in decision["reason"].lower()


# =============================================================================
# 2. Low Scores and Consecutive Requirements (Rule B)
# =============================================================================
class TestStepwiseRuleB_LowScores:
    def test_first_attempt_scores_4_of_10_does_not_immediately_decrease(self, isolated_db_client):
        """Case 3: First attempt scores 4/10: difficulty does not immediately decrease."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_b_1"

        # Attempt 1: 4/10 (< 50%) at Mixed
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Structures and Unions",
            score=4,
            total=10,
            session_id="sess_b1_1",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Structures and Unions", conn=conn)
        conn.close()

        # Must NOT decrease difficulty based on single isolated low score
        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["consecutive_low_streak"] == 1
        assert "Single low score recorded" in decision["reason"]

    def test_two_consecutive_scores_below_5_of_10_decreases_difficulty_by_one_level(self, isolated_db_client):
        """Case 4: Two consecutive scores below 5/10: next quiz difficulty decreases by one level."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_b_2"

        # Attempt 1: 4/10 at Advanced -> streak = 1, stays Advanced
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="File IO",
            score=4,
            total=10,
            session_id="sess_b2_1",
            initial_difficulty=ADAPTIVE_LEVEL_ADVANCED
        )

        # Attempt 2: 3/10 at Advanced -> streak = 2 -> demotes to Mixed
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="File IO",
            score=3,
            total=10,
            session_id="sess_b2_2",
            initial_difficulty=ADAPTIVE_LEVEL_ADVANCED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "File IO", conn=conn)
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_ADVANCED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["consecutive_low_streak"] == 0  # reset upon demotion
        assert "Demoted from Advanced to Mixed" in decision["reason"]

    def test_subsequent_attempts_continue_to_decrease_every_two_low_scores(self, isolated_db_client):
        """Rule B: Continues decreasing by one level after every two consecutive low attempts, down to floor."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_b_multi"

        # 4 consecutive low scores from Advanced:
        # Attempt 1: 4/10 -> streak = 1 (Advanced)
        # Attempt 2: 3/10 -> streak = 2 -> demotes to Mixed (streak resets to 0)
        # Attempt 3: 4/10 -> streak = 1 (Mixed)
        # Attempt 4: 2/10 -> streak = 2 -> demotes to Foundational (streak resets to 0)
        for i, score in enumerate([4, 3, 4, 2]):
            create_and_submit_quiz(
                client=client,
                db_file=db_file,
                student_id=student_id,
                course_code="22CS101",
                topic_name="Linked Lists in C",
                score=score,
                total=10,
                session_id=f"sess_b_seq_{i+1}",
                initial_difficulty=ADAPTIVE_LEVEL_ADVANCED
            )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Linked Lists in C", conn=conn)
        conn.close()

        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL

    def test_demotion_floor_at_foundational(self, isolated_db_client):
        """Rule B: Difficulty never falls below the application floor (Foundational / Easy)."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_b_floor"

        # Two consecutive low scores on Foundational: remains Foundational
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Arrays and Strings",
            score=1,
            total=10,
            session_id="sess_fl_1",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Arrays and Strings",
            score=2,
            total=10,
            session_id="sess_fl_2",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Arrays and Strings", conn=conn)
        conn.close()

        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert "baseline" in decision["reason"].lower() or "floor" in decision["reason"].lower()


# =============================================================================
# 3. Benchmark (Rule C) and Recovery Resets (Rule D)
# =============================================================================
class TestStepwiseRuleC_And_D_BenchmarkAndRecovery:
    def test_scores_4_7_3_do_not_trigger_low_streak_decrease(self, isolated_db_client):
        """Case 5: Scores 4/10, 7/10, 3/10 do not trigger a low-streak decrease."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_d_recovery"

        # Attempt 1: 4/10 -> streak = 1 (Mixed)
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Recursion in C",
            score=4,
            total=10,
            session_id="sess_rec_1",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        # Attempt 2: 7/10 -> Rule A: promotes to Advanced! Resets streak to 0!
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Recursion in C",
            score=7,
            total=10,
            session_id="sess_rec_2",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        # Attempt 3: 3/10 -> streak = 1 (< 2, no decrease!)
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Recursion in C",
            score=3,
            total=10,
            session_id="sess_rec_3",
            initial_difficulty=ADAPTIVE_LEVEL_ADVANCED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Recursion in C", conn=conn)
        conn.close()

        # Level must remain Advanced because 7/10 reset the low streak!
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert decision["consecutive_low_streak"] == 1

    def test_score_of_exactly_5_of_10_neither_increases_nor_counts_as_low(self, isolated_db_client):
        """Case 6: A score of exactly 5/10 does not trigger an increase or count as a low score."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_c_benchmark"

        # Initial baseline attempt: 5/10 (50%)
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Macros and Preprocessor",
            score=5,
            total=10,
            session_id="sess_bm_1",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Macros and Preprocessor", conn=conn)
        conn.close()

        # Score of 5/10 does not increase difficulty
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["consecutive_low_streak"] == 0

    def test_score_of_5_of_10_resets_consecutive_low_streak(self, isolated_db_client):
        """Rule C: A score of exactly 5/10 resets the consecutive low streak after a low score."""
        client, db_file = isolated_db_client
        student_id = "stud_rule_c_reset"

        # Attempt 1: 4/10 -> streak = 1
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Conditional Compilation",
            score=4,
            total=10,
            session_id="sess_bm_res_1",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        # Attempt 2: 5/10 -> benchmark resets streak to 0
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Conditional Compilation",
            score=5,
            total=10,
            session_id="sess_bm_res_2",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        # Attempt 3: 3/10 -> streak = 1 (does NOT trigger demotion!)
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Conditional Compilation",
            score=3,
            total=10,
            session_id="sess_bm_res_3",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Conditional Compilation", conn=conn)
        conn.close()

        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["consecutive_low_streak"] == 1


# =============================================================================
# 4. Student & Topic Isolation and Boundaries
# =============================================================================
class TestStepwiseIsolationAndBoundaries:
    def test_difficulty_never_exceeds_maximum_or_falls_below_minimum(self):
        """Case 7: Difficulty level progression functions strictly respect boundaries."""
        # Top boundary
        assert increase_level(ADAPTIVE_LEVEL_ADVANCED) == ADAPTIVE_LEVEL_ADVANCED
        assert increase_level("hard") == ADAPTIVE_LEVEL_ADVANCED

        # Bottom boundary
        assert decrease_level(ADAPTIVE_LEVEL_FOUNDATIONAL) == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decrease_level("easy") == ADAPTIVE_LEVEL_FOUNDATIONAL

        # Normal transitions
        assert increase_level(ADAPTIVE_LEVEL_FOUNDATIONAL) == ADAPTIVE_LEVEL_MIXED
        assert increase_level(ADAPTIVE_LEVEL_MIXED) == ADAPTIVE_LEVEL_ADVANCED
        assert decrease_level(ADAPTIVE_LEVEL_ADVANCED) == ADAPTIVE_LEVEL_MIXED
        assert decrease_level(ADAPTIVE_LEVEL_MIXED) == ADAPTIVE_LEVEL_FOUNDATIONAL

    def test_adaptive_state_for_one_topic_does_not_affect_another_topic(self, isolated_db_client):
        """Case 8: Adaptive state for one topic does not affect another topic."""
        client, db_file = isolated_db_client
        student_id = "stud_topic_iso"

        # Topic 1: Pointers -> scores 10/10 -> promotes to Advanced
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Pointers",
            score=10,
            total=10,
            session_id="sess_top_1",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        dec_topic1 = compute_adaptive_decision(student_id, "22CS101", "Pointers", conn=conn)
        # Topic 2: File IO has 0 attempts -> must be at baseline (Mixed)
        dec_topic2 = compute_adaptive_decision(student_id, "22CS101", "File IO", conn=conn)
        conn.close()

        assert dec_topic1["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert dec_topic2["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert dec_topic2["is_decision_active"] is False

    def test_adaptive_state_for_one_student_does_not_affect_another_student(self, isolated_db_client):
        """Case 9: Adaptive state for one student does not affect another student."""
        client, db_file = isolated_db_client
        student_a = "stud_isolation_a"
        student_b = "stud_isolation_b"

        # Student A scores 10/10 -> Advanced
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_a,
            course_code="22CS101",
            topic_name="Structures",
            score=10,
            total=10,
            session_id="sess_stud_a",
            initial_difficulty=ADAPTIVE_LEVEL_MIXED
        )

        conn = get_connection(db_file)
        dec_a = compute_adaptive_decision(student_a, "22CS101", "Structures", conn=conn)
        # Student B has 0 attempts on Structures -> must be at baseline (Mixed)
        dec_b = compute_adaptive_decision(student_b, "22CS101", "Structures", conn=conn)
        conn.close()

        assert dec_a["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert dec_b["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert dec_b["is_decision_active"] is False


# =============================================================================
# 5. Submission Integrity & Variable Length Normalization
# =============================================================================
class TestStepwiseSubmissionIntegrity:
    def test_abandoned_and_incomplete_quizzes_do_not_affect_adaptive_state(self, isolated_db_client):
        """Case 10: Abandoned and incomplete quizzes do not affect adaptive state."""
        client, db_file = isolated_db_client
        student_id = "stud_abandoned"

        # Create session but DO NOT submit it
        conn = get_connection(db_file)
        get_or_create_student(student_id, conn=conn)
        create_quiz_session(
            session_id="sess_incom_1",
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Uncompleted Topic",
            questions=SAMPLE_QUESTIONS[:10],
            recommended_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL,
            conn=conn
        )
        conn.commit()

        # Decision must remain inactive and at default baseline
        decision = compute_adaptive_decision(student_id, "22CS101", "Uncompleted Topic", conn=conn)
        conn.close()

        assert decision["is_decision_active"] is False
        assert decision["attempt_count"] == 0

    def test_duplicate_submission_does_not_apply_adjustment_twice(self, isolated_db_client):
        """Case 11: Duplicate submission does not apply an adjustment twice."""
        client, db_file = isolated_db_client
        student_id = "stud_dup_submission"

        # Attempt 1 at Foundational scores 10/10 -> promotes to Mixed
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22CS101",
            topic_name="Idempotency Topic",
            score=10,
            total=10,
            session_id="sess_dup_1",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )

        # Resubmit the exact same completed session with answers
        resubmit_res = client.post(
            "/api/quiz/submit",
            json={
                "quiz_id": "sess_dup_1",
                "answers": ["A"] * 10,
                "student_id": student_id
            },
            headers=auth_header_for(student_id)
        )
        assert resubmit_res.status_code == 200

        conn = get_connection(db_file)
        decision = compute_adaptive_decision(student_id, "22CS101", "Idempotency Topic", conn=conn)
        mastery = get_topic_mastery(student_id, "22CS101", "Idempotency Topic", conn=conn)
        conn.close()

        # Difficulty must still be Mixed, NOT promoted again to Advanced
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert mastery["current_level"] == ADAPTIVE_LEVEL_MIXED
        assert mastery["attempt_count"] == 1

    def test_variable_length_quizzes_use_percentage_based_thresholds_correctly(self):
        """Case 12: Variable-length quizzes use percentage-based thresholds correctly."""
        # 5-question quiz: 4/5 = 80.0% (> 50%) -> promotes
        prog_5q_high = calculate_adaptive_progression(
            attempts=[{"score": 4, "total": 5, "recommended_difficulty": "foundational"}],
            initial_level="foundational"
        )
        assert prog_5q_high["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED

        # 5-question quiz: two consecutive low scores (2/5 = 40% and 1/5 = 20%) -> demotes
        prog_5q_low = calculate_adaptive_progression(
            attempts=[
                {"score": 2, "total": 5, "recommended_difficulty": "advanced"},
                {"score": 1, "total": 5, "recommended_difficulty": "advanced"}
            ],
            initial_level="advanced"
        )
        assert prog_5q_low["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED

        # 20-question quiz: 10/20 = 50.0% benchmark -> maintains and resets streak
        prog_20q_bm = calculate_adaptive_progression(
            attempts=[
                {"score": 8, "total": 20, "recommended_difficulty": "mixed"},  # 40% < 50%
                {"score": 10, "total": 20, "recommended_difficulty": "mixed"} # 50% == 50%
            ],
            initial_level="mixed"
        )
        assert prog_20q_bm["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert prog_20q_bm["consecutive_low_streak"] == 0


# =============================================================================
# 6. End-to-End Next Quiz Generation Flow & Manual Overrides
# =============================================================================
DISTINCT_QUESTIONS = [
    {
        "question": "What is the primary function of a McCulloch-Pitts neuron?",
        "options": {"A": "Linear threshold gate", "B": "Continuous feedback", "C": "Stochastic sampling", "D": "Memory store"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Biological Inspiration",
        "explanation": "It functions as a linear threshold gate."
    },
    {
        "question": "Which researcher introduced the perceptron learning algorithm in 1958?",
        "options": {"A": "Frank Rosenblatt", "B": "Alan Turing", "C": "John von Neumann", "D": "Claude Shannon"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Perceptron Origins",
        "explanation": "Frank Rosenblatt introduced the Perceptron."
    },
    {
        "question": "What mathematical proof shows perceptrons converge on linearly separable data?",
        "options": {"A": "Perceptron Convergence Theorem", "B": "Universal Approximation", "C": "Central Limit", "D": "No Free Lunch"},
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Convergence",
        "explanation": "The Perceptron Convergence Theorem guarantees convergence."
    },
    {
        "question": "Which boolean function cannot be solved by a single-layer perceptron?",
        "options": {"A": "XOR function", "B": "AND function", "C": "OR function", "D": "NOT function"},
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Linear Separability",
        "explanation": "XOR is not linearly separable."
    },
    {
        "question": "What activation function replaced step functions to enable backpropagation?",
        "options": {"A": "Sigmoid function", "B": "Step function", "C": "Dirac delta", "D": "Heaviside step"},
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Activation Functions",
        "explanation": "Sigmoid provides smooth differentiability."
    },
    {
        "question": "What is the derivative of the standard logistic sigmoid function sigma(x)?",
        "options": {"A": "sigma(x) * (1 - sigma(x))", "B": "1 - sigma(x)", "C": "sigma(x)^2", "D": "exp(-x)"},
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Derivatives",
        "explanation": "The derivative is sigma(x)*(1-sigma(x))."
    },
    {
        "question": "Which historic book by Minsky and Papert highlighted perceptron limitations?",
        "options": {"A": "Perceptrons (1969)", "B": "Cybernetics", "C": "Deep Learning", "D": "Neural Networks"},
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "AI Winter",
        "explanation": "The 1969 book Perceptrons highlighted limitations."
    },
    {
        "question": "In what decade did backpropagation popularize multilayer perceptrons?",
        "options": {"A": "1980s", "B": "1950s", "C": "1960s", "D": "2010s"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Multilayer Perceptrons",
        "explanation": "Rumelhart, Hinton, and Williams published in 1986."
    },
    {
        "question": "What is the primary architectural difference between an MLP and a single perceptron?",
        "options": {"A": "Hidden layers and non-linearities", "B": "Binary weights", "C": "No weights", "D": "Single input only"},
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Layer Architecture",
        "explanation": "MLPs have hidden layers with non-linear activations."
    },
    {
        "question": "What breakthrough in 2012 accelerated modern deep learning on ImageNet?",
        "options": {"A": "AlexNet using GPUs", "B": "Support Vector Machines", "C": "Decision Trees", "D": "K-Means"},
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Deep Learning Era",
        "explanation": "AlexNet won ImageNet in 2012."
    }
]


class DummyTestProvider:
    name = "dummy_provider"

    def is_configured(self):
        return True

    def generate_quiz_raw(self, *args, **kwargs):
        return json.dumps({
            "questions": DISTINCT_QUESTIONS
        })


class TestStepwiseEndToEndNextQuizFlow:
    @patch("services.llm_service._get_providers", return_value=[DummyTestProvider()])
    def test_adjusted_difficulty_reaches_actual_quiz_generation_request(self, mock_providers, isolated_db_client):
        """Case 13: The adjusted difficulty reaches the actual quiz-generation request."""
        client, db_file = isolated_db_client
        student_id = "stud_e2e_adaptive"

        # Step 1: Submit first quiz with high score (10/10) on Foundational
        create_and_submit_quiz(
            client=client,
            db_file=db_file,
            student_id=student_id,
            course_code="22AM301",
            topic_name="History of Deep Learning, What Is Deep Learning",
            score=10,
            total=10,
            session_id="sess_e2e_1",
            initial_difficulty=ADAPTIVE_LEVEL_FOUNDATIONAL
        )

        # Step 2: Request next quiz for the same topic via API
        gen_res = client.post(
            "/api/quiz/generate",
            json={
                "course_code": "22AM301",
                "topic": "History of Deep Learning, What Is Deep Learning",
                "student_id": student_id
            },
            headers=auth_header_for(student_id)
        )
        assert gen_res.status_code == 200
        gen_data = gen_res.json()

        # The newly generated quiz session MUST reflect the promoted difficulty (Mixed)!
        assert gen_data["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert gen_data["adaptive_applied"] is True

        # Verify persisted quiz_sessions row
        session_id = gen_data["quiz_id"]
        conn = get_connection(db_file)
        session = get_quiz_session(session_id, student_id=student_id, conn=conn)
        conn.close()

        assert session is not None
        assert session["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED

    @patch("services.llm_service._get_providers", return_value=[DummyTestProvider()])
    def test_manual_difficulty_override_preserves_setup_flow(self, mock_providers, isolated_db_client):
        """Case 14: Manual difficulty selection overrides adaptive mode when explicitly requested."""
        client, db_file = isolated_db_client
        student_id = "stud_manual_override"

        # Generate quiz with explicit difficulty="hard"
        gen_res = client.post(
            "/api/quiz/generate",
            json={
                "course_code": "22AM301",
                "topic": "History of Deep Learning, What Is Deep Learning",
                "student_id": student_id,
                "difficulty": "hard"
            },
            headers=auth_header_for(student_id)
        )
        assert gen_res.status_code == 200
        gen_data = gen_res.json()

        # Manual selection takes precedence
        assert gen_data["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert gen_data["adaptive_applied"] is False
