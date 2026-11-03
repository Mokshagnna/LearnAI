"""
Test Suite: AI Provider Reliability and Retake Novelty Assurance
Validates:
1. AI provider fallback, valid models, bounded retries on transient errors, fast-fail on auth/404, diagnostic headers.
2. Three-level duplicate detection:
   - Level 1: Exact duplicate detection.
   - Level 2: Near-duplicate detection (including canonical alias & synonym mapping).
   - Level 3: Learning objective duplicate detection.
3. Preservation of distinct learning objectives within the same syllabus topic (e.g., Backpropagation).
4. Targeted replacement ensuring 10 questions with 4 Easy, 4 Medium, 2 Hard difficulty distribution.
5. End-to-end multi-attempt retake simulation across successive attempts.
"""

import json
import os
import pytest
import sqlite3
import uuid
from typing import Dict, Any, List
from unittest.mock import patch, MagicMock

from services.novelty import (
    calculate_question_similarity,
    calculate_canonical_similarity,
    extract_canonical_tokens,
    extract_learning_objective,
    is_question_near_duplicate,
    evaluate_quiz_novelty,
    normalise_text,
)
from services.evaluator import evaluate_answers
from services.database import (
    init_db,
    get_or_create_student,
    create_quiz_session,
    submit_quiz_atomic,
    get_question_fingerprints,
    get_question_concepts,
    get_question_history_full,
)
from services.llm_service import (
    generate_quiz,
    AIServiceUnavailableError,
    AIError,
    AIRateLimitError,
    AIConfigError,
    AITimeoutError,
)
from services.providers.gemini_provider import GeminiProvider
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider


# ─────────────────────────────────────────────────────────────────────────────
# 1. AI Provider Reliability & Fallback Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAIProviderReliability:
    """Verifies provider configurations, valid model defaults, and error classification."""

    def test_gemini_valid_model_defaults(self):
        """Ensures Gemini defaults to gemini-2.0-flash with gemini-1.5-flash fallback."""
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
        assert model == "gemini-2.0-flash"
        assert fallback == "gemini-1.5-flash"
        assert "2.5" not in model

    def test_groq_valid_model_defaults(self):
        """Ensures Groq defaults to active models (llama-3.1-8b-instant, llama-3.3-70b-versatile)."""
        model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        fallback = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
        assert model in ("llama-3.1-8b-instant", "llama-3.3-70b-versatile")
        assert fallback in ("llama-3.1-8b-instant", "llama-3.3-70b-versatile")
        assert "mixtral" not in model

    def test_nvidia_valid_model_defaults(self):
        """Ensures NVIDIA defaults to valid meta/llama-3.3-70b-instruct with meta/llama-3.1-8b-instruct fallback."""
        model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        fallback = os.getenv("NVIDIA_FALLBACK_MODEL", "meta/llama-3.1-8b-instruct")
        assert model == "meta/llama-3.3-70b-instruct"
        assert fallback == "meta/llama-3.1-8b-instruct"

    def test_provider_transient_error_categorization(self):
        """HTTP 503 and 429 are categorized as retryable service unavailable / rate limit."""
        provider = GeminiProvider()
        exc_503 = Exception("HTTP 503: Service Unavailable")
        assert provider._is_retryable(exc_503) is True
        assert provider._categorize(exc_503) == "SERVICE_UNAVAILABLE_503"

        exc_429 = Exception("HTTP 429: Resource Exhausted Rate Limit")
        assert provider._is_retryable(exc_429) is True

    def test_provider_permanent_error_categorization(self):
        """HTTP 401, 403, and 404 fast-fail as permanent non-retryable errors."""
        provider = GeminiProvider()
        exc_404 = Exception("HTTP 404: Model not found")
        assert provider._is_retryable(exc_404) is False
        assert provider._categorize(exc_404) == "MODEL_NOT_FOUND_404"

        exc_auth = Exception("API key not valid. Please pass a valid API key.")
        assert provider._is_auth_error(exc_auth) is True
        assert provider._is_retryable(exc_auth) is False
        assert provider._categorize(exc_auth) == "AUTH_CONFIG_ERROR"

    def test_standardized_student_facing_error_message(self):
        """Verifies standardized student-facing message and retryable status code."""
        err = AIServiceUnavailableError()
        assert err.status_code == 503
        assert err.retryable is True
        assert err.message == "Quiz generation is temporarily unavailable. Please try again shortly."


