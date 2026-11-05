"""
Unit and regression tests for Module 3: Selective Question Replacement & End-to-End Deadline.

Verifies:
1. One invalid question among otherwise valid questions: only one replacement.
2. Multiple invalid questions: only those questions replaced.
3. All valid questions: zero replacement calls.
4. Replacement questions that duplicate another question in the same quiz (rejected as DUPLICATE_INTERNAL).
5. Replacement questions that duplicate student history (rejected as DUPLICATE_HISTORY).
6. Provider timeout during initial generation: controlled AITimeoutError.
7. Deadline reached during replenishment or repair: does not initiate impossible call, raises controlled failure.
8. Provider fallback when sufficient time remains.
9. No fallback after the deadline: skips secondary provider when budget insufficient.
10. Quota cooldown respected under deadline pressure: never bypasses Module 1 cooldown.
11. No partial or corrupt persistence after timeout: database persistence is never invoked on failure.
12. Final question count and academic correctness across all returned questions.
"""

import json
import os
import time
import pytest
from unittest.mock import MagicMock, patch

from services.providers.base import BaseAIProvider, ProviderUnavailableError
from services.llm_service import (
    generate_quiz,
    RequestDeadline,
    AITimeoutError,
    AIServiceUnavailableError,
    AIRateLimitError
)
from services.quota_manager import quota_manager, RateLimitInfo, RateLimitType


