import os
import tempfile
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta

from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_quiz_session,
    get_quiz_session,
    complete_quiz_session,
    save_student_answers,
    get_student_answers_for_session,
    get_student_answers_for_attempt,
    get_topic_mastery,
    upsert_topic_mastery,
    compute_next_adaptive_level,
    backfill_topic_mastery_from_attempts,
    get_stored_questions_for_topic,
    save_quiz_attempt,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary isolated SQLite database for testing."""
    db_file = tmp_path / "test_learnai.db"
    init_db(db_file)
    return db_file


class TestDatabaseMigrationAndSchema:
    def test_init_db_idempotency_preserves_tables(self, temp_db):
        """Calling init_db multiple times must not drop tables or corrupt data."""
        # Insert a student
        conn = get_connection(temp_db)
        conn.execute("INSERT INTO students (id, created_at, last_active) VALUES ('s1', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

        # Re-initialize
        init_db(temp_db)

        # Verify tables and student remain intact
        conn = get_connection(temp_db)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "students" in tables
        assert "quiz_attempts" in tables
        assert "question_metadata" in tables
        assert "quiz_sessions" in tables
        assert "student_answers" in tables
        assert "topic_mastery" in tables

        student = conn.execute("SELECT * FROM students WHERE id = 's1'").fetchone()
        assert student is not None
        assert student["id"] == "s1"
        conn.close()

    def test_foreign_keys_enforced(self, temp_db):
        """Foreign key constraint must be active and prevent orphan records."""
        conn = get_connection(temp_db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO quiz_sessions (
                    id, student_id, course_code, course_name, topic_name,
                    questions_snapshot, created_at, expires_at
                ) VALUES ('sess_orphan', 'non_existent_student', '22AM301', 'DL', 'Topic', '[]', '2026-01-01', '2026-01-02')
            """)
            conn.commit()
        conn.close()


class TestQuizSessionLifecycle:
    def test_create_and_retrieve_quiz_session(self, temp_db):
        """Verify quiz session creation, default 24h expiration, and questions snapshot retrieval."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_1", conn=conn)

        sample_questions = [
            {
                "question": "What is an activation function?",
                "options": {"A": "Linear", "B": "Non-linear transform", "C": "Loss", "D": "Optimizer"},
                "correct_answer": "B",
                "difficulty": "Easy",
                "concept": "Activation"
            }
        ]

        sess = create_quiz_session(
            session_id="sess_101",
            student_id="stud_1",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Activation Functions",
            questions=sample_questions,
            recommended_difficulty="mixed",
            adaptive_metadata={"reason": "initial diagnostic"},
            conn=conn
        )

        assert sess["id"] == "sess_101"
        assert sess["status"] == "in_progress"
        assert sess["questions"] == sample_questions

        # Fetch session
        fetched = get_quiz_session("sess_101", student_id="stud_1", conn=conn)
        assert fetched is not None
        assert fetched["id"] == "sess_101"
        assert fetched["status"] == "in_progress"
        assert len(fetched["questions"]) == 1
        assert fetched["questions"][0]["correct_answer"] == "B"
        assert fetched["adaptive_metadata"] == {"reason": "initial diagnostic"}
        conn.close()

    def test_session_ownership_validation(self, temp_db):
        """Attempting to access a session belonging to another student must raise PermissionError."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_alice", conn=conn)
        get_or_create_student("stud_bob", conn=conn)

        create_quiz_session(
            session_id="sess_alice",
            student_id="stud_alice",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=[],
            conn=conn
        )

        # Alice can access her own session
        sess = get_quiz_session("sess_alice", student_id="stud_alice", conn=conn)
        assert sess is not None

        # Bob accessing Alice's session must raise PermissionError
        with pytest.raises(PermissionError) as exc_info:
            get_quiz_session("sess_alice", student_id="stud_bob", conn=conn)
        assert "does not belong to student stud_bob" in str(exc_info.value)
        conn.close()

    def test_session_expiration_validation(self, temp_db):
        """Expired sessions must auto-transition to 'expired' and reject completion."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_exp", conn=conn)

        # Create session with duration -10 seconds (already expired)
        create_quiz_session(
            session_id="sess_expired",
            student_id="stud_exp",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="CNNs",
            questions=[],
            duration_seconds=-10,
            conn=conn
        )

        # Retrieval triggers expiration check
        sess = get_quiz_session("sess_expired", student_id="stud_exp", auto_expire=True, conn=conn)
        assert sess is not None
        assert sess["status"] == "expired"

        # Attempting to complete an expired session must raise ValueError
        with pytest.raises(ValueError) as exc_info:
            complete_quiz_session("sess_expired", student_id="stud_exp", conn=conn)
        assert "has expired" in str(exc_info.value)
        conn.close()

    def test_session_completion_idempotency(self, temp_db):
        """Completing a session twice must raise ValueError to prevent duplicate submissions."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_idemp", conn=conn)

        create_quiz_session(
            session_id="sess_idemp",
            student_id="stud_idemp",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="RNNs",
            questions=[],
            conn=conn
        )

        # First completion succeeds
        completed = complete_quiz_session("sess_idemp", student_id="stud_idemp", conn=conn)
        assert completed["status"] == "completed"
        assert completed["submitted_at"] is not None

        # Second completion must fail
        with pytest.raises(ValueError) as exc_info:
            complete_quiz_session("sess_idemp", student_id="stud_idemp", conn=conn)
        assert "already been submitted" in str(exc_info.value)
        conn.close()


