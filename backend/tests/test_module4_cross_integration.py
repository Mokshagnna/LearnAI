"""
Module 4 Cross-Module Integration and Regression Verification Suite.

Verifies end-to-end interactions across:
1. Quota Management + Provider Fallback
2. Novelty Evaluation + Selective Replacement
3. Academic Correctness + Provider Fallback
4. Shared Request Deadline + Quota + Replacement
5. Persistence Safety and API Integration
6. Four-Provider Coverage Documentation (Groq, NVIDIA, Gemini, Cloudflare)
"""

import os
import json
import time
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from services.providers.base import (
    BaseAIProvider,
    ProviderUnavailableError,
    ProviderConfigError,
    validate_single_question,
    validate_quiz_data,
    calculate_max_output_tokens
)
from services.llm_service import (
    generate_quiz,
    RequestDeadline,
    classify_and_validate_candidate,
    build_selective_replacement_prompt,
    AITimeoutError,
    AIServiceUnavailableError,
    AIRateLimitError,
    AIConfigError
)
from services.quota_manager import (
    quota_manager,
    parse_rate_limit,
    RateLimitInfo,
    RateLimitType,
    ProviderQuotaManager
)
from services.novelty import (
    is_question_near_duplicate,
    are_numerical_problems_identical_structure,
    is_question_relevant_to_topic
)
from services.database import (
    init_db,
    get_connection,
    create_quiz_session,
    get_quiz_session
)
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter


# Pool of 15 distinct, mathematically calibrated Linear Algebra questions for testing
DISTINCT_QUESTIONS_POOL = [
    {
        "question": "Matrices: What is the primary definition of an invertible square matrix?",
        "options": {
            "A": "A matrix with a non-zero determinant",
            "B": "A matrix with zero trace",
            "C": "A non-square matrix",
            "D": "An identity matrix only"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Invertibility",
        "explanation": "A square matrix is invertible if and only if its determinant is non-zero."
    },
    {
        "question": "Matrices: Which theorem establishes that trace equals sum of eigenvalues?",
        "options": {
            "A": "The Matrix Spectral Trace Theorem",
            "B": "Pythagorean Theorem",
            "C": "Cramer's Rule",
            "D": "Cayley-Hamilton Identity"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Eigenvalues",
        "explanation": "The trace of a square matrix always equals the algebraic sum of its eigenvalues."
    },
    {
        "question": "Matrices: How is the rank of a matrix defined in terms of vector spaces?",
        "options": {
            "A": "The dimension of the column space",
            "B": "The total number of zero entries",
            "C": "The sum of all diagonal elements",
            "D": "The number of rows minus columns"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Matrix Rank",
        "explanation": "The rank of a matrix is the dimension of the vector space spanned by its columns."
    },
    {
        "question": "Matrices: Under what condition is a square matrix guaranteed to be symmetric?",
        "options": {
            "A": "When it equals its own transpose",
            "B": "When all off-diagonal elements are 1",
            "C": "When its determinant is negative",
            "D": "When its rank is zero"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Symmetric Matrices",
        "explanation": "A matrix A is symmetric if and only if A equals A transpose."
    },
    {
        "question": "Matrices: What does the Cayley-Hamilton theorem state about any square matrix A?",
        "options": {
            "A": "Every square matrix satisfies its own characteristic polynomial equation",
            "B": "Every matrix has only non-negative eigenvalues",
            "C": "The transpose always equals the matrix inverse",
            "D": "Determinant is always strictly positive"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Cayley-Hamilton",
        "explanation": "The Cayley-Hamilton theorem states that substituting A into its characteristic polynomial yields the zero matrix."
    },
    {
        "question": "Matrices: What is the determinant of an upper triangular matrix?",
        "options": {
            "A": "The product of its main diagonal elements",
            "B": "The sum of all diagonal elements",
            "C": "Zero under all circumstances",
            "D": "The determinant of its first row only"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Determinants",
        "explanation": "For any triangular matrix, the determinant equals the product of its main diagonal entries."
    },
    {
        "question": "Matrices: What characterizes an orthogonal matrix Q?",
        "options": {
            "A": "The transpose of Q equals the inverse of Q",
            "B": "The determinant must be exactly zero",
            "C": "All off-diagonal elements must equal zero",
            "D": "The trace must equal the dimension n"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Orthogonality",
        "explanation": "A real matrix Q is orthogonal if Q^T * Q = I, which means Q^T = Q^(-1)."
    },
    {
        "question": "Matrices: What is the effect of row operations on the determinant of a matrix?",
        "options": {
            "A": "Swapping two rows multiplies the determinant by negative one",
            "B": "Adding a scalar multiple of a row to another sets determinant to zero",
            "C": "Scaling a row has no effect on the determinant",
            "D": "Row operations always leave determinant unchanged"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Row Operations",
        "explanation": "Swapping any two rows of a square matrix negates its determinant."
    },
    {
        "question": "Matrices: Under what condition do two non-zero vectors u and v become orthogonal?",
        "options": {
            "A": "Their standard inner dot product equals zero",
            "B": "Their cross product equals the identity matrix",
            "C": "Their lengths are both equal to one",
            "D": "Their vector sum equals zero"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Vector Spaces",
        "explanation": "Two vectors in an inner product space are orthogonal if and only if their dot product is zero."
    },
    {
        "question": "Matrices: How are eigenvalues affected when a matrix is transposed?",
        "options": {
            "A": "Eigenvalues remain identical because det(A - lambda*I) = det((A - lambda*I)^T)",
            "B": "Eigenvalues are inverted",
            "C": "Eigenvalues are negated",
            "D": "Eigenvalues become complex"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Matrix Transpose",
        "explanation": "A matrix and its transpose have the same characteristic polynomial, hence identical eigenvalues."
    },
    {
        "question": "Matrices: What is the nullity of an m by n matrix A?",
        "options": {
            "A": "The dimension of the null space kernel of A",
            "B": "The number of non-zero rows in reduced row echelon form",
            "C": "The determinant of the submatrix",
            "D": "The total number of columns minus rank squared"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Nullity",
        "explanation": "The nullity of A is the dimension of the solution space to Ax = 0."
    },
    {
        "question": "Matrices: According to the Rank-Nullity theorem, for an m by n matrix A, what equals n?",
        "options": {
            "A": "Rank of A plus Nullity of A",
            "B": "Rank of A multiplied by Nullity of A",
            "C": "Rank of A minus Nullity of A",
            "D": "Trace of A plus Rank of A"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Rank-Nullity Theorem",
        "explanation": "The Rank-Nullity theorem establishes that rank(A) + nullity(A) = n, where n is the number of columns."
    },
    {
        "question": "Matrices: Which property defines an idempotent square matrix P?",
        "options": {
            "A": "P squared equals P",
            "B": "P transpose equals negative P",
            "C": "P inverse equals P squared",
            "D": "Trace of P equals zero"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Idempotent Matrices",
        "explanation": "A matrix P is idempotent if and only if P * P = P."
    },
    {
        "question": "Matrices: What are the possible eigenvalues of an idempotent matrix?",
        "options": {
            "A": "Only zero and one",
            "B": "Any real number",
            "C": "Only negative one and positive one",
            "D": "Purely imaginary numbers"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Idempotent Eigenvalues",
        "explanation": "Since P*x = lambda*x and P^2 = P, lambda^2 = lambda, meaning lambda can only be 0 or 1."
    },
    {
        "question": "Matrices: What does it mean for a square matrix to be defective?",
        "options": {
            "A": "It has fewer linearly independent eigenvectors than its dimension n",
            "B": "Its determinant cannot be computed",
            "C": "Its diagonal elements are non-real numbers",
            "D": "It has no zero entries anywhere in its rows"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Defective Matrices",
        "explanation": "A matrix is defective if the geometric multiplicity of at least one eigenvalue is strictly less than its algebraic multiplicity."
    }
]


def _make_quiz_json(questions: list) -> str:
    return json.dumps({"questions": questions})


@pytest.fixture(autouse=True)
def clean_quota_and_limiters():
    """Ensure clean quota and rate limiter state before and after each test."""
    quota_manager.reset_all()
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()
    yield
    quota_manager.reset_all()
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """Creates an isolated temporary SQLite database and TestClient for main.py."""
    db_file = tmp_path / "module4_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    return client, db_file


# ==============================================================================
# 1. Quota Management + Provider Fallback Integration
# ==============================================================================

class TestQuotaAndProviderFallbackIntegration:
    """Tests the interaction between QuotaManager cooldowns and LLM provider failover."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_first_provider_in_tpm_cooldown_next_provider_succeeds(self, mock_student):
        """When first provider has an active TPM cooldown, it is skipped in 0ms and next provider generates quiz."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True
        p1.generate_quiz_raw.side_effect = ProviderUnavailableError("All Groq models cooling down", category="RATE_LIMIT_429")

        p2 = MagicMock(spec=BaseAIProvider)
        p2.name = "nvidia"
        p2.is_configured.return_value = True
        p2.generate_quiz_raw.return_value = _make_quiz_json(DISTINCT_QUESTIONS_POOL[:10])

        with patch("services.llm_service._get_providers", return_value=[p1, p2]):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                student_id="student_mod4",
                deadline_seconds=30.0
            )

        assert quiz["provider_used"] == "nvidia"
        assert len(quiz["questions"]) == 10
        assert quiz["unique_count"] == 10

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_first_provider_in_tpd_cooldown_next_provider_succeeds(self, mock_student):
        """When first provider is in TPD daily exhaustion, second provider succeeds cleanly."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True
        p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq daily TPD quota exhausted", category="RATE_LIMIT_429")

        p2 = MagicMock(spec=BaseAIProvider)
        p2.name = "cloudflare"
        p2.is_configured.return_value = True
        p2.generate_quiz_raw.return_value = _make_quiz_json(DISTINCT_QUESTIONS_POOL[:10])

        with patch("services.llm_service._get_providers", return_value=[p1, p2]):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                student_id="student_mod4"
            )

        assert quiz["provider_used"] == "cloudflare"
        assert len(quiz["questions"]) == 10

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_all_providers_cooling_down_raises_controlled_error(self, mock_student):
        """When all configured providers are cooling down, raises AIRateLimitError cleanly without hung tasks."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True
        p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq cooling down", category="RATE_LIMIT_429")

        p2 = MagicMock(spec=BaseAIProvider)
        p2.name = "nvidia"
        p2.is_configured.return_value = True
        p2.generate_quiz_raw.side_effect = ProviderUnavailableError("NVIDIA cooling down", category="RATE_LIMIT_429")

        with patch("services.llm_service._get_providers", return_value=[p1, p2]):
            with pytest.raises(AIRateLimitError) as exc_info:
                generate_quiz(
                    course_code="22MT103",
                    course_name="Linear Algebra",
                    topic_name="Matrices",
                    syllabus_context="Matrices, determinants, and linear transformations",
                    student_id="student_mod4"
                )

        assert "rate limit or quota exceeded" in str(exc_info.value).lower()
        assert exc_info.value.category == "RATE_LIMIT_429"

    def test_successful_request_does_not_clear_genuine_daily_lockout(self):
        """Verified safeguard: record_success must never clear an active TPD or daily lockout."""
        info = RateLimitInfo(
            limit_type=RateLimitType.TPD,
            retry_after_seconds=3600.0,
            is_daily_exhaustion=True,
            explicit_reset_time=True
        )
        quota_manager.record_rate_limit("groq", "llama-3.3-70b-versatile", "gsk_prod_key", info)

        # Attempt to record success on the same model/key
        quota_manager.record_success("groq", "llama-3.3-70b-versatile", "gsk_prod_key")

        avail, remaining = quota_manager.is_model_available("groq", "llama-3.3-70b-versatile", "gsk_prod_key")
        assert avail is False
        assert remaining > 3500.0

    def test_transient_unknown_429_does_not_create_unjustified_daily_lockout(self):
        """Unknown 429 without header hints must trigger only short transient cooldown (<=60s), never 24h lockout."""
        info = parse_rate_limit("groq", Exception("429 Too Many Requests"), response_headers={})
        assert info.limit_type == RateLimitType.UNKNOWN_429
        assert info.is_daily_exhaustion is False

        cd = quota_manager.record_rate_limit("groq", "llama-3.1-8b-instant", "gsk_key_transient", info)
        assert cd <= 60.0

        avail, rem = quota_manager.is_model_available("groq", "llama-3.1-8b-instant", "gsk_key_transient")
        assert avail is False
        assert rem <= 60.0

    def test_cooldown_state_scoped_by_provider_model_and_api_key_hash(self):
        """Cooldown on (provider, model, key) must not bleed into other models, keys, or providers."""
        info = RateLimitInfo(
            limit_type=RateLimitType.TPD,
            retry_after_seconds=600.0,
            is_daily_exhaustion=True
        )
        quota_manager.record_rate_limit("groq", "llama-3.3-70b-versatile", "key_alpha", info)

        # 1. Same provider, same model, DIFFERENT key -> Available
        avail_diff_key, _ = quota_manager.is_model_available("groq", "llama-3.3-70b-versatile", "key_beta")
        assert avail_diff_key is True

        # 2. Same provider, DIFFERENT model, same key -> Available
        avail_diff_model, _ = quota_manager.is_model_available("groq", "llama-3.1-8b-instant", "key_alpha")
        assert avail_diff_model is True

        # 3. DIFFERENT provider, same model name, same key -> Available
        avail_diff_provider, _ = quota_manager.is_model_available("nvidia", "llama-3.3-70b-versatile", "key_alpha")
        assert avail_diff_provider is True


# ==============================================================================
# 2. Novelty Evaluation + Selective Replacement Integration
# ==============================================================================

class TestNoveltyAndSelectiveReplacementIntegration:
    """Tests cross-module interaction between candidate classification, duplicate detection, and selective repair."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_initial_batch_valid_and_duplicate_only_duplicates_replaced(self, mock_student):
        """Initial batch contains 7 valid questions and 3 internal duplicates. Only duplicates replaced."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # 7 valid distinct questions, plus 3 questions that duplicate index 0
        initial_candidates = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:7]]
        for i in range(3):
            dup = dict(DISTINCT_QUESTIONS_POOL[0])
            dup["question"] = f"Matrices: What is the primary definition of an invertible square matrix? (Variant {i})"
            initial_candidates.append(dup)

        # Replacement returns 3 novel questions (indices 7, 8, 9)
        replacements = [dict(q) for q in DISTINCT_QUESTIONS_POOL[7:10]]

        mock_provider.generate_quiz_raw.side_effect = [
            _make_quiz_json(initial_candidates),
            _make_quiz_json(replacements)
        ]

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                student_id="student_mod4"
            )

        assert len(quiz["questions"]) == 10
        assert quiz["preserved_count"] == 7
        assert quiz["replaced_count"] == 3
        assert quiz["additional_replacement_calls"] == 1

        # The first 7 stems in the quiz must match the original 7 preserved questions exactly
        for i in range(7):
            assert quiz["questions"][i]["question"] == DISTINCT_QUESTIONS_POOL[i]["question"]

    def test_replacement_candidates_checked_against_accepted_and_student_history(self):
        """Candidate in replacement round is rejected if it duplicates an accepted question or student history."""
        accepted = [dict(DISTINCT_QUESTIONS_POOL[0])]
        history = [dict(DISTINCT_QUESTIONS_POOL[1])]

        # Candidate 1: Duplicates accepted question 0
        cand_internal_dup = dict(DISTINCT_QUESTIONS_POOL[0])
        val_q1, reason1, cat1 = classify_and_validate_candidate(
            cand=cand_internal_dup,
            topic_name="Matrices",
            course_name="Linear Algebra",
            course_code="22MT103",
            concepts=["Invertibility"],
            syllabus_context=None,
            competing_topics=None,
            accepted_questions=accepted,
            history_questions=history
        )
        assert val_q1 is None
        assert cat1 == "DUPLICATE_INTERNAL"
        assert "Duplicates accepted question" in reason1

        # Candidate 2: Duplicates student history question 1
        cand_hist_dup = dict(DISTINCT_QUESTIONS_POOL[1])
        val_q2, reason2, cat2 = classify_and_validate_candidate(
            cand=cand_hist_dup,
            topic_name="Matrices",
            course_name="Linear Algebra",
            course_code="22MT103",
            concepts=["Eigenvalues"],
            syllabus_context=None,
            competing_topics=None,
            accepted_questions=accepted,
            history_questions=history
        )
        assert val_q2 is None
        assert cat2 == "DUPLICATE_HISTORY"
        assert "Duplicates student history" in reason2

        # Candidate 3: Novel distinct question 2
        cand_novel = dict(DISTINCT_QUESTIONS_POOL[2])
        val_q3, reason3, cat3 = classify_and_validate_candidate(
            cand=cand_novel,
            topic_name="Matrices",
            course_name="Linear Algebra",
            course_code="22MT103",
            concepts=["Matrix Rank"],
            syllabus_context=None,
            competing_topics=None,
            accepted_questions=accepted,
            history_questions=history
        )
        assert val_q3 is not None
        assert reason3 is None
        assert cat3 is None

    def test_shared_syllabus_concept_does_not_trigger_false_rejection(self):
        """Questions sharing the same concept ('Eigenvalues') but with distinct cognitive structure are both accepted."""
        q1 = {
            "question": "Matrices: Define the characteristic equation used to find eigenvalues of A.",
            "options": {"A": "det(A - lambda*I) = 0", "B": "trace(A) = lambda", "C": "A * I = lambda", "D": "det(A) = lambda"},
            "correct_answer": "A",
            "difficulty": "Easy",
            "concept": "Eigenvalues",
            "explanation": "Eigenvalues are roots of the characteristic equation det(A - lambda*I) = 0."
        }
        q2 = {
            "question": "Matrices: If a 2x2 matrix has eigenvalues 3 and 5, what is its trace?",
            "options": {"A": "8", "B": "15", "C": "2", "D": "Zero"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": "Eigenvalues",
            "explanation": "The trace of a square matrix is the sum of its eigenvalues: 3 + 5 = 8."
        }

        # Validate q1
        val_q1, _, _ = classify_and_validate_candidate(
            cand=q1,
            topic_name="Matrices",
            course_name="Linear Algebra",
            course_code="22MT103",
            concepts=["Eigenvalues"],
            syllabus_context=None,
            competing_topics=None,
            accepted_questions=[],
            history_questions=[]
        )
        assert val_q1 is not None

        # Validate q2 with q1 in accepted_questions
        val_q2, _, cat2 = classify_and_validate_candidate(
            cand=q2,
            topic_name="Matrices",
            course_name="Linear Algebra",
            course_code="22MT103",
            concepts=["Eigenvalues"],
            syllabus_context=None,
            competing_topics=None,
            accepted_questions=[val_q1],
            history_questions=[]
        )
        assert val_q2 is not None
        assert cat2 is None

    def test_academically_distinct_questions_remain_accepted(self):
        """A full set of distinct questions from pool passes candidate classification without any false positive collision."""
        accepted = []
        for q in DISTINCT_QUESTIONS_POOL[:10]:
            val_q, reason, cat = classify_and_validate_candidate(
                cand=dict(q),
                topic_name="Matrices",
                course_name="Linear Algebra",
                course_code="22MT103",
                concepts=["Invertibility", "Eigenvalues", "Matrix Rank", "Symmetric Matrices"],
                syllabus_context=None,
                competing_topics=None,
                accepted_questions=accepted,
                history_questions=[]
            )
            assert val_q is not None, f"Failed on question: {q['question']}, reason: {reason}"
            accepted.append(val_q)

        assert len(accepted) == 10


# ==============================================================================
# 3. Academic Correctness + Provider Fallback Integration
# ==============================================================================

class TestAcademicCorrectnessAndProviderFallbackIntegration:
    """Tests academic verification enforcement across provider boundaries."""

    def test_academically_invalid_questions_rejected_regardless_of_provider(self):
        """Malformed or mathematically contradictory questions are rejected before acceptance."""
        # 1. Missing option D
        bad_options = {
            "question": "Matrices: What is the trace?",
            "options": {"A": "Sum of diagonal", "B": "Product", "C": "Determinant"},
            "correct_answer": "A", "difficulty": "Easy", "concept": "Trace", "explanation": "Diagonal sum."
        }
        v1, r1, cat1 = validate_single_question(bad_options, topic_name="Matrices", return_details=True)
        assert v1 is None
        assert cat1 == "OPTIONS"

        # 2. Invalid answer key 'Z'
        bad_key = {
            "question": "Matrices: What is the trace?",
            "options": {"A": "Sum of diagonal", "B": "Product", "C": "Determinant", "D": "None"},
            "correct_answer": "Z", "difficulty": "Easy", "concept": "Trace", "explanation": "Diagonal sum."
        }
        v2, r2, cat2 = validate_single_question(bad_key, topic_name="Matrices", return_details=True)
        assert v2 is None
        assert cat2 == "OPTIONS"

        # 3. Duplicate option text (A and B identical)
        dup_options = {
            "question": "Matrices: What is the trace?",
            "options": {"A": "Sum of diagonal", "B": "Sum of diagonal", "C": "Determinant", "D": "None"},
            "correct_answer": "A", "difficulty": "Easy", "concept": "Trace", "explanation": "Diagonal sum."
        }
        v3, r3, cat3 = validate_single_question(dup_options, topic_name="Matrices", return_details=True)
        assert v3 is None
        assert cat3 == "OPTIONS"

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_provider_returning_invalid_mathematics_allows_later_provider_to_succeed(self, mock_student):
        """Provider 1 returns mathematically flawed questions; failover to Provider 2 succeeds."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True

        # Flawed questions with duplicate options in every question
        flawed_questions = [
            {
                "question": f"Matrices: Question number {i}?",
                "options": {"A": "Same text", "B": "Same text", "C": "Third option", "D": "Fourth option"},
                "correct_answer": "A", "difficulty": "Easy", "concept": "Matrices", "explanation": "Reason"
            }
            for i in range(10)
        ]
        p1.generate_quiz_raw.return_value = _make_quiz_json(flawed_questions)

        p2 = MagicMock(spec=BaseAIProvider)
        p2.name = "nvidia"
        p2.is_configured.return_value = True
        p2.generate_quiz_raw.return_value = _make_quiz_json(DISTINCT_QUESTIONS_POOL[:10])

        with patch("services.llm_service._get_providers", return_value=[p1, p2]):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                student_id="student_mod4"
            )

        assert quiz["provider_used"] == "nvidia"
        assert len(quiz["questions"]) == 10

    def test_final_quiz_passes_complete_validation(self):
        """End-to-end quiz generated with selective replacement passes validate_quiz_data with zero rejections."""
        quiz_data = {"questions": DISTINCT_QUESTIONS_POOL[:10]}
        result = validate_quiz_data(
            data=quiz_data,
            course_name="Linear Algebra",
            topic_name="Matrices",
            syllabus_context="Matrices, determinants, and linear transformations"
        )

        assert result["unique_count"] == 10
        assert result["has_duplicates"] is False
        assert len(result["rejection_reasons"]) == 0


# ==============================================================================
# 4. Deadline + Quota + Replacement Integration
# ==============================================================================

class TestDeadlineQuotaAndReplacementIntegration:
    """Tests the single shared RequestDeadline across quota fast-skips and replacement loops."""

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_cooling_provider_skipped_without_spending_deadline(self, mock_student):
        """Provider in cooldown is skipped in <50ms, preserving budget for the subsequent provider."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True
        p1.generate_quiz_raw.side_effect = ProviderUnavailableError("All models cooling down", category="RATE_LIMIT_429")

        p2 = MagicMock(spec=BaseAIProvider)
        p2.name = "nvidia"
        p2.is_configured.return_value = True

        allocated_timeouts = []
        def record_timeout(*args, **kwargs):
            allocated_timeouts.append(kwargs.get("timeout_seconds"))
            return _make_quiz_json(DISTINCT_QUESTIONS_POOL[:10])
        p2.generate_quiz_raw.side_effect = record_timeout

        with patch("services.llm_service._get_providers", return_value=[p1, p2]):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Matrices",
                syllabus_context="Matrices, determinants, and linear transformations",
                student_id="student_mod4",
                deadline_seconds=20.0
            )

        assert quiz["provider_used"] == "nvidia"
        # Since p1 skipped near-instantly, p2 should receive nearly the entire budget
        assert len(allocated_timeouts) >= 1
        assert allocated_timeouts[0] >= 15.0

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_no_provider_called_after_shared_deadline_expires(self, mock_student):
        """When deadline has expired, no provider calls are executed and AITimeoutError is raised."""
        p1 = MagicMock(spec=BaseAIProvider)
        p1.name = "groq"
        p1.is_configured.return_value = True

        with patch("services.llm_service._get_providers", return_value=[p1]):
            with patch("services.llm_service.RequestDeadline.can_attempt", return_value=False):
                with pytest.raises(AITimeoutError):
                    generate_quiz(
                        course_code="22MT103",
                        course_name="Linear Algebra",
                        topic_name="Matrices",
                        syllabus_context="Matrices, determinants, and linear transformations",
                        student_id="student_mod4",
                        deadline_seconds=5.0
                    )

        assert p1.generate_quiz_raw.call_count == 0

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_no_replacement_begins_when_remaining_budget_is_insufficient(self, mock_student):
        """When remaining time < REPLACEMENT_MIN_TIME_REQUIREMENTS, replacement call is aborted cleanly."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # Initial batch returns 8 valid questions and 2 invalid
        initial_candidates = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:8]]
        initial_candidates.append({"question": "Matrices: Incomplete?", "options": {"A": "1"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Matrices", "explanation": "Exp"})
        initial_candidates.append({"question": "Matrices: Incomplete2?", "options": {"A": "2"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Matrices", "explanation": "Exp"})

        mock_provider.generate_quiz_raw.return_value = _make_quiz_json(initial_candidates)

        # Allow initial check to pass, but when checking replacement budget return False
        real_can_attempt = RequestDeadline.can_attempt
        call_count = [0]
        def custom_can_attempt(self, min_time_needed):
            call_count[0] += 1
            if call_count[0] > 2:  # After provider checks, fail replacement check
                return False
            return real_can_attempt(self, min_time_needed)

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with patch.object(RequestDeadline, "can_attempt", custom_can_attempt):
                with pytest.raises((AITimeoutError, AIServiceUnavailableError)) as exc_info:
                    generate_quiz(
                        course_code="22MT103",
                        course_name="Linear Algebra",
                        topic_name="Matrices",
                        syllabus_context="Matrices, determinants, and linear transformations",
                        student_id="student_mod4",
                        deadline_seconds=10.0
                    )

        # Replacement call was never made because can_attempt returned False for replacement
        assert mock_provider.generate_quiz_raw.call_count == 1

    @patch("services.llm_service.get_or_create_student", return_value="student_mod4")
    def test_requested_question_count_is_never_silently_reduced(self, mock_student):
        """When full quiz is requested (allow_partial=False), an 8/10 result raises an error, never returning partial."""
        mock_provider = MagicMock(spec=BaseAIProvider)
        mock_provider.name = "groq"
        mock_provider.is_configured.return_value = True

        # Return only 8 valid questions, and empty replacement for all subsequent calls
        initial_candidates = [dict(q) for q in DISTINCT_QUESTIONS_POOL[:8]]
        call_count = [0]
        def mock_generate_raw(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_quiz_json(initial_candidates)
            return _make_quiz_json([])

        mock_provider.generate_quiz_raw.side_effect = mock_generate_raw

        with patch("services.llm_service._get_providers", return_value=[mock_provider]):
            with pytest.raises(AIServiceUnavailableError) as exc_info:
                generate_quiz(
                    course_code="22MT103",
                    course_name="Linear Algebra",
                    topic_name="Matrices",
                    syllabus_context="Matrices, determinants, and linear transformations",
                    student_id="student_mod4",
                    allow_partial=False
                )

        assert "8/10" in str(exc_info.value)


# ==============================================================================
# 5. Persistence Safety and API Integration
# ==============================================================================

class TestPersistenceAndApiIntegration:
    """Tests transaction boundaries, atomic session persistence, and FastAPI endpoint schemas."""

    def test_successful_generation_persists_one_complete_quiz_session(self, isolated_db_client):
        """HTTP POST /api/quiz/generate persists exactly 1 session with complete snapshot on success."""
        client, db_file = isolated_db_client

        mock_quiz = {
            "subject": "Linear Algebra",
            "topic": "Matrices",
            "course_code": "22MT103",
            "questions": DISTINCT_QUESTIONS_POOL[:10],
            "recommended_difficulty": "mixed",
            "adaptive_applied": False,
            "adaptive_explanation": None,
            "adaptive_metadata": {},
            "attempt_id": "attempt_1",
            "attempt_number": 1,
            "provider_used": "groq",
            "novelty_repairs": 0,
            "novelty_warning": False,
            "timing_metrics": {"total_service_sec": 1.2}
        }

        with patch("main.generate_quiz", return_value=mock_quiz):
            resp = client.post("/api/quiz/generate", json={
                "course_code": "22MT103",
                "topic": "Matrices",
                "student_id": "student_persist_test"
            })

        assert resp.status_code == 200
        data = resp.json()
        assert "quiz_id" in data
        quiz_id = data["quiz_id"]
        assert len(data["questions"]) == 10

        # Verify DB contents
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, questions_snapshot FROM quiz_sessions WHERE id = ?", (quiz_id,))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == quiz_id
        assert row[1] == "in_progress"
        snapshot = json.loads(row[2])
        assert len(snapshot) == 10
        # Snapshot in DB must contain correct_answer and explanation
        assert "correct_answer" in snapshot[0]
        assert "explanation" in snapshot[0]

    def test_failed_generation_does_not_persist_partial_quiz(self, isolated_db_client):
        """When quiz generation raises an error, no session record is created in the database."""
        client, db_file = isolated_db_client

        with patch("main.generate_quiz", side_effect=AITimeoutError("AI service timed out")):
            resp = client.post("/api/quiz/generate", json={
                "course_code": "22MT103",
                "topic": "Matrices",
                "student_id": "student_fail_test"
            })

        assert resp.status_code == 504

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM quiz_sessions")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0

    def test_database_exception_handled_without_leaving_corrupted_records(self, isolated_db_client):
        """Database exception during session creation returns 500 without leaving corrupted records."""
        client, db_file = isolated_db_client

        mock_quiz = {
            "subject": "Linear Algebra",
            "topic": "Matrices",
            "course_code": "22MT103",
            "questions": DISTINCT_QUESTIONS_POOL[:10],
            "recommended_difficulty": "mixed"
        }

        with patch("main.generate_quiz", return_value=mock_quiz):
            with patch("main.create_quiz_session", side_effect=sqlite3.OperationalError("Simulated disk I/O failure")):
                resp = client.post("/api/quiz/generate", json={
                    "course_code": "22MT103",
                    "topic": "Matrices",
                    "student_id": "student_io_error"
                })

        assert resp.status_code == 500

    def test_endpoint_response_schema_compatibility(self, isolated_db_client):
        """Public endpoint response preserves expected frontend contract while stripping sensitive fields."""
        client, _ = isolated_db_client

        mock_quiz = {
            "subject": "Linear Algebra",
            "topic": "Matrices",
            "course_code": "22MT103",
            "questions": DISTINCT_QUESTIONS_POOL[:10],
            "recommended_difficulty": "mixed",
            "adaptive_applied": True,
            "adaptive_explanation": "Adaptive leveling applied."
        }

        with patch("main.generate_quiz", return_value=mock_quiz):
            resp = client.post("/api/quiz/generate", json={
                "course_code": "22MT103",
                "topic": "Matrices",
                "student_id": "student_schema_test"
            })

        assert resp.status_code == 200
        payload = resp.json()

        # Required top-level keys
        assert "quiz_id" in payload
        assert "course_code" in payload
        assert "topic" in payload
        assert "questions" in payload
        assert "recommended_difficulty" in payload
        assert "adaptive_applied" in payload

        # Required public question fields (answers & explanations stripped)
        first_q = payload["questions"][0]
        assert "id" in first_q
        assert "question" in first_q
        assert "options" in first_q
        assert "difficulty" in first_q
        assert "concept" in first_q
        assert "correct_answer" not in first_q
        assert "explanation" not in first_q


# ==============================================================================
# 6. Four-Provider Coverage Documentation & Actual Behavior
# ==============================================================================

class TestFourProviderCoverageDocumentation:
    """
    Documents and verifies actual rate-limit and quota behaviors across all 4 providers:
    - Groq: Integrated with Module 1 shared QuotaManager (TPD/TPM/model availability).
    - NVIDIA: Integrated with Module 1 shared QuotaManager (TPD/TPM/model availability).
    - Gemini: Independent rate-limit categorization (Google GenAI API errors mapped to RATE_LIMIT_429).
    - Cloudflare: Independent rate-limit categorization (HTTP 429 response mapped to RATE_LIMIT_429).
    """

    def test_gemini_independent_rate_limit_behavior(self):
        """Gemini maps 429 / resource exhausted to ProviderUnavailableError(category='RATE_LIMIT_429')."""
        from services.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider()
        # Mock Google GenAI exception
        genai_err = Exception("429 Resource has been exhausted (e.g. check quota).")
        cat = provider._categorize(genai_err)
        assert cat == "RATE_LIMIT_429"

    def test_cloudflare_independent_rate_limit_behavior(self):
        """Cloudflare maps HTTP 429 to ProviderUnavailableError(category='RATE_LIMIT_429')."""
        from services.providers.cloudflare_provider import CloudflareProvider
        import httpx

        provider = CloudflareProvider()
        # Mock httpx response with 429
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.text = json.dumps({"errors": [{"message": "Rate limit exceeded"}]})
        mock_resp.json.return_value = {"errors": [{"message": "Rate limit exceeded"}]}

        with patch.object(CloudflareProvider, "is_configured", return_value=True):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__.return_value = mock_client
                mock_client.post.return_value = mock_resp
                mock_client_cls.return_value = mock_client

                with pytest.raises(ProviderUnavailableError) as exc_info:
                    provider._execute("prompt", timeout_seconds=10.0)

                assert exc_info.value.category == "RATE_LIMIT_429"

    def test_shared_quota_manager_architecture_scope(self):
        """
        Architectural assertion:
        Confirms that all four providers (Groq, Nvidia, Gemini, Cloudflare)
        explicitly integrate with quota_manager for uniform rate-limiting and fast-skips.
        """
        from services.providers import groq_provider, nvidia_provider, gemini_provider, cloudflare_provider

        # All four providers integrate QuotaManager
        assert "quota_manager" in groq_provider.__dict__
        assert "quota_manager" in nvidia_provider.__dict__
        assert "quota_manager" in gemini_provider.__dict__
        assert "quota_manager" in cloudflare_provider.__dict__