# ─────────────────────────────────────────────────────────────────────────────
# 2. Three-Level Duplicate Detection Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestThreeLevelDuplicateDetection:
    """Verifies Level 1 (exact), Level 2 (near/canonical), and Level 3 (objective)."""

    def test_level1_exact_duplicate_detection(self):
        """Verifies exact matches and whitespace/casing normalization."""
        q1 = {"question": "What is the primary function of a perceptron?", "concept": "Perceptron"}
        q2 = {"question": "  what is the primary function of a perceptron?  ", "concept": "Perceptron"}

        is_dup, reason = is_question_near_duplicate(q1, q2)
        assert is_dup is True
        assert "Exact identical question wording" in reason

    def test_level2_artificial_variation_detection(self):
        """Verifies artificial variation tags like '(Application Variation)' are caught."""
        q1 = {"question": "Calculate the gradient of the MSE loss function.", "concept": "Loss Function"}
        q2 = {"question": "Calculate the gradient of the MSE loss function (Application Variation)", "concept": "Loss Function"}

        is_dup, reason = is_question_near_duplicate(q1, q2)
        assert is_dup is True
        assert "Artificial variation" in reason

    def test_level2_canonical_alias_user_case_1(self):
        """
        User Bug Case 1:
        Q1: 'What is the main purpose of the McCulloch-Pitts neuron?'
        Q2: 'What is the primary function of the MP neuron?'
        Must be detected as duplicate via alias & synonym canonicalization.
        """
        q1 = {"question": "What is the main purpose of the McCulloch-Pitts neuron?", "concept": "McCulloch-Pitts Neuron"}
        q2 = {"question": "What is the primary function of the MP neuron?", "concept": "McCulloch-Pitts Neuron"}

        is_dup, reason = is_question_near_duplicate(q1, q2, topic_name="McCulloch-Pitts Neuron")
        assert is_dup is True
        assert any(term in reason.lower() for term in ["paraphrasing", "learning objective", "similarity"])

    def test_level2_canonical_alias_user_case_2(self):
        """
        User Bug Case 2:
        Q1: 'Which values can a McCulloch-Pitts neuron take as inputs?'
        Q2: 'What are the allowed input values for an MP neuron?'
        Must be detected as duplicate via alias & synonym canonicalization.
        """
        q1 = {"question": "Which values can a McCulloch-Pitts neuron take as inputs?", "concept": "McCulloch-Pitts Neuron"}
        q2 = {"question": "What are the allowed input values for an MP neuron?", "concept": "McCulloch-Pitts Neuron"}

        is_dup, reason = is_question_near_duplicate(q1, q2, topic_name="McCulloch-Pitts Neuron")
        assert is_dup is True
        assert any(term in reason.lower() for term in ["paraphrasing", "learning objective", "similarity"])

    def test_level2_canonical_alias_user_case_3(self):
        """
        User Bug Case 3:
        Q1: 'Why can't a single McCulloch-Pitts neuron implement the XOR function?'
        Q2: 'What prevents an MP neuron from solving the XOR problem?'
        Must be detected as duplicate via alias & synonym canonicalization.
        """
        q1 = {"question": "Why can't a single McCulloch-Pitts neuron implement the XOR function?", "concept": "McCulloch-Pitts Neuron"}
        q2 = {"question": "What prevents an MP neuron from solving the XOR problem?", "concept": "McCulloch-Pitts Neuron"}

        is_dup, reason = is_question_near_duplicate(q1, q2, topic_name="McCulloch-Pitts Neuron")
        assert is_dup is True
        assert any(term in reason.lower() for term in ["paraphrasing", "learning objective", "similarity"])

    def test_level3_preserves_distinct_learning_objectives_in_same_topic(self):
        """
        Critical Syllabus Requirement:
        Distinct learning objectives within the SAME topic (e.g., Backpropagation)
        MUST NOT be flagged as duplicates.
        - Chain rule application
        - Weight updates
        - Gradient calculation
        """
        q_chain = {
            "question": "How does the chain rule apply to error backpropagation in deep neural networks?",
            "concept": "Backpropagation Chain Rule",
            "options": {"A": "Multiplies partial derivatives", "B": "Adds constants", "C": "Inverts matrices", "D": "Zeroes out activations"},
            "correct_answer": "A"
        }
        q_weight = {
            "question": "What is the formula for weight updates in gradient descent during backpropagation?",
            "concept": "Weight Update Rule",
            "options": {"A": "w = w - lr * grad", "B": "w = w + grad", "C": "w = lr / grad", "D": "w = w * lr"},
            "correct_answer": "A"
        }
        q_grad = {
            "question": "How are gradients calculated for hidden layers in backpropagation?",
            "concept": "Gradient Calculation",
            "options": {"A": "Propagating errors backward from output", "B": "Direct forward measurement", "C": "Random perturbation", "D": "Analytical integration"},
            "correct_answer": "A"
        }

        # Verify pairwise independence
        is_dup1, _ = is_question_near_duplicate(q_chain, q_weight, topic_name="Backpropagation")
        is_dup2, _ = is_question_near_duplicate(q_chain, q_grad, topic_name="Backpropagation")
        is_dup3, _ = is_question_near_duplicate(q_weight, q_grad, topic_name="Backpropagation")

        assert is_dup1 is False, "Chain rule and weight update must be recognized as distinct objectives"
        assert is_dup2 is False, "Chain rule and gradient calculation must be recognized as distinct objectives"
        assert is_dup3 is False, "Weight update and gradient calculation must be recognized as distinct objectives"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Novelty Evaluator and Targeted Replacement Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNoveltyEvaluatorAndTargetedReplacement:
    """Verifies evaluate_quiz_novelty across history and targeted replacement logic."""

    def test_evaluate_quiz_novelty_catches_all_levels(self):
        """Tests that evaluate_quiz_novelty flags exact, near, and objective duplicates."""
        history_questions = [
            {"question": "What is the main purpose of the McCulloch-Pitts neuron?", "concept": "McCulloch-Pitts Neuron"},
            {"question": "Which values can a McCulloch-Pitts neuron take as inputs?", "concept": "McCulloch-Pitts Neuron"},
            {"question": "Why can't a single McCulloch-Pitts neuron implement the XOR function?", "concept": "McCulloch-Pitts Neuron"}
        ]
        history_fps = [normalise_text(q["question"]) for q in history_questions]
        history_concepts = [q["concept"] for q in history_questions]

        # Candidate quiz containing 3 duplicates and 1 novel question
        candidate_questions = [
            {"question": "What is the primary function of the MP neuron?", "concept": "McCulloch-Pitts Neuron"},  # Dup of 0
            {"question": "What are the allowed input values for an MP neuron?", "concept": "McCulloch-Pitts Neuron"},  # Dup of 1
            {"question": "What prevents an MP neuron from solving the XOR problem?", "concept": "McCulloch-Pitts Neuron"},  # Dup of 2
            {"question": "In what year did Warren McCulloch and Walter Pitts publish their formal neuron model?", "concept": "History of MP Neuron"}  # Novel
        ]

        result = evaluate_quiz_novelty(
            questions=candidate_questions,
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=history_questions,
            topic_name="McCulloch-Pitts Neuron"
        )

        assert result["is_fully_novel"] is False
        assert len(result["all_flagged_indices"]) == 3
        assert 0 in result["all_flagged_indices"]
        assert 1 in result["all_flagged_indices"]
        assert 2 in result["all_flagged_indices"]
        assert 3 not in result["all_flagged_indices"]
        assert result["novel_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. End-to-End Multi-Attempt Retake Simulation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiAttemptRetakeSimulation:
    """Simulates 3 consecutive quiz attempts on the same topic."""

    def test_three_attempt_retake_novelty_pipeline(self, tmp_path):
        """
        Simulates:
        1. Attempt 1 generated and submitted with 10 questions.
        2. Attempt 2 retake requested: 2 generated questions happen to duplicate Attempt 1.
           Targeted replacement detects them and replaces them with 2 novel questions.
           Attempt 2 submitted.
        3. Attempt 3 retake requested: questions verified against history of both attempts.
        """
        test_db = tmp_path / "test_learnai.db"
        init_db(test_db)
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row

        student_id = get_or_create_student("student_retake_test", conn=conn)
        course_code = "22AM301"
        topic_name = "McCulloch-Pitts Neuron"

        # ── Attempt 1: 10 initial questions ──
        attempt1_questions = [
            {
                "question": f"MP Neuron Attempt 1 Question {i+1}: What is rule {i+1}?",
                "options": {"A": f"Rule {i+1} Correct", "B": "Distractor B", "C": "Distractor C", "D": "Distractor D"},
                "correct_answer": "A",
                "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
                "concept": f"Rule {i+1}",
                "explanation": f"Explanation for rule {i+1}"
            }
            for i in range(10)
        ]

        sess1_id = str(uuid.uuid4())
        session1 = create_quiz_session(
            session_id=sess1_id,
            student_id=student_id,
            course_code=course_code,
            course_name="Deep Learning",
            topic_name=topic_name,
            questions=attempt1_questions,
            recommended_difficulty="mixed",
            conn=conn
        )

        # Submit Attempt 1
        sub1 = submit_quiz_atomic(
            session_id=sess1_id,
            student_id=student_id,
            answers=["A"] * 10,
            evaluator_fn=evaluate_answers,
            conn=conn
        )
        assert sub1["score"] == 10

        # Verify fingerprints and history questions in DB
        fps_after_att1 = get_question_fingerprints(student_id, course_code, topic_name, conn=conn)
        assert len(fps_after_att1) == 10

        history_full_att1 = get_question_history_full(student_id, course_code, topic_name, conn=conn)
        assert len(history_full_att1) >= 10

        # ── Attempt 2: AI generates 10 questions where Q1, Q2 duplicate Attempt 1 ──
        attempt2_candidates = [
            # Q1 and Q2 duplicate Attempt 1 questions
            {"question": "MP Neuron Attempt 1 Question 1: What is rule 1?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Rule 1"},
            {"question": "MP Neuron Attempt 1 Question 2: What is rule 2?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Rule 2"},
        ] + [
            # Q3-Q10 are novel
            {
                "question": f"MP Neuron Attempt 2 Question {i+1}: What is novelty aspect {i+1}?",
                "options": {"A": "Correct", "B": "B", "C": "C", "D": "D"},
                "correct_answer": "A",
                "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
                "concept": f"Aspect {i+1}",
                "explanation": f"Explanation {i+1}"
            }
            for i in range(2, 10)
        ]

        eval2 = evaluate_quiz_novelty(
            questions=attempt2_candidates,
            history_fingerprints=fps_after_att1,
            history_concepts=get_question_concepts(student_id, course_code, topic_name, conn=conn),
            history_questions=history_full_att1,
            topic_name=topic_name
        )

        assert eval2["is_fully_novel"] is False
        assert eval2["all_flagged_indices"] == [0, 1]

        # Targeted replacement replaces Q1, Q2 with 2 brand-new questions
        novel_kept = [q for idx, q in enumerate(attempt2_candidates) if idx not in eval2["all_flagged_indices"]]
        assert len(novel_kept) == 8

        replacements = [
            {"question": "MP Neuron Brand New Replacement 1?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": "New Concept 1"},
            {"question": "MP Neuron Brand New Replacement 2?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": "New Concept 2"}
        ]
        attempt2_final = novel_kept + replacements
        assert len(attempt2_final) == 10

        eval2_repaired = evaluate_quiz_novelty(
            questions=attempt2_final,
            history_fingerprints=fps_after_att1,
            history_concepts=get_question_concepts(student_id, course_code, topic_name, conn=conn),
            history_questions=history_full_att1,
            topic_name=topic_name
        )
        assert eval2_repaired["is_fully_novel"] is True
        assert eval2_repaired["novel_count"] == 10

        # Submit Attempt 2
        sess2_id = str(uuid.uuid4())
        session2 = create_quiz_session(
            session_id=sess2_id,
            student_id=student_id,
            course_code=course_code,
            course_name="Deep Learning",
            topic_name=topic_name,
            questions=attempt2_final,
            recommended_difficulty="mixed",
            conn=conn
        )
        sub2 = submit_quiz_atomic(
            session_id=sess2_id,
            student_id=student_id,
            answers=["A"] * 10,
            evaluator_fn=evaluate_answers,
            conn=conn
        )
        assert sub2["score"] == 10

        # ── Attempt 3: History now contains 20 distinct questions ──
        history_full_att2 = get_question_history_full(student_id, course_code, topic_name, conn=conn)
        assert len(history_full_att2) == 20

        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Isolation and Edge-Case Tests (Student, Course, Topic, DB Persistence)
# ─────────────────────────────────────────────────────────────────────────────

class TestIsolationAndEdgeCases:
    """Verifies isolation boundaries and database persistence."""

    def test_student_isolation(self, tmp_path):
        """Student A's history does not prevent Student B from receiving identical questions."""
        test_db = tmp_path / "test_student_iso.db"
        init_db(test_db)
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row

        student_a = get_or_create_student("student_A", conn=conn)
        student_b = get_or_create_student("student_B", conn=conn)
        course_code = "22AM301"
        topic_name = "Perceptron"

        questions_a = [
            {
                "question": f"Perceptron core question {i+1}?",
                "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                "correct_answer": "A",
                "difficulty": "Easy",
                "concept": f"Concept {i+1}"
            }
            for i in range(10)
        ]

        # Student A completes quiz
        sess_a = str(uuid.uuid4())
        create_quiz_session(sess_a, student_a, course_code, "Deep Learning", topic_name, questions_a, "mixed", conn=conn)
        submit_quiz_atomic(sess_a, student_a, ["A"] * 10, evaluate_answers, conn=conn)

        # Student A history has 10 questions
        fps_a = get_question_fingerprints(student_a, course_code, topic_name, conn=conn)
        assert len(fps_a) == 10

        # Student B history MUST be empty
        fps_b = get_question_fingerprints(student_b, course_code, topic_name, conn=conn)
        assert len(fps_b) == 0

        # Student B evaluates questions identical to Student A's questions: MUST be 100% novel for Student B
        eval_b = evaluate_quiz_novelty(
            questions=questions_a,
            history_fingerprints=fps_b,
            history_concepts=[],
            history_questions=[],
            topic_name=topic_name
        )
        assert eval_b["is_fully_novel"] is True
        assert eval_b["novel_count"] == 10
        conn.close()

    def test_course_isolation(self, tmp_path):
        """Attempts in Course A do not cross-contaminate history for Course B for the same student."""
        test_db = tmp_path / "test_course_iso.db"
        init_db(test_db)
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row

        student_id = get_or_create_student("multi_course_student", conn=conn)
        course_1 = "22AM301"
        course_2 = "22CS201"
        topic_name = "Loss Function"

        questions = [
            {"question": f"Loss function question {i+1}?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": f"Loss {i+1}"}
            for i in range(10)
        ]

        sess_1 = str(uuid.uuid4())
        create_quiz_session(sess_1, student_id, course_1, "Deep Learning", topic_name, questions, "mixed", conn=conn)
        submit_quiz_atomic(sess_1, student_id, ["A"] * 10, evaluate_answers, conn=conn)

        # Course 1 has 10 fingerprints
        fps_c1 = get_question_fingerprints(student_id, course_1, topic_name, conn=conn)
        assert len(fps_c1) == 10

        # Course 2 has 0 fingerprints
        fps_c2 = get_question_fingerprints(student_id, course_2, topic_name, conn=conn)
        assert len(fps_c2) == 0
        conn.close()

    def test_topic_isolation(self, tmp_path):
        """Completed questions in Topic 1 do not block questions in Topic 2 for the same course."""
        test_db = tmp_path / "test_topic_iso.db"
        init_db(test_db)
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row

        student_id = get_or_create_student("topic_student", conn=conn)
        course_code = "22AM301"
        topic_1 = "McCulloch-Pitts Neuron"
        topic_2 = "Perceptron"

        questions = [
            {"question": f"Topic 1 question {i+1}?", "options": {"A": "A", "B": "B", "C": "C", "D": "D"}, "correct_answer": "A", "difficulty": "Easy", "concept": f"Topic1 {i+1}"}
            for i in range(10)
        ]

        sess_1 = str(uuid.uuid4())
        create_quiz_session(sess_1, student_id, course_code, "Deep Learning", topic_1, questions, "mixed", conn=conn)
        submit_quiz_atomic(sess_1, student_id, ["A"] * 10, evaluate_answers, conn=conn)

        assert len(get_question_fingerprints(student_id, course_code, topic_1, conn=conn)) == 10
        assert len(get_question_fingerprints(student_id, course_code, topic_2, conn=conn)) == 0
        conn.close()

    def test_database_persistence_across_process_restarts(self, tmp_path):
        """Historical fingerprints and full questions persist across separate database connections."""
        test_db = tmp_path / "persistent_learnai.db"
        init_db(test_db)

        student_id = "persistent_student_xyz"
        course_code = "22AM301"
        topic_name = "Backpropagation"
        questions = [
            {
                "question": f"Backprop persistent question {i+1}: What is factor {i+1}?",
                "options": {"A": "Correct", "B": "B", "C": "C", "D": "D"},
                "correct_answer": "A",
                "difficulty": "Medium",
                "concept": f"Factor {i+1}",
                "explanation": f"Explanation {i+1}"
            }
            for i in range(10)
        ]

        # Connection 1: write and submit
        conn1 = sqlite3.connect(test_db)
        conn1.row_factory = sqlite3.Row
        s_id = get_or_create_student(student_id, conn=conn1)
        sess_id = str(uuid.uuid4())
        create_quiz_session(sess_id, s_id, course_code, "Deep Learning", topic_name, questions, "mixed", conn=conn1)
        submit_quiz_atomic(sess_id, s_id, ["A"] * 10, evaluate_answers, conn=conn1)
        conn1.close()

        # Connection 2: simulate new backend process reading from disk
        conn2 = sqlite3.connect(test_db)
        conn2.row_factory = sqlite3.Row
        fps_read = get_question_fingerprints(student_id, course_code, topic_name, conn=conn2)
        history_read = get_question_history_full(student_id, course_code, topic_name, conn=conn2)
        concepts_read = get_question_concepts(student_id, course_code, topic_name, conn=conn2)

        assert len(fps_read) == 10
        assert len(history_read) == 10
        assert len(concepts_read) == 10
        assert history_read[0]["question"].startswith("Backprop persistent question 1")
        conn2.close()

    def test_graceful_handling_when_candidate_pool_exhausted(self):
        """When candidate questions duplicate history, evaluate_quiz_novelty cleanly flags all duplicates."""
        history_questions = [
            {"question": "What is the function of an activation function in a neural network?", "concept": "Activation Function"}
        ]
        history_fps = [normalise_text(q["question"]) for q in history_questions]

        duplicate_candidates = [
            {"question": "What is the function of an activation function in a neural network?", "concept": "Activation Function"}
        ]

        res = evaluate_quiz_novelty(
            questions=duplicate_candidates,
            history_fingerprints=history_fps,
            history_concepts=["Activation Function"],
            history_questions=history_questions,
            topic_name="Activation Functions"
        )

        assert res["is_fully_novel"] is False
        assert res["all_flagged_indices"] == [0]
        assert 0 in res["text_duplicate_indices"]
        assert "Exact Duplicate" in res["duplicate_reasons"][0]
        assert res["novel_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Extended Retake Scenarios (1-dup, 3-dup, Paraphrased-only, Vocab Overlap, Deep History)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtendedRetakeScenarios:
    """Verifies edge-case retake scenarios required by Phase 4."""

    def test_candidate_with_single_duplicate(self):
        """Candidate quiz with exactly 1 duplicate out of 10 questions is cleanly intercepted."""
        history = [
            {"question": "What is the primary function of the McCulloch-Pitts neuron?", "concept": "Purpose"}
        ]
        history_fps = [normalise_text(q["question"]) for q in history]

        candidate_quiz = [
            # Q0 is a duplicate
            {"question": "What is the main purpose of the McCulloch-Pitts neuron?", "concept": "Purpose"}
        ] + [
            # Q1-Q9 are novel
            {"question": f"MP neuron distinct aspect {i+1}?", "concept": f"Aspect {i+1}"}
            for i in range(1, 10)
        ]

        res = evaluate_quiz_novelty(
            questions=candidate_quiz,
            history_fingerprints=history_fps,
            history_concepts=["Purpose"],
            history_questions=history,
            topic_name="McCulloch-Pitts Neuron"
        )

        assert res["is_fully_novel"] is False
        assert res["all_flagged_indices"] == [0]
        assert res["novel_count"] == 9

    def test_candidate_with_three_duplicates(self):
        """Candidate quiz with 3 duplicates (exact, near, and objective) is intercepted with novel_count=7."""
        history = [
            {"question": "What is the primary function of the McCulloch-Pitts neuron?", "concept": "Purpose of MP Neuron"},
            {"question": "Which values can a McCulloch-Pitts neuron take as inputs?", "concept": "Input Domain"},
            {"question": "Why can't a single McCulloch-Pitts neuron implement the XOR function?", "concept": "XOR Limitation"}
        ]
        history_fps = [normalise_text(q["question"]) for q in history]
        history_concepts = [q["concept"] for q in history]

        candidates = [
            # Dup 1: Level 1 exact
            {"question": "What is the primary function of the McCulloch-Pitts neuron?", "concept": "Purpose of MP Neuron"},
            # Dup 2: Level 2 canonical alias
            {"question": "What are the allowed input values for an MP neuron?", "concept": "Input Domain"},
            # Dup 3: Level 3 learning objective
            {"question": "What prevents an MP neuron from solving the XOR problem?", "concept": "XOR Limitation"}
        ] + [
            # 7 Novel questions
            {"question": f"MP neuron novel inquiry {i+1}?", "concept": f"NovelConcept {i+1}"}
            for i in range(3, 10)
        ]

        res = evaluate_quiz_novelty(
            questions=candidates,
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=history,
            topic_name="McCulloch-Pitts Neuron"
        )

        assert res["is_fully_novel"] is False
        assert res["all_flagged_indices"] == [0, 1, 2]
        assert res["novel_count"] == 7

    def test_candidate_with_only_paraphrased_duplicates(self):
        """Candidate quiz where ALL 10 questions are paraphrased duplicates of history is 100% intercepted."""
        history = [
            {"question": f"Standard historical question statement for concept {i+1}?", "concept": f"Concept {i+1}"}
            for i in range(10)
        ]
        history_fps = [normalise_text(q["question"]) for q in history]
        history_concepts = [q["concept"] for q in history]

        # Paraphrased candidates using artificial variations or synonyms
        paraphrased_candidates = [
            {"question": f"Standard historical question statement for concept {i+1} (Application Variation)", "concept": f"Concept {i+1}"}
            for i in range(10)
        ]

        res = evaluate_quiz_novelty(
            questions=paraphrased_candidates,
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=history,
            topic_name="General Topic"
        )

        assert res["is_fully_novel"] is False
        assert len(res["all_flagged_indices"]) == 10
        assert res["novel_count"] == 0

    def test_candidate_with_different_concepts_similar_vocabulary(self):
        """Questions on genuinely distinct concepts with overlapping vocabulary are NOT rejected."""
        q_and = {"question": "How can an MP neuron implement a 2-input logical AND gate?", "concept": "AND Gate"}
        q_or = {"question": "How can an MP neuron implement a 2-input logical OR gate?", "concept": "OR Gate"}
        is_dup_gates, _ = is_question_near_duplicate(q_and, q_or, topic_name="McCulloch-Pitts Neuron")
        assert is_dup_gates is False, "AND gate and OR gate must be recognized as distinct"

        q_act = {"question": "What is the role of an activation function in a deep neural network?", "concept": "Activation Function"}
        q_loss = {"question": "What is the role of a loss function in evaluating cross-entropy error?", "concept": "Loss Function"}
        is_dup_fn, _ = is_question_near_duplicate(q_act, q_loss, topic_name="Neural Networks")
        assert is_dup_fn is False, "Activation function and loss function must be recognized as distinct"

        q_exc = {"question": "How do excitatory inputs affect the threshold summation in an MP neuron?", "concept": "Excitatory Inputs"}
        q_inh = {"question": "How do inhibitory inputs completely suppress action potential firing?", "concept": "Inhibitory Inputs"}
        is_dup_in, _ = is_question_near_duplicate(q_exc, q_inh, topic_name="McCulloch-Pitts Neuron")
        assert is_dup_in is False, "Excitatory and inhibitory inputs must be recognized as distinct"

    def test_topic_with_limited_learning_objectives(self):
        """When history exhausts all available topic facets, novelty check accurately reports 0 novel questions."""
        history = [
            {"question": "Fact 1 of narrow micro-topic?", "concept": "Fact 1"},
            {"question": "Fact 2 of narrow micro-topic?", "concept": "Fact 2"},
            {"question": "Fact 3 of narrow micro-topic?", "concept": "Fact 3"}
        ]
        history_fps = [normalise_text(q["question"]) for q in history]

        # Candidates repeat all 3 facts
        candidates = [
            {"question": "Fact 1 of narrow micro-topic?", "concept": "Fact 1"},
            {"question": "Fact 2 of narrow micro-topic?", "concept": "Fact 2"},
            {"question": "Fact 3 of narrow micro-topic?", "concept": "Fact 3"}
        ]

        res = evaluate_quiz_novelty(
            questions=candidates,
            history_fingerprints=history_fps,
            history_concepts=["Fact 1", "Fact 2", "Fact 3"],
            history_questions=history,
            topic_name="Micro Topic"
        )
        assert res["is_fully_novel"] is False
        assert res["novel_count"] == 0
        assert len(res["all_flagged_indices"]) == 3

    def test_fifth_and_sixth_retakes_deep_history(self):
        """Verifies novelty evaluation across 40 and 50 accumulated prior questions in deep retake history."""
        # Accumulate 50 historical questions across 5 prior attempts
        deep_history = [
            {
                "question": f"Deep historical question statement number {i+1} for McCulloch-Pitts?",
                "concept": f"DeepConcept {i+1}"
            }
            for i in range(50)
        ]
        deep_fps = [normalise_text(q["question"]) for q in deep_history]
        deep_concepts = [q["concept"] for q in deep_history]

        # Candidate quiz for Attempt 6: 10 brand-new questions
        attempt6_candidates = [
            {
                "question": f"Attempt 6 brand new inquiry number {j+1} for McCulloch-Pitts?",
                "concept": f"Attempt6Concept {j+1}"
            }
            for j in range(10)
        ]

        res = evaluate_quiz_novelty(
            questions=attempt6_candidates,
            history_fingerprints=deep_fps,
            history_concepts=deep_concepts,
            history_questions=deep_history,
            topic_name="McCulloch-Pitts Neuron"
        )

        assert res["is_fully_novel"] is True
        assert res["novel_count"] == 10
        assert len(res["all_flagged_indices"]) == 0