class TestGranularStudentAnswers:
    def test_save_and_retrieve_student_answers(self, temp_db):
        """Verify per-question student answers are persisted with all attributes."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_ans", conn=conn)

        create_quiz_session("sess_ans", "stud_ans", "22AM301", "DL", "Optimization", [], conn=conn)

        # Create dummy quiz_attempt to satisfy foreign key
        conn.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts, created_at
            ) VALUES ('att_ans', 'stud_ans', '22AM301', 'DL', 'Optimization', '{}', 1, 2, 50.0, 'Developing', '{}', '{}', '[]', '[]', '2026-01-01')
        """)
        conn.commit()

        details = [
            {
                "question": "What is SGD?",
                "selected_answer": "B",
                "correct_answer": "B",
                "is_correct": True,
                "difficulty": "Easy",
                "concept": "SGD",
                "explanation": "Stochastic Gradient Descent"
            },
            {
                "question": "What causes vanishing gradients?",
                "selected_answer": "A",
                "correct_answer": "C",
                "is_correct": False,
                "difficulty": "Hard",
                "concept": "Gradients",
                "explanation": "Deep networks with sigmoid"
            }
        ]

        count = save_student_answers("sess_ans", "att_ans", "stud_ans", details, conn=conn)
        assert count == 2

        answers = get_student_answers_for_session("sess_ans", conn=conn)
        assert len(answers) == 2
        assert answers[0]["question_index"] == 0
        assert answers[0]["selected_answer"] == "B"
        assert answers[0]["is_correct"] == 1
        assert answers[1]["question_index"] == 1
        assert answers[1]["selected_answer"] == "A"
        assert answers[1]["is_correct"] == 0
        conn.close()

    def test_duplicate_question_index_rejected(self, temp_db):
        """UNIQUE(session_id, question_index) must prevent duplicate answers for the same question."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_dup", conn=conn)
        create_quiz_session("sess_dup", "stud_dup", "22AM301", "DL", "Topic", [], conn=conn)

        conn.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts, created_at
            ) VALUES ('att_dup', 'stud_dup', '22AM301', 'DL', 'Topic', '{}', 1, 1, 100.0, 'Good', '{}', '{}', '[]', '[]', '2026-01-01')
        """)
        conn.commit()

        details = [{"question": "Q1", "selected_answer": "A", "correct_answer": "A", "is_correct": True}]
        save_student_answers("sess_dup", "att_dup", "stud_dup", details, conn=conn)

        # Attempt to insert same question index again must trigger IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            save_student_answers("sess_dup", "att_dup", "stud_dup", details, conn=conn)
        conn.close()


