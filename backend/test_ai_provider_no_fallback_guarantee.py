"""
test_ai_provider_no_fallback_guarantee.py
=========================================
Automated test suite proving that LearnAI strictly enforces fresh AI generation
and NEVER falls back to curated questions, static banks, or stored questions.

Verifies:
1. A successful AI response produces a valid, fresh quiz.
2. Groq failure triggers the existing fallback sequence (Groq -> NVIDIA -> Gemini).
3. Groq and NVIDIA failure triggers Gemini fallback.
4. When all providers fail, a controlled AIServiceUnavailableError (503) or AIConfigError is produced.
5. Zero curated questions or static question banks are returned when all providers fail.
6. No silent reuse of previously generated or stored database questions.
7. API response returns HTTP 503 / 500 with controlled error payload (never a 200 with fake questions).
8. Existing quiz submission and scoring behavior remains completely intact.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from services.database import init_db
from services.auth_utils import sign_student_identity
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.llm_service import (
    generate_quiz,
    AIError,
    AIServiceUnavailableError,
    AIConfigError,
    _load_local_demo_questions
)


@pytest.fixture(autouse=True)
def reset_limiters():
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()
    yield
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_no_fallback.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    return TestClient(app)


def _make_mock_ai_questions(count=10, topic="Linear Algebra Concepts"):
    """Generate distinct valid questions with completely distinct vocabulary to satisfy all deduplication rules."""
    templates = [
        ("Define the condition under which a transformation preserves vector addition.", "Linearity holds over vector sums", "Easy", "Definition of matrix"),
        ("What property must a square matrix possess to be classified as symmetric?", "Matrix equals its own transpose", "Easy", "Types of matrices"),
        ("Explain the procedure to compute entry in row i, column j of product AB.", "Dot product of row i with column j", "Medium", "Algebra of matrices"),
        ("Under what specific criteria is a matrix termed diagonal?", "All off-diagonal entries are strictly zero", "Easy", "Types of matrices"),
        ("For addition A plus B to be valid, what dimensional requirement must hold?", "Both matrices must share identical row and column dimensions", "Easy", "Algebra of matrices"),
        ("What is the defining characteristic of an identity matrix?", "Square matrix with ones along main diagonal and zeros elsewhere", "Easy", "Types of matrices"),
        ("How does scalar multiplication k times A alter individual elements?", "Every component is multiplied by scalar k", "Medium", "Algebra of matrices"),
        ("State the mathematical relationship for a skew-symmetric matrix.", "Transpose equals negative of original matrix", "Medium", "Types of matrices"),
        ("Which algebraic rule establishes parenthesization for triple matrix products?", "Associative property of matrix multiplication", "Hard", "Algebra of matrices"),
        ("Describe the structural format of an upper triangular matrix.", "All entries located below the main diagonal are zero", "Easy", "Types of matrices"),
        ("What role does the zero matrix serve in additive matrix operations?", "Acts as the unique additive identity element", "Medium", "Algebra of matrices"),
        ("Under what circumstances can matrix subtraction be performed between matrices?", "Both operands must possess identical dimensions", "Medium", "Algebra of matrices")
    ]
    questions = []
    for i in range(min(count, len(templates))):
        q_text, ans, diff, concept = templates[i]
        questions.append({
            "question": q_text,
            "options": {
                "A": ans,
                "B": f"Incorrect option B{i} violating {concept}",
                "C": f"Erroneous choice C{i} for {diff} level",
                "D": f"Unrelated distractor D{i}"
            },
            "correct_answer": "A",
            "difficulty": diff,
            "concept": concept,
            "explanation": f"Step-by-step mathematical reasoning proves: {ans}.",
            "option_explanations": {
                "A": f"Correct: {ans} satisfies the mathematical theorem.",
                "B": f"Incorrect: Choice B{i} violates {concept}.",
                "C": f"Incorrect: Choice C{i} is mathematically invalid.",
                "D": f"Incorrect: Choice D{i} has no theoretical foundation."
            }
        })
    return questions


# ── 1. Successful AI response produces the quiz ──────────────────────────────
def test_successful_groq_produces_fresh_quiz():
    """Verify that a successful Groq response generates and returns a fresh quiz."""
    mock_questions = _make_mock_ai_questions(10, "Linear Algebra Concepts")
    mock_json = json.dumps({"questions": mock_questions})

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.return_value = mock_json

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        quiz = generate_quiz(
            course_code="22MT103",
            course_name="Linear Algebra",
            topic_name="Linear Algebra Concepts",
            syllabus_context="Matrix algebra, types of matrices, definition",
            num_questions=10
        )

    assert quiz is not None
    assert len(quiz["questions"]) == 10
    assert quiz.get("is_fallback") is not True
    assert quiz.get("provider_used") == "groq"
    assert "Define the condition" in quiz["questions"][0]["question"]


# ── 2. Groq failure triggers NVIDIA fallback ─────────────────────────────────
def test_groq_failure_triggers_nvidia_fallback():
    """Verify that if Groq fails, NVIDIA is called in the fallback chain and succeeds."""
    mock_questions = _make_mock_ai_questions(10, "Linear Algebra Concepts")
    mock_json = json.dumps({"questions": mock_questions})

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = Exception("Groq 429 Rate Limit Exceeded")

    mock_nvidia = MagicMock()
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = mock_json

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        quiz = generate_quiz(
            course_code="22MT103",
            course_name="Linear Algebra",
            topic_name="Linear Algebra Concepts",
            syllabus_context="Matrix algebra, types of matrices, definition",
            num_questions=10
        )

    assert quiz is not None
    assert quiz.get("provider_used") == "nvidia"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.called
    assert mock_nvidia.generate_quiz_raw.called


# ── 3. Groq and NVIDIA failure triggers Gemini fallback ──────────────────────
def test_groq_and_nvidia_failure_triggers_gemini_fallback():
    """Verify that if both Groq and NVIDIA fail, Gemini is called and succeeds."""
    mock_questions = _make_mock_ai_questions(10, "Linear Algebra Concepts")
    mock_json = json.dumps({"questions": mock_questions})

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = Exception("Groq 503 Service Unavailable")

    mock_nvidia = MagicMock()
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = Exception("NVIDIA 504 Gateway Timeout")

    mock_gemini = MagicMock()
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.return_value = mock_json

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia, mock_gemini]):
        quiz = generate_quiz(
            course_code="22MT103",
            course_name="Linear Algebra",
            topic_name="Linear Algebra Concepts",
            syllabus_context="Matrix algebra, types of matrices, definition",
            num_questions=10
        )

    assert quiz is not None
    assert quiz.get("provider_used") == "gemini"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.called
    assert mock_nvidia.generate_quiz_raw.called
    assert mock_gemini.generate_quiz_raw.called


# ── 4. All providers failing produces controlled unavailable response ───────
def test_all_providers_failing_raises_controlled_service_unavailable():
    """Verify that when all providers fail, a controlled AIServiceUnavailableError is raised."""
    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = Exception("Groq 503 down")

    mock_nvidia = MagicMock()
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = Exception("NVIDIA 503 down")

    mock_gemini = MagicMock()
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.side_effect = Exception("Gemini 503 down")

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia, mock_gemini]):
        with pytest.raises(AIError) as exc_info:
            generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Linear Algebra Concepts",
                syllabus_context="Syllabus context",
                num_questions=10
            )

    err = exc_info.value
    # Must be controlled error with status 503 or 500, never a crash or question payload
    assert err.status_code in (500, 502, 503, 504)
    assert not isinstance(err, dict)


# ── 5. Zero curated questions returned after provider failure ─────────────────
def test_no_curated_or_stored_questions_ever_returned_on_failure():
    """Proves that _load_local_demo_questions returns None and no static questions are substituted."""
    assert _load_local_demo_questions() is None

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = Exception("Groq temporary outage")

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        with pytest.raises(AIError):
            generate_quiz(
                course_code="22AM803",
                course_name="Computer Vision",
                topic_name="Introduction and Goals of Computer Vision",
                syllabus_context="Computer vision syllabus",
                num_questions=10
            )


# ── 6. API endpoint returns controlled error response, not 200 with fallback ─
def test_api_endpoint_returns_503_when_providers_fail(client):
    """Verify POST /api/quiz/generate returns HTTP 503/500 with clear message when providers fail."""
    student_id = "test_no_fallback_stud"
    token = sign_student_identity(student_id)

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = Exception("Upstream connection reset")

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        resp = client.post(
            "/api/quiz/generate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "student_id": student_id,
                "course_code": "22MT103",
                "topic": "Linear Algebra Concepts",
                "practice_mode": "general_syllabus"
            }
        )

    # API must return an error status code, NOT 200 OK
    assert resp.status_code in (500, 502, 503, 504)
    data = resp.json()
    assert "questions" not in data
    assert "detail" in data
    assert "AI provider" in data["detail"] or "unavailable" in data["detail"].lower() or "error" in data["detail"].lower()


# ── 7. Submission and scoring behavior remains fully intact ───────────────────
def test_quiz_submission_and_scoring_remains_intact(client):
    """Verify that answer submission and scoring work completely normally."""
    student_id = "test_submit_scoring_stud"
    token = sign_student_identity(student_id)

    # Generate a quiz via mocked provider
    mock_questions = _make_mock_ai_questions(5, "Linear Algebra Concepts")
    mock_json = json.dumps({"questions": mock_questions})

    mock_groq = MagicMock()
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.return_value = mock_json

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        gen_resp = client.post(
            "/api/quiz/generate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "student_id": student_id,
                "course_code": "22MT103",
                "topic": "Linear Algebra Concepts",
                "practice_mode": "general_syllabus",
                "num_questions": 5
            }
        )
    assert gen_resp.status_code == 200, f"Expected 200 OK, got {gen_resp.status_code}: {gen_resp.text}"
    quiz_data = gen_resp.json()
    quiz_id = quiz_data["quiz_id"]

    # Submit answers (all "A" -> 100% correct)
    sub_resp = client.post(
        "/api/quiz/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "quiz_id": quiz_id,
            "student_id": student_id,
            "answers": ["A", "A", "A", "A", "A"]
        }
    )
    assert sub_resp.status_code == 200
    result = sub_resp.json()
    assert result["score"] == 5
    assert result["total"] == 5
    assert result["percentage"] == 100.0
    assert "details" in result
    assert len(result["details"]) == 5
