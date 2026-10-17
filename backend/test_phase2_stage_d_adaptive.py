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
    get_quiz_session,
    get_topic_mastery,
    upsert_topic_mastery,
    save_student_answers,
    save_quiz_attempt,
    get_stored_questions_for_topic,
    submit_quiz_atomic,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)
from services.adaptive import (
    compute_adaptive_decision,
    get_recommended_difficulty,
    get_difficulty_distribution,
    get_adaptive_prompt_additions,
    get_adaptive_explanation,
    ADAPTIVE_MIN_QUESTIONS,
    PROMOTION_THRESHOLD,
    DEMOTION_THRESHOLD
)
from services.llm_service import generate_quiz, AIServiceUnavailableError


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
        "difficulty": "Easy" if i < 6 else ("Medium" if i < 9 else "Hard"),
        "concept": "Neuron Model",
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
    db_file = tmp_path / "stage_d_adaptive_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()
    client = TestClient(app)
    return client, db_file


def seed_history(
    conn: sqlite3.Connection,
    student_id: str,
    course_code: str,
    topic_name: str,
    total_q: int,
    correct_q: int,
    level: str = "mixed",
    session_id: str = "sess_seed",
    attempt_id: str = "att_seed"
):
    """Seed student, session, attempt, answers, and topic_mastery respecting foreign keys."""
    get_or_create_student(student_id, conn=conn)
    create_quiz_session(
        session_id=session_id,
        student_id=student_id,
        course_code=course_code,
        course_name="Deep Learning",
        topic_name=topic_name,
        questions=SAMPLE_QUESTIONS[:total_q],
        recommended_difficulty=level,
        conn=conn
    )
    quiz_data = {"subject": "Deep Learning", "topic": topic_name, "questions": SAMPLE_QUESTIONS[:total_q]}
    conn.execute("""
        INSERT INTO quiz_attempts (
            id, student_id, course_code, course_name, topic_name,
            quiz_data, score, total, percentage, status,
            difficulty_accuracy, concept_accuracy, weak_concepts, strong_concepts,
            recommended_difficulty, created_at, session_id
        ) VALUES (?, ?, ?, 'Deep Learning', ?, ?, ?, ?, ?, 'passed', '{}', '{}', '[]', '[]', ?, '2026-10-17T12:00:00Z', ?)
    """, (
        attempt_id, student_id, course_code, topic_name,
        json.dumps(quiz_data), correct_q, total_q, round((correct_q / total_q) * 100, 1),
        level, session_id
    ))
    answers = [
        {
            "id": i,
            "question": f"Question {i+1}",
            "selected_answer": "A",
            "correct_answer": "A" if i < correct_q else "B",
            "is_correct": 1 if i < correct_q else 0,
            "difficulty": "Easy" if i < 6 else "Medium",
            "concept": "Concept",
            "explanation": "test explanation"
        }
        for i in range(total_q)
    ]
    save_student_answers(session_id, attempt_id, student_id, answers, conn=conn)
    upsert_topic_mastery(
        student_id=student_id,
        course_code=course_code,
        topic_name=topic_name,
        score=correct_q,
        total=total_q,
        weak_concepts=[],
        strong_concepts=["Concept"],
        current_level=level,
        conn=conn
    )
    conn.execute("""
        UPDATE topic_mastery SET current_level = ?
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
    """, (level, student_id, course_code, topic_name))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Adaptive Decision Engine Unit Tests