class TestAdaptiveTopicMastery:
    def test_insufficient_history_retains_current_level(self):
        """With fewer than 5 questions, level must NOT change even with 100% or 0% score."""
        # 4 questions with 100% accuracy remains 'mixed'
        next_lvl = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_MIXED,
            total_questions=4,
            mastery_score=1.0,
            min_questions=5
        )
        assert next_lvl == ADAPTIVE_LEVEL_MIXED

        # 2 questions with 0% accuracy remains 'mixed'
        next_lvl = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_MIXED,
            total_questions=2,
            mastery_score=0.0,
            min_questions=5
        )
        assert next_lvl == ADAPTIVE_LEVEL_MIXED

    def test_step_wise_promotion_rules(self):
        """Step-wise promotion moves at most one level: foundational -> mixed -> advanced."""
        # foundational -> mixed on >= 80% accuracy
        lvl1 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            total_questions=10,
            mastery_score=0.90
        )
        assert lvl1 == ADAPTIVE_LEVEL_MIXED

        # mixed -> advanced on >= 80% accuracy
        lvl2 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_MIXED,
            total_questions=10,
            mastery_score=0.85
        )
        assert lvl2 == ADAPTIVE_LEVEL_ADVANCED

        # advanced -> advanced (ceiling)
        lvl3 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_ADVANCED,
            total_questions=10,
            mastery_score=0.95
        )
        assert lvl3 == ADAPTIVE_LEVEL_ADVANCED

    def test_step_wise_demotion_rules(self):
        """Step-wise demotion moves at most one level: advanced -> mixed -> foundational."""
        # advanced -> mixed on <= 40% accuracy
        lvl1 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_ADVANCED,
            total_questions=10,
            mastery_score=0.30
        )
        assert lvl1 == ADAPTIVE_LEVEL_MIXED

        # mixed -> foundational on <= 40% accuracy
        lvl2 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_MIXED,
            total_questions=10,
            mastery_score=0.40  # boundary case: exactly 0.40 demotes
        )
        assert lvl2 == ADAPTIVE_LEVEL_FOUNDATIONAL

        # foundational -> foundational (floor)
        lvl3 = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            total_questions=10,
            mastery_score=0.20
        )
        assert lvl3 == ADAPTIVE_LEVEL_FOUNDATIONAL

    def test_no_direct_jumps_between_foundational_and_advanced(self):
        """Foundational must never jump directly to Advanced, and Advanced must never jump directly to Foundational."""
        # Perfect 100% on foundational must only promote to mixed, not advanced
        prom = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            total_questions=20,
            mastery_score=1.0
        )
        assert prom == ADAPTIVE_LEVEL_MIXED

        # 0% on advanced must only demote to mixed, not foundational
        demo = compute_next_adaptive_level(
            current_level=ADAPTIVE_LEVEL_ADVANCED,
            total_questions=20,
            mastery_score=0.0
        )
        assert demo == ADAPTIVE_LEVEL_MIXED

    def test_intermediate_accuracy_maintains_level(self):
        """Scores between demotion (0.40) and promotion (0.80) thresholds maintain current level."""
        for lvl in [ADAPTIVE_LEVEL_FOUNDATIONAL, ADAPTIVE_LEVEL_MIXED, ADAPTIVE_LEVEL_ADVANCED]:
            res = compute_next_adaptive_level(
                current_level=lvl,
                total_questions=10,
                mastery_score=0.60
            )
            assert res == lvl

    def test_topic_and_student_isolation(self, temp_db):
        """Mastery for Student A on Topic 1 must not alter Student B or Topic 2."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_A", conn=conn)
        get_or_create_student("stud_B", conn=conn)

        # Student A practices Topic 1 with high score
        upsert_topic_mastery(
            student_id="stud_A",
            course_code="22AM301",
            topic_name="Topic 1",
            score=9,
            total=10,
            weak_concepts=[],
            strong_concepts=["C1"],
            conn=conn
        )

        # Student A practices Topic 2 with low score
        upsert_topic_mastery(
            student_id="stud_A",
            course_code="22AM301",
            topic_name="Topic 2",
            score=2,
            total=10,
            weak_concepts=["C2"],
            strong_concepts=[],
            conn=conn
        )

        m_a1 = get_topic_mastery("stud_A", "22AM301", "Topic 1", conn=conn)
        m_a2 = get_topic_mastery("stud_A", "22AM301", "Topic 2", conn=conn)
        m_b1 = get_topic_mastery("stud_B", "22AM301", "Topic 1", conn=conn)

        assert m_a1["mastery_score"] == 0.90
        assert m_a1["current_level"] == ADAPTIVE_LEVEL_ADVANCED

        assert m_a2["mastery_score"] == 0.20
        assert m_a2["current_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL

        # Student B has no record on Topic 1
        assert m_b1 is None
        conn.close()


class TestStoredQuestionRetrieval:
    def test_get_stored_questions_topic_isolation_and_validation(self, temp_db):
        """Ensure stored questions query never leaks questions across topics and excludes duplicates."""
        conn = get_connection(temp_db)
        get_or_create_student("stud_q", conn=conn)

        quiz_topic_1 = {
            "questions": [
                {
                    "question": "What is a Perceptron?",
                    "options": {"A": "Classifier", "B": "Database", "C": "Router", "D": "Cable"},
                    "correct_answer": "A",
                    "difficulty": "Easy",
                    "concept": "Perceptron",
                    "explanation": "Binary classifier"
                },
                {
                    "question": "What is XOR problem?",
                    "options": {"A": "Linear", "B": "Non-linear separability", "C": "Loss", "D": "Rate"},
                    "correct_answer": "B",
                    "difficulty": "Medium",
                    "concept": "XOR",
                    "explanation": "Minsky & Papert"
                }
            ]
        }

        quiz_topic_2 = {
            "questions": [
                {
                    "question": "What is Hough Transform?",
                    "options": {"A": "Feature extraction", "B": "Sound wave", "C": "Tree", "D": "Sort"},
                    "correct_answer": "A",
                    "difficulty": "Medium",
                    "concept": "Transform",
                    "explanation": "Line detection"
                }
            ]
        }

        # Save attempts for topic 1 and topic 2
        save_quiz_attempt(
            student_id="stud_q",
            course_code="22AM301",
            course_name="DL",
            topic_name="Perceptron Basics",
            quiz_data=quiz_topic_1,
            score=2, total=2, percentage=100.0, status="Excellent",
            difficulty_accuracy={}, concept_accuracy={},
            weak_concepts=[], strong_concepts=[],
            conn=conn
        )

        save_quiz_attempt(
            student_id="stud_q",
            course_code="22AM803",
            course_name="CV",
            topic_name="Edge Detection",
            quiz_data=quiz_topic_2,
            score=1, total=1, percentage=100.0, status="Good",
            difficulty_accuracy={}, concept_accuracy={},
            weak_concepts=[], strong_concepts=[],
            conn=conn
        )

        # Query topic 1 questions
        t1_questions = get_stored_questions_for_topic("22AM301", "Perceptron Basics", conn=conn)
        assert len(t1_questions) == 2
        assert all("Hough" not in q["question"] for q in t1_questions)

        # Query with difficulty filter
        easy_only = get_stored_questions_for_topic("22AM301", "Perceptron Basics", difficulty="Easy", conn=conn)
        assert len(easy_only) == 1
        assert easy_only[0]["question"] == "What is a Perceptron?"

        # Query with fingerprint exclusion
        excluded_fp = ["what is a perceptron"]
        novel_only = get_stored_questions_for_topic(
            "22AM301", "Perceptron Basics", exclude_fingerprints=excluded_fp, conn=conn
        )
        assert len(novel_only) == 1
        assert novel_only[0]["question"] == "What is XOR problem?"
        conn.close()
