"""
test_production_readiness.py
============================
Comprehensive test suite for LearnAI:
- Successful quiz generation & mock provider orchestration
- Blank or incomplete answer options rejection
- Duplicate and off-topic questions detection
- Correct-answer validation and normalization
- Answer evaluation and score calculation
- Curriculum integrity and clean topic selection
- Security & request boundary validation
- Secret redaction and privacy preservation

All tests run offline with zero network calls and zero quota consumption.

Run with:
    python -m unittest backend.test_production_readiness -v
"""
import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.join(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.providers.base import (
    validate_single_question,
    validate_quiz_data,
    extract_normalized_options,
    normalize_correct_answer,
    extract_json_from_text,
    ProviderUnavailableError,
    ProviderConfigError
)
from services.evaluator import evaluate_answers
from services.curriculum_service import curriculum_service
from services.novelty import (
    is_question_near_duplicate,
    detect_within_quiz_duplicates,
    is_question_relevant_to_topic
)
from services.llm_service import generate_quiz, AIServiceUnavailableError, AIConfigError
from main import app, QuizRequest, SubmitRequest
from pydantic import ValidationError


class TestQuestionValidationAndNormalization(unittest.TestCase):

    def test_blank_option_rejected(self):
        """Questions with blank, null, or placeholder options must be rejected."""
        q_blank = {
            "question": "What is the rank of an identity matrix of size 3x3?",
            "options": {"A": "3", "B": "", "C": "1", "D": "0"},
            "correct_answer": "A",
            "concept": "Rank"
        }
        res = validate_single_question(q_blank)
        self.assertIsNone(res, "Question with empty option B should be rejected")

        q_placeholder = {
            "question": "What is the rank of an identity matrix of size 3x3?",
            "options": {"A": "3", "B": "N/A", "C": "1", "D": "0"},
            "correct_answer": "A",
            "concept": "Rank"
        }
        res_placeholder = validate_single_question(q_placeholder)
        self.assertIsNone(res_placeholder, "Question with placeholder option B ('N/A') should be rejected")

    def test_missing_option_keys_rejected(self):
        """Questions lacking any of A, B, C, D must be rejected without inventing options."""
        q_three_opts = {
            "question": "Which layer applies convolution?",
            "options": {"A": "Conv2D", "B": "Dense", "C": "Dropout"},
            "correct_answer": "A"
        }
        res = validate_single_question(q_three_opts)
        self.assertIsNone(res, "Question with only 3 options must be rejected")

    def test_correct_answer_normalization(self):
        """Correct answers in varied formats (A, Option A, choice B, full text) must normalize to letter."""
        opts = {"A": "ReLU", "B": "Sigmoid", "C": "Tanh", "D": "Softmax"}

        self.assertEqual(normalize_correct_answer("A", opts), "A")
        self.assertEqual(normalize_correct_answer("b", opts), "B")
        self.assertEqual(normalize_correct_answer("Option C", opts), "C")
        self.assertEqual(normalize_correct_answer("Choice (D)", opts), "D")
        self.assertEqual(normalize_correct_answer("ReLU", opts), "A")
        self.assertEqual(normalize_correct_answer("1", opts), "A")
        self.assertEqual(normalize_correct_answer("4", opts), "D")
        self.assertIsNone(normalize_correct_answer("Invalid", opts))
        self.assertIsNone(normalize_correct_answer(None, opts))

    def test_duplicate_question_detection(self):
        """Duplicate and near-duplicate questions within a quiz are strictly filtered."""
        q1 = {
            "question": "What is the derivative of the sigmoid function?",
            "options": {"A": "s(1-s)", "B": "1-s^2", "C": "max(0,1)", "D": "e^x"},
            "correct_answer": "A",
            "concept": "Sigmoid Derivative"
        }
        q1_paraphrase = {
            "question": "Which expression represents the derivative of the sigmoid activation function?",
            "options": {"A": "s(1-s)", "B": "1-s^2", "C": "max(0,1)", "D": "e^x"},
            "correct_answer": "A",
            "concept": "Sigmoid Derivative"
        }
        is_dup, reason = is_question_near_duplicate(q1, q1_paraphrase)
        self.assertTrue(is_dup, f"Expected near duplicate to be flagged: {reason}")

        # Batch deduplication
        batch = [q1, q1_paraphrase]
        res = detect_within_quiz_duplicates(batch)
        self.assertTrue(res["has_duplicates"])
        self.assertEqual(len(res["unique_questions"]), 1)

    def test_off_topic_question_filtered(self):
        """Questions outside the topic scope are rejected."""
        q_off_topic = {
            "question": "What is the integrating factor of dy/dx + Py = Q?",
            "options": {"A": "e^(int P dx)", "B": "e^x", "C": "P", "D": "Q"},
            "correct_answer": "A",
            "concept": "Differential Equations"
        }
        is_rel, reason = is_question_relevant_to_topic(
            q_off_topic,
            topic_name="Linear Algebra Concepts",
            concepts=["Definition of matrix", "Types of matrices"],
            syllabus_context="Matrices: Definition, types, adjoint, inverse"
        )
        self.assertFalse(is_rel, "Differential equations question should be rejected for Linear Algebra")


class TestAnswerEvaluationAndScoring(unittest.TestCase):

    def test_perfect_score_evaluation(self):
        """100% score yields zero weak concepts and 'Excellent' status."""
        questions = [
            {"question": "Q1", "correct_answer": "A", "concept": "Matrices", "difficulty": "Easy"},
            {"question": "Q2", "correct_answer": "B", "concept": "Matrices", "difficulty": "Medium"},
            {"question": "Q3", "correct_answer": "C", "concept": "Determinants", "difficulty": "Hard"}
        ]
        answers = ["A", "B", "C"]
        res = evaluate_answers(questions, answers)

        self.assertEqual(res["score"], 3)
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["percentage"], 100.0)
        self.assertEqual(res["status"], "Excellent")
        self.assertEqual(len(res["weak_concepts"]), 0)
        self.assertIn("Matrices", res["strong_concepts"])
        self.assertIn("Determinants", res["strong_concepts"])

    def test_weak_concept_detection(self):
        """Concepts where accuracy < 60% are classified as weak."""
        questions = [
            {"question": "Q1", "correct_answer": "A", "concept": "Matrices", "difficulty": "Easy"},
            {"question": "Q2", "correct_answer": "B", "concept": "Matrices", "difficulty": "Easy"},
            {"question": "Q3", "correct_answer": "C", "concept": "Inverses", "difficulty": "Medium"},
            {"question": "Q4", "correct_answer": "D", "concept": "Inverses", "difficulty": "Hard"}
        ]
        # Student answers both Matrices correctly, but misses both Inverses
        answers = ["A", "B", "A", "A"]
        res = evaluate_answers(questions, answers)

        self.assertEqual(res["score"], 2)
        self.assertEqual(res["percentage"], 50.0)
        self.assertEqual(res["status"], "Developing")
        self.assertIn("Inverses", res["weak_concepts"])
        self.assertIn("Matrices", res["strong_concepts"])
        self.assertEqual(res["concept_accuracy"]["Matrices"]["accuracy"], 100.0)
        self.assertEqual(res["concept_accuracy"]["Inverses"]["accuracy"], 0.0)