# Pool of 15 genuinely distinct linear algebra questions across concepts, cognitive operations, and stems
DISTINCT_QUESTIONS_POOL = [
    {
        "question": "Matrices: What is the primary definition of an invertible square matrix?",
        "options": {"A": "A matrix with a non-zero determinant", "B": "A matrix with zero trace", "C": "A non-square matrix", "D": "An identity matrix only"},
        "correct_answer": "A", "difficulty": "Easy", "concept": "Invertibility",
        "explanation": "A square matrix is invertible if and only if its determinant is non-zero."
    },
    {
        "question": "Matrices: Which of the following theorems establishes that trace equals sum of eigenvalues?",
        "options": {"A": "The Matrix Spectral Trace Theorem", "B": "Pythagorean Theorem", "C": "Cramer's Rule", "D": "Cayley-Hamilton Identity"},
        "correct_answer": "A", "difficulty": "Easy", "concept": "Eigenvalues",
        "explanation": "The trace of a square matrix always equals the algebraic sum of its eigenvalues."
    },
    {
        "question": "Matrices: How is the rank of a matrix defined in terms of vector spaces?",
        "options": {"A": "The dimension of the column space", "B": "The total number of zero entries", "C": "The sum of all diagonal elements", "D": "The number of rows minus columns"},
        "correct_answer": "A", "difficulty": "Easy", "concept": "Matrix Rank",
        "explanation": "The rank of a matrix is the dimension of the vector space spanned by its columns."
    },
    {
        "question": "Matrices: Under what condition is a square matrix guaranteed to be symmetric?",
        "options": {"A": "When it equals its own transpose", "B": "When all off-diagonal elements are 1", "C": "When its determinant is negative", "D": "When its rank is zero"},
        "correct_answer": "A", "difficulty": "Easy", "concept": "Symmetric Matrices",
        "explanation": "A matrix A is symmetric if and only if A equals A transpose."
    },
    {
        "question": "Matrices: In Gaussian elimination, what is the effect of row swap on the determinant?",
        "options": {"A": "It multiplies the determinant by -1", "B": "It sets determinant to zero", "C": "It leaves determinant unchanged", "D": "It doubles the determinant"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Determinants",
        "explanation": "Swapping two rows of a matrix negates the determinant."
    },
    {
        "question": "Matrices: What geometric subspace is mapped to the zero vector by linear mapping A?",
        "options": {"A": "The null space or kernel", "B": "The orthogonal complement of range", "C": "The eigenspace with eigenvalue 1", "D": "The row space"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Null Space",
        "explanation": "The kernel (null space) contains all vectors v such that A v = 0."
    },
    {
        "question": "Matrices: What is the product of an orthogonal matrix Q with its transpose Q^T?",
        "options": {"A": "The identity matrix I", "B": "The zero matrix", "C": "The scalar determinant", "D": "A diagonal matrix of eigenvalues"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Orthogonal Matrices",
        "explanation": "By definition of orthogonal matrices, Q times Q transpose equals the identity matrix."
    },
    {
        "question": "Matrices: According to the Rank-Nullity Theorem, what is rank(A) + nullity(A) for m x n matrix?",
        "options": {"A": "n, the number of columns", "B": "m, the number of rows", "C": "m times n", "D": "The determinant of A"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Rank-Nullity",
        "explanation": "The Rank-Nullity Theorem states that dim(col space) + dim(null space) = n."
    },
    {
        "question": "Matrices: Under what specific condition is a real symmetric matrix positive definite?",
        "options": {"A": "All of its eigenvalues are strictly positive", "B": "Its trace is positive", "C": "All entries are non-zero", "D": "Its determinant equals 1"},
        "correct_answer": "A", "difficulty": "Hard", "concept": "Positive Definite",
        "explanation": "A symmetric matrix is positive definite if and only if all its eigenvalues are strictly positive."
    },
    {
        "question": "Matrices: How does scalar multiplication of an n x n matrix by constant c scale its determinant?",
        "options": {"A": "It scales by c raised to power n", "B": "It scales linearly by c", "C": "It scales by c squared", "D": "Determinant is invariant to scalar scaling"},
        "correct_answer": "A", "difficulty": "Hard", "concept": "Determinant Scaling",
        "explanation": "Scaling an n x n matrix by scalar c multiplies the determinant by c^n."
    },
    {
        "question": "Matrices: Which algorithm computes the singular value decomposition (SVD) of rectangular matrix A?",
        "options": {"A": "Golub-Kahan bidiagonalization algorithm", "B": "Dijkstra shortest path", "C": "Newton-Raphson method", "D": "Kruskal minimum spanning tree"},
        "correct_answer": "A", "difficulty": "Hard", "concept": "SVD",
        "explanation": "SVD is commonly computed using Golub-Kahan bidiagonalization followed by QR iterations."
    },
    {
        "question": "Matrices: What is the characteristic polynomial of a 2x2 matrix with trace T and determinant D?",
        "options": {"A": "lambda^2 - T*lambda + D = 0", "B": "lambda^2 + T*lambda - D = 0", "C": "lambda^2 - D*lambda + T = 0", "D": "lambda^2 + (T+D) = 0"},
        "correct_answer": "A", "difficulty": "Hard", "concept": "Characteristic Equation",
        "explanation": "For any 2x2 matrix, the characteristic equation is det(A - lambda*I) = lambda^2 - tr(A)*lambda + det(A) = 0."
    },
    {
        "question": "Matrices: Under what condition does matrix A have full column rank?",
        "options": {"A": "When its columns are linearly independent", "B": "When all rows are identical", "C": "When its determinant is zero", "D": "When nullity exceeds 1"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Linear Independence",
        "explanation": "A matrix has full column rank when all its column vectors are linearly independent."
    },
    {
        "question": "Matrices: What is the inverse of an upper triangular invertible matrix?",
        "options": {"A": "An upper triangular matrix", "B": "A lower triangular matrix", "C": "A skew-symmetric matrix", "D": "A strictly diagonal matrix"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Triangular Matrices",
        "explanation": "The inverse of an upper triangular invertible matrix is always upper triangular."
    },
    {
        "question": "Matrices: How are eigenvalues affected when a matrix is transposed?",
        "options": {"A": "Eigenvalues remain identical because det(A - lambda*I) = det((A - lambda*I)^T)", "B": "Eigenvalues are inverted", "C": "Eigenvalues are negated", "D": "Eigenvalues become complex"},
        "correct_answer": "A", "difficulty": "Medium", "concept": "Matrix Transpose",
        "explanation": "A matrix and its transpose have the same characteristic polynomial, hence identical eigenvalues."
    }
]


def _make_quiz_json(questions: list) -> str:
    return json.dumps({"questions": questions})


class TestSelectiveReplacement:
    """Tests selective question replacement behavior."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_one_invalid_question_only_one_replacement(self, mock_student):
        """Test 1: One invalid question among otherwise valid questions triggers exactly one replacement."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # Initial batch: 9 valid from pool, 1 academically invalid at index 4
        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        initial_questions[4] = {
            "question": "Matrices: Which matrix is symmetric?",
            "options": {
                "A": "[[1, 2], [3, 4]]",
                "B": "[[0, 1], [2, 0]]",
                "C": "[[1, 0], [0, 1]]",
                "D": "[[5, 6], [7, 8]]"
            },
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": "Symmetric Matrices",
            "explanation": "Option A is symmetric."
        }

        # Replacement call returns 1 valid question (index 10 from pool)
        replacement_questions = [dict(DISTINCT_QUESTIONS_POOL[10])]

        mock_provider.generate_quiz_raw.side_effect = [
            _make_quiz_json(initial_questions),
            _make_quiz_json(replacement_questions)
        ]

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        assert mock_provider.generate_quiz_raw.call_count == 2
        assert quiz["initial_provider_calls"] == 1
        assert quiz["additional_replacement_calls"] == 1
        assert quiz["total_provider_calls"] == 2
        assert quiz["preserved_count"] == 9
        assert quiz["replaced_count"] == 1
        assert len(quiz["replacement_records"]) >= 1

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_multiple_invalid_questions_only_those_replaced(self, mock_student):
        """Test 2: Multiple invalid questions (3 invalid): only those 3 are replaced, 7 preserved."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # Initial batch: 7 valid, 3 invalid
        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        initial_questions[1] = {
            "question": "Matrices: Which matrix is symmetric?",
            "options": {
                "A": "[[1, 2], [3, 4]]",
                "B": "[[0, 1], [2, 0]]",
                "C": "[[1, 0], [0, 1]]",
                "D": "[[5, 6], [7, 8]]"
            },
            "correct_answer": "A",
            "difficulty": "Easy",
            "concept": "Symmetric Matrices",
            "explanation": "Option A is symmetric."
        }
        initial_questions[5] = {"question": "Too short", "options": {"A": "1"}}  # Malformed schema
        initial_questions[8] = {
            "question": "What is the primary function of an operating system kernel?",
            "options": {"A": "Process scheduling", "B": "Web browsing", "C": "Word processing", "D": "Video streaming"},
            "correct_answer": "A",
            "concept": "Operating Systems",
            "explanation": "Kernel manages processes."
        }

        # Replacement call returns 3 valid questions (indices 10, 11, 12 from pool)
        replacements = [dict(DISTINCT_QUESTIONS_POOL[10]), dict(DISTINCT_QUESTIONS_POOL[11]), dict(DISTINCT_QUESTIONS_POOL[12])]

        mock_provider.generate_quiz_raw.side_effect = [
            _make_quiz_json(initial_questions),
            _make_quiz_json(replacements)
        ]

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        assert mock_provider.generate_quiz_raw.call_count == 2
        assert quiz["preserved_count"] == 7
        assert quiz["replaced_count"] == 3
        assert quiz["total_provider_calls"] == 2


    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_all_valid_questions_zero_replacement_calls(self, mock_student):
        """Test 3: All valid questions: zero replacement calls made."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        mock_provider.generate_quiz_raw.return_value = _make_quiz_json(initial_questions)

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        assert mock_provider.generate_quiz_raw.call_count == 1
        assert quiz["initial_provider_calls"] == 1
        assert quiz["additional_replacement_calls"] == 0
        assert quiz["total_provider_calls"] == 1
        assert quiz["preserved_count"] == 10
        assert quiz["replaced_count"] == 0

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_replacement_questions_duplicate_within_quiz_rejected(self, mock_student):
        """Test 4: Replacement questions that duplicate another question in the same quiz are rejected."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        initial_questions[9] = {"question": "Bad question", "options": {}}  # Invalid

        # Round 1 replacement candidate duplicates question 0 from the accepted set
        dup_candidate = dict(DISTINCT_QUESTIONS_POOL[0])
        # Round 2 replacement candidate is genuinely novel (index 10)
        valid_candidate = dict(DISTINCT_QUESTIONS_POOL[10])

        mock_provider.generate_quiz_raw.side_effect = [
            _make_quiz_json(initial_questions),
            _make_quiz_json([dup_candidate]),
            _make_quiz_json([valid_candidate])
        ]

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        assert mock_provider.generate_quiz_raw.call_count == 3
        dup_records = [r for r in quiz["replacement_records"] if r.get("category") == "DUPLICATE_INTERNAL"]
        assert len(dup_records) >= 1

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_replacement_questions_duplicate_history_rejected(self, mock_student):
        """Test 5: Replacement questions that duplicate student attempt history are rejected."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        history_q = dict(DISTINCT_QUESTIONS_POOL[10])

        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        initial_questions[9] = {"question": "Bad question", "options": {}}  # Invalid

        # Round 1 candidate matches history_q
        hist_dup_cand = dict(DISTINCT_QUESTIONS_POOL[10])
        # Round 2 candidate is novel (index 11)
        novel_cand = dict(DISTINCT_QUESTIONS_POOL[11])

        mock_provider.generate_quiz_raw.side_effect = [
            _make_quiz_json(initial_questions),
            _make_quiz_json([hist_dup_cand]),
            _make_quiz_json([novel_cand])
        ]

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with patch("services.llm_service.get_question_history_full", return_value=[history_q]):
                quiz = generate_quiz(
                    course_code="22MA101",
                    course_name="Linear Algebra",
                    topic_name="Matrices",
                    syllabus_context="Matrices, determinants, and linear transformations",
                    num_questions=10
                )

        assert len(quiz["questions"]) == 10
        assert mock_provider.generate_quiz_raw.call_count == 3
        hist_records = [r for r in quiz["replacement_records"] if r.get("category") == "DUPLICATE_HISTORY"]
        assert len(hist_records) >= 1


class TestSharedDeadlineAndBudgets:
    """Tests shared end-to-end deadline and timeout behaviors."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_provider_timeout_during_initial_generation(self, mock_student):
        """Test 6: Provider timeout during initial generation raises controlled AITimeoutError."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True
        mock_provider.generate_quiz_raw.side_effect = ProviderUnavailableError("Gateway timeout (504)", category="TIMEOUT")

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with pytest.raises(AITimeoutError) as exc_info:
                generate_quiz(
                    course_code="22MA101",
                    course_name="Linear Algebra",
                    topic_name="Matrices",
                    syllabus_context="Matrices, determinants",
                    num_questions=10
                )
            assert isinstance(exc_info.value, AITimeoutError)

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_deadline_reached_during_replenishment_does_not_begin_impossible_call(self, mock_student):
        """Test 7: Deadline reached during replenishment: does not begin call when budget insufficient."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # Initial call returns 8 valid questions, needing 2 replacements
        initial_questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:8]]
        mock_provider.generate_quiz_raw.return_value = _make_quiz_json(initial_questions)

        # Mock time.perf_counter such that after initial generation, remaining time <= 1.0s
        seq = [0.0, 0.1, 0.2, 14.5, 14.6, 14.7, 14.8, 14.9, 15.0]
        idx = [0]
        def mock_perf():
            val = seq[min(idx[0], len(seq) - 1)]
            idx[0] += 1
            return val

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with patch("time.perf_counter", side_effect=mock_perf):
                with pytest.raises(AITimeoutError):
                    generate_quiz(
                        course_code="22MA101",
                        course_name="Linear Algebra",
                        topic_name="Matrices",
                        syllabus_context="Matrices, determinants",
                        num_questions=10,
                        deadline_seconds=15.0
                    )

        assert mock_provider.generate_quiz_raw.call_count == 1

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_provider_fallback_when_sufficient_time_remains(self, mock_student):
        """Test 8: Primary provider fails transiently -> falls back to secondary provider when sufficient time remains."""
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq 503 Service Unavailable", category="SERVICE_UNAVAILABLE")

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.return_value = _make_quiz_json([dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]])

        with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants",
                num_questions=10,
                deadline_seconds=25.0
            )

        assert quiz["provider_used"] == "nvidia"
        assert len(quiz["questions"]) == 10
        assert mock_groq.generate_quiz_raw.call_count == 1
        assert mock_nvidia.generate_quiz_raw.call_count == 1

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_no_fallback_after_deadline(self, mock_student):
        """Test 9: Primary fails, but remaining deadline is less than secondary provider's min required time -> secondary skipped."""
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq failed", category="TIMEOUT")

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"  # Requires 10.0s min
        mock_gemini.is_configured.return_value = True

        seq = [0.0, 0.1, 11.0, 11.1, 11.2, 11.3, 11.4]
        idx = [0]
        def mock_perf():
            val = seq[min(idx[0], len(seq) - 1)]
            idx[0] += 1
            return val

        with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_gemini]):
            with patch("time.perf_counter", side_effect=mock_perf):
                with pytest.raises(AITimeoutError):
                    generate_quiz(
                        course_code="22MA101",
                        course_name="Linear Algebra",
                        topic_name="Matrices",
                        syllabus_context="Matrices, determinants",
                        num_questions=10,
                        deadline_seconds=15.0
                    )

        assert mock_groq.generate_quiz_raw.call_count == 1
        assert mock_gemini.generate_quiz_raw.call_count == 0

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_quota_cooldown_respected_under_deadline_pressure(self, mock_student, monkeypatch):
        """Test 10: Provider in Module 1 quota cooldown is respected and skipped, never bypassed."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_module3")
        from services.providers.groq_provider import GroqProvider

        quota_manager.record_rate_limit(
            "groq",
            "llama-3.3-70b-versatile",
            "test_groq_key_module3",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )
        quota_manager.record_rate_limit(
            "groq",
            "llama-3.1-8b-instant",
            "test_groq_key_module3",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )

        mock_groq = GroqProvider()

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.return_value = _make_quiz_json([dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]])

        with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants",
                num_questions=10,
                deadline_seconds=15.0
            )

        assert quiz["provider_used"] == "nvidia"
        assert len(quiz["questions"]) == 10
        assert mock_nvidia.generate_quiz_raw.call_count == 1


class TestPersistenceSafetyAndCorrectness:
    """Tests that timeout/failures never persist partial quizzes, and final quiz is complete & valid."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_no_partial_or_corrupt_persistence_after_timeout(self, mock_student):
        """Test 11: Timeout or failure never causes partial or corrupt quiz session persistence."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True
        mock_provider.generate_quiz_raw.side_effect = ProviderUnavailableError("Timeout", category="TIMEOUT")

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with patch("services.database.create_quiz_session") as mock_persist:
                with pytest.raises(AITimeoutError):
                    generate_quiz(
                        course_code="22MA101",
                        course_name="Linear Algebra",
                        topic_name="Matrices",
                        syllabus_context="Matrices, determinants",
                        num_questions=10
                    )
                assert mock_persist.call_count == 0

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_final_question_count_and_academic_correctness(self, mock_student):
        """Test 12: Final quiz guarantees exact requested count and 100% academic verification passes."""
        from services.academic_verifier import verify_academic_correctness

        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        mock_provider.generate_quiz_raw.return_value = _make_quiz_json(questions)

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MA101",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        for q in quiz["questions"]:
            is_valid, reason = verify_academic_correctness(q)
            assert is_valid is True, f"Question failed academic verification: {reason}"
            assert len(q["options"]) == 4
            assert q["correct_answer"] in ("A", "B", "C", "D")

    @patch("services.llm_service.get_or_create_student", return_value="student_mod3")
    def test_gemini_skipped_for_budget_allows_later_eligible_provider(self, mock_student):
        """Test 13: When Gemini is skipped because remaining deadline < 10.0s, the loop continues to later eligible providers."""
        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True

        mock_cloudflare = MagicMock(spec=BaseAIProvider)
        mock_cloudflare.name = "cloudflare"
        mock_cloudflare.is_configured.return_value = True

        questions = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:10]]
        mock_cloudflare.generate_quiz_raw.return_value = _make_quiz_json(questions)

        # Simulate deadline having 8.5s remaining (less than Gemini's 10.0s requirement, but more than Cloudflare's 3.0s requirement)
        fake_deadline = RequestDeadline(overall_deadline=8.5, start_time=time.perf_counter())

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_cloudflare]):
            with patch("services.llm_service.RequestDeadline", return_value=fake_deadline):
                quiz = generate_quiz(
                    course_code="22MA101",
                    course_name="Linear Algebra",
                    topic_name="Matrices",
                    syllabus_context="Matrices, determinants",
                    num_questions=10
                )

        # Assert Gemini was skipped without calling generate_quiz_raw
        assert mock_gemini.generate_quiz_raw.call_count == 0
        # Assert Cloudflare ran successfully and provided the quiz
        assert mock_cloudflare.generate_quiz_raw.call_count >= 1
        assert quiz["provider_used"] == "cloudflare"
        assert len(quiz["questions"]) == 10