# ─────────────────────────────────────────────────────────────────────────────
class TestAdaptiveDecisionEngine:
    def test_new_student_starts_at_mixed(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        
        decision = compute_adaptive_decision(
            student_id="new_student_1",
            course_code="22AM301",
            topic_name="McCulloch-Pitts Neuron",
            conn=conn
        )
        conn.close()

        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["is_decision_active"] is False
        assert decision["graded_answers_count"] == 0
        assert "New student or topic without practice history" in decision["reason"]
        assert decision["policy_version"] == "1.0-stepwise"

    def test_insufficient_history_retains_current_level(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_few_answers"

        # Established at Foundational, but only 3 answers submitted (100% correct)
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Perceptrons",
            total_q=3,
            correct_q=3,
            level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            session_id="sess_few",
            attempt_id="att_few"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Perceptrons",
            conn=conn
        )
        conn.close()

        # Must NOT promote to Mixed because 3 < 5 min questions
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decision["is_decision_active"] is False
        assert decision["graded_answers_count"] < ADAPTIVE_MIN_QUESTIONS
        assert "Insufficient practice history" in decision["reason"]

    def test_stepwise_promotion_foundational_to_mixed(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_promote_f_to_m"

        # 6 valid answers with 5 correct (83.3% accuracy >= 80%) starting from Foundational
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Activation Functions",
            total_q=6,
            correct_q=5,
            level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            session_id="sess_f_m",
            attempt_id="att_f_m"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Activation Functions",
            conn=conn
        )
        conn.close()

        assert decision["is_decision_active"] is True
        assert decision["previous_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["accuracy"] >= PROMOTION_THRESHOLD

    def test_stepwise_promotion_mixed_to_advanced(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_promote_m_to_a"

        # 10 questions with 9 correct (90% >= 80%) from Mixed
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Loss Functions",
            total_q=10,
            correct_q=9,
            level=ADAPTIVE_LEVEL_MIXED,
            session_id="sess_m_a",
            attempt_id="att_m_a"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Loss Functions",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert "Promoted from Mixed to Advanced" in decision["reason"]

    def test_promotion_ceiling_at_advanced(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_ceil_a"

        # Perfect 10/10 from Advanced remains Advanced
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Backprop",
            total_q=10,
            correct_q=10,
            level=ADAPTIVE_LEVEL_ADVANCED,
            session_id="sess_ceil",
            attempt_id="att_ceil"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Backprop",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_ADVANCED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED
        assert "Maintained Advanced difficulty at ceiling" in decision["reason"]

    def test_stepwise_demotion_advanced_to_mixed(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_demo_a_to_m"

        # 10 questions with 3 correct (30% <= 40%) from Advanced
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Optimizers",
            total_q=10,
            correct_q=3,
            level=ADAPTIVE_LEVEL_ADVANCED,
            session_id="sess_a_m",
            attempt_id="att_a_m"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Optimizers",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_ADVANCED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert "Demoted from Advanced to Mixed" in decision["reason"]

    def test_stepwise_demotion_mixed_to_foundational(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_demo_m_to_f"

        # 10 questions with 4 correct (40% exactly <= 40% threshold) from Mixed
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="Regularization",
            total_q=10,
            correct_q=4,
            level=ADAPTIVE_LEVEL_MIXED,
            session_id="sess_m_f",
            attempt_id="att_m_f"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="Regularization",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert "Demoted from Mixed to Foundational" in decision["reason"]

    def test_demotion_floor_at_foundational(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_floor_f"

        # 0% accuracy on Foundational remains Foundational
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="FloorTopic",
            total_q=10,
            correct_q=0,
            level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            session_id="sess_floor",
            attempt_id="att_floor"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="FloorTopic",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert "Maintained Foundational difficulty at baseline" in decision["reason"]

    def test_no_direct_jumps_between_foundational_and_advanced(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_no_jumps"

        # 100% on Foundational must only promote to Mixed, NEVER direct to Advanced
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="JumpTopic",
            total_q=10,
            correct_q=10,
            level=ADAPTIVE_LEVEL_FOUNDATIONAL,
            session_id="sess_nojmp",
            attempt_id="att_nojmp"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="JumpTopic",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] != ADAPTIVE_LEVEL_ADVANCED

    def test_intermediate_accuracy_maintains_level(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_intermediate"

        # 60% accuracy (between 40% and 80%) retains Mixed
        seed_history(
            conn=conn,
            student_id=student_id,
            course_code="22AM301",
            topic_name="MidTopic",
            total_q=10,
            correct_q=6,
            level=ADAPTIVE_LEVEL_MIXED,
            session_id="sess_mid",
            attempt_id="att_mid"
        )

        decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="MidTopic",
            conn=conn
        )
        conn.close()

        assert decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert decision["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert "Maintained Mixed difficulty" in decision["reason"]

    def test_student_and_topic_isolation(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        get_or_create_student("stud_alpha", conn=conn)
        get_or_create_student("stud_beta", conn=conn)

        # Student Alpha scores 100% on Topic X -> Advanced
        seed_history(
            conn=conn,
            student_id="stud_alpha",
            course_code="22AM301",
            topic_name="Topic X",
            total_q=10,
            correct_q=10,
            level=ADAPTIVE_LEVEL_MIXED,
            session_id="sess_xa",
            attempt_id="att_xa"
        )

        # Decision for Alpha on Topic X promotes to Advanced
        dec_alpha_x = compute_adaptive_decision("stud_alpha", "22AM301", "Topic X", conn=conn)
        assert dec_alpha_x["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED

        # Decision for Beta on Topic X remains Mixed (uninfluenced by Alpha)
        dec_beta_x = compute_adaptive_decision("stud_beta", "22AM301", "Topic X", conn=conn)
        assert dec_beta_x["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert dec_beta_x["graded_answers_count"] == 0

        # Decision for Alpha on Topic Y remains Mixed (uninfluenced by Topic X)
        dec_alpha_y = compute_adaptive_decision("stud_alpha", "22AM301", "Topic Y", conn=conn)
        assert dec_alpha_y["recommended_difficulty"] == ADAPTIVE_LEVEL_MIXED
        assert dec_alpha_y["graded_answers_count"] == 0

        conn.close()

    def test_adaptive_decision_determinism(self, isolated_db_client):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = "stud_determ"
        get_or_create_student(student_id, conn=conn)

        # Given the exact same inputs, multiple runs must return identical decisions
        d1 = compute_adaptive_decision(student_id, "22AM301", "Topic D", conn=conn)
        d2 = compute_adaptive_decision(student_id, "22AM301", "Topic D", conn=conn)
        conn.close()

        assert d1["recommended_difficulty"] == d2["recommended_difficulty"]
        assert d1["graded_answers_count"] == d2["graded_answers_count"]
        assert d1["accuracy"] == d2["accuracy"]
        assert d1["reason"] == d2["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Adaptive Generation Integration Tests
# ─────────────────────────────────────────────────────────────────────────────
class TestAdaptiveGenerationIntegration:
    @patch("main.generate_quiz")
    def test_generation_persists_adaptive_metadata(self, mock_gen, isolated_db_client):
        client, db_file = isolated_db_client

        mock_gen.return_value = {
            "subject": "Deep Learning",
            "topic": "Convolutional Networks",
            "course_code": "22AM301",
            "questions": SAMPLE_QUESTIONS,
            "recommended_difficulty": "advanced",
            "adaptive_applied": True,
            "adaptive_explanation": {"level": "advanced", "label": "Advanced Challenge", "description": "Challenging in-depth mastery"},
            "adaptive_metadata": {
                "student_id": "stud_int_1",
                "course_code": "22AM301",
                "topic_name": "Convolutional Networks",
                "previous_level": "mixed",
                "recommended_difficulty": "advanced",
                "history_window": 10,
                "graded_answers_count": 10,
                "accuracy": 0.90,
                "reason": "High recent accuracy (90% >= 80%). Promoted from Mixed to Advanced.",
                "is_decision_active": True,
                "policy_version": "1.0-stepwise",
                "timestamp": "2026-10-17T12:00:00Z"
            },
            "attempt_id": "att_gen_1",
            "attempt_number": 2,
            "provider_used": "groq",
            "is_fallback": False
        }

        res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "Convolutional Networks",
            "student_id": "stud_int_1"
        })
        assert res.status_code == 200
        data = res.json()

        assert data["recommended_difficulty"] == "advanced"
        assert data["adaptive_applied"] is True
        assert data["adaptive_metadata"]["reason"] == "High recent accuracy (90% >= 80%). Promoted from Mixed to Advanced."

        # Verify persisted in quiz_sessions
        session_id = data["quiz_id"]
        conn = get_connection(db_file)
        session = get_quiz_session(session_id, conn=conn)
        conn.close()

        assert session is not None
        assert session["recommended_difficulty"] == "advanced"
        assert session["adaptive_metadata"]["previous_level"] == "mixed"
        assert session["adaptive_metadata"]["accuracy"] == 0.90

    @patch("main.generate_quiz")
    def test_session_retrieval_returns_adaptive_metadata_and_sanitizes_questions(self, mock_gen, isolated_db_client):
        client, _ = isolated_db_client

        mock_gen.return_value = {
            "subject": "Deep Learning",
            "topic": "Convolutional Networks",
            "course_code": "22AM301",
            "questions": SAMPLE_QUESTIONS,
            "recommended_difficulty": "foundational",
            "adaptive_applied": True,
            "adaptive_explanation": {"level": "foundational", "label": "Foundational Practice"},
            "adaptive_metadata": {
                "previous_level": "mixed",
                "recommended_difficulty": "foundational",
                "graded_answers_count": 10,
                "accuracy": 0.30,
                "reason": "Demoted from Mixed to Foundational"
            },
            "attempt_id": "att_gen_2",
            "attempt_number": 2,
            "provider_used": "nvidia",
            "is_fallback": False
        }

        res = client.post("/api/quiz/generate", json={
            "course_code": "22AM301",
            "topic": "Convolutional Networks",
            "student_id": "stud_int_2"
        })
        session_id = res.json()["quiz_id"]

        # Retrieve session
        get_res = client.get(f"/api/quiz/session/{session_id}?student_id=stud_int_2")
        assert get_res.status_code == 200
        s_data = get_res.json()

        assert s_data["recommended_difficulty"] == "foundational"
        assert s_data["adaptive_metadata"]["previous_level"] == "mixed"
        # Ensure answers and explanations are stripped
        for q in s_data["questions"]:
            assert "correct_answer" not in q
            assert "explanation" not in q
            assert "difficulty" in q

    def test_stored_question_fallback_when_enabled(self, isolated_db_client, monkeypatch):
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        monkeypatch.setenv("ENABLE_STORED_FALLBACK", "true")
        monkeypatch.setenv("OVERALL_DEADLINE_SECONDS", "5.0")

        # Seed 10 validated stored questions in quiz_attempts using seed_history
        seed_history(
            conn=conn,
            student_id="other_stud_stored",
            course_code="22AM301",
            topic_name="Stored Topic",
            total_q=10,
            correct_q=8,
            level="mixed",
            session_id="sess_stored_1",
            attempt_id="att_stored_1"
        )

        # When all AI providers are unavailable, stored fallback serves questions
        with patch("services.llm_service._get_providers", return_value=[]):
            with patch("services.llm_service._load_local_demo_questions", return_value=None):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Stored Topic",
                    syllabus_context="Context",
                    student_id="stud_fallback_user"
                )

        assert quiz["is_fallback"] is True
        assert quiz["provider_used"] == "stored_db"
        assert len(quiz["questions"]) == 10
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Adaptive Submission & Chaining Tests
# ─────────────────────────────────────────────────────────────────────────────
class TestAdaptiveSubmissionAndChaining:
    def test_submission_updates_topic_mastery_and_promotes_next_quiz(self, isolated_db_client):
        """
        End-to-End Chaining Test:
        1. Student starts at Foundational difficulty.
        2. Submits quiz with 10/10 correct answers.
        3. Transaction commits, updating topic_mastery.
        4. Next quiz decision evaluates the updated performance and recommends Mixed difficulty!
        """
        client, db_file = isolated_db_client
        student_id = "stud_chaining_1"

        # Initialize session at Foundational
        conn = get_connection(db_file)
        get_or_create_student(student_id, conn=conn)
        create_quiz_session(
            session_id="sess_chain_1",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="History of Deep Learning, What Is Deep Learning",
            questions=SAMPLE_QUESTIONS,
            recommended_difficulty="foundational",
            adaptive_metadata={
                "previous_level": "foundational",
                "recommended_difficulty": "foundational",
                "reason": "Initial foundational assignment"
            },
            conn=conn
        )
        conn.commit()
        conn.close()

        # Submit quiz with perfect score (all 'A')
        submit_res = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_chain_1",
            "answers": ["A"] * 10,
            "student_id": student_id
        })
        assert submit_res.status_code == 200
        result = submit_res.json()
        assert result["score"] == 10

        # Check topic_mastery was updated
        conn = get_connection(db_file)
        mastery = get_topic_mastery(student_id, "22AM301", "History of Deep Learning, What Is Deep Learning", conn=conn)
        assert mastery is not None
        assert mastery["total_questions"] == 10
        assert mastery["total_correct"] == 10
        assert mastery["current_level"] == ADAPTIVE_LEVEL_MIXED

        # Next quiz decision for this student and topic must now recommend Mixed!
        next_decision = compute_adaptive_decision(
            student_id=student_id,
            course_code="22AM301",
            topic_name="History of Deep Learning, What Is Deep Learning",
            conn=conn
        )
        conn.close()

        assert next_decision["is_decision_active"] is True
        assert next_decision["previous_level"] == ADAPTIVE_LEVEL_MIXED
        assert next_decision["recommended_difficulty"] == ADAPTIVE_LEVEL_ADVANCED

    def test_duplicate_submission_does_not_double_update_mastery(self, isolated_db_client):
        """Duplicate submission returns existing result without inflating topic_mastery total_questions."""
        client, db_file = isolated_db_client
        student_id = "stud_dup_mastery"

        conn = get_connection(db_file)
        create_quiz_session(
            session_id="sess_dup_mastery",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="History of Deep Learning, What Is Deep Learning",
            questions=SAMPLE_QUESTIONS,
            recommended_difficulty="mixed",
            conn=conn
        )
        conn.commit()
        conn.close()

        # First submission
        res1 = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_dup_mastery",
            "answers": ["A"] * 10,
            "student_id": student_id
        })
        assert res1.status_code == 200

        # Second submission
        res2 = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_dup_mastery",
            "answers": ["A"] * 10,
            "student_id": student_id
        })
        assert res2.status_code == 200

        conn = get_connection(db_file)
        mastery = get_topic_mastery(student_id, "22AM301", "History of Deep Learning, What Is Deep Learning", conn=conn)
        conn.close()

        assert mastery["attempt_count"] == 1
        assert mastery["total_questions"] == 10
        assert mastery["total_correct"] == 10