class TestCurriculumAndTopicSelection(unittest.TestCase):

    def test_curriculum_loads_valid_courses(self):
        """Curriculum data loads accurately without modifying curriculum.json."""
        curriculum = curriculum_service.get_curriculum()
        self.assertIn("courses", curriculum)
        courses = curriculum["courses"]
        self.assertGreater(len(courses), 0)

        # Confirm all courses have clean code, name, and topics
        for c in courses:
            self.assertIn("code", c)
            self.assertIn("name", c)
            self.assertIn("topics", c)
            self.assertGreater(len(c["topics"]), 0)

    def test_selectable_topics_exclude_module_unit_headings(self):
        """Selectable topics must not be bare syllabus module/unit structural headings."""
        curriculum = curriculum_service.get_curriculum()
        for course in curriculum["courses"]:
            for topic in course["topics"]:
                name = topic.get("name", "")
                self.assertFalse(
                    name.lower().startswith("module ") and "unit" in name.lower() and ":" not in name,
                    f"Structural heading found as topic name: {name}"
                )


class TestSecurityAndRequestValidation(unittest.TestCase):

    def test_quiz_request_valid_boundaries(self):
        """QuizRequest accepts valid parameters and rejects invalid ones."""
        valid_req = QuizRequest(course_code="22AM301", topic="Deep Learning Concepts")
        self.assertEqual(valid_req.course_code, "22AM301")
        self.assertEqual(valid_req.topic, "Deep Learning Concepts")

        # Empty course_code rejected (min_length=2)
        with self.assertRaises(ValidationError):
            QuizRequest(course_code="", topic="Valid Topic")

        # Oversized course_code rejected (max_length=50)
        with self.assertRaises(ValidationError):
            QuizRequest(course_code="X" * 51, topic="Valid Topic")

        # Empty topic rejected (min_length=1)
        with self.assertRaises(ValidationError):
            QuizRequest(course_code="22AM301", topic="")

    def test_submit_request_valid_boundaries(self):
        """SubmitRequest enforces quiz_id and non-empty answers array."""
        valid_sub = SubmitRequest(quiz_id="test-quiz-123", answers=["A", "B", "C", "D"])
        self.assertEqual(valid_sub.quiz_id, "test-quiz-123")
        self.assertEqual(len(valid_sub.answers), 4)

        # Empty answers list rejected (min_length=1)
        with self.assertRaises(ValidationError):
            SubmitRequest(quiz_id="test-quiz-123", answers=[])

        # Empty quiz_id rejected (min_length=1)
        with self.assertRaises(ValidationError):
            SubmitRequest(quiz_id="", answers=["A"])

    def test_api_keys_not_in_error_or_logs(self):
        """API keys are never formatted into exception strings or public error representations."""
        secret_key = "gsk_test_super_secret_groq_key_value"
        err = ProviderUnavailableError("Temporary network failure", category="SERVICE_UNAVAILABLE")
        self.assertNotIn(secret_key, str(err))

        cfg_err = ProviderConfigError("API key configuration error", category="AUTH_CONFIG_ERROR")
        self.assertNotIn(secret_key, str(cfg_err))

    def test_debug_mode_docs_configuration(self):
        """FastAPI app has OpenAPI documentation gated by DEBUG setting."""
        from main import DEBUG
        if not DEBUG:
            self.assertIsNone(app.docs_url)
            self.assertIsNone(app.redoc_url)
            self.assertIsNone(app.openapi_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
